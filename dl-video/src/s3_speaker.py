# 参数调用示例:
#   python src/s3_speaker.py <audio_file> <srt_file>
#   python src/s3_speaker.py <audio_file> <srt_file> --token hf_xxx
#   python src/s3_speaker.py <audio_file> <srt_file> --output <output.srt>
"""Step3-附加: 说话人识别 — 为字幕条目标注说话人

用法:
  # 基本用法（输出到原 SRT 同目录，文件名追加 .speaker）
  python src/s3_speaker.py output/demo.mp3 output/demo.mlx_turbo.srt

  # 指定输出路径
  python src/s3_speaker.py output/demo.mp3 output/demo.mlx_turbo.srt --output output/demo_speaker.srt

  # 指定 HuggingFace token（也可用环境变量 HF_TOKEN）
  python src/s3_speaker.py output/demo.mp3 output/demo.mlx_turbo.srt --token hf_xxx

  # 自定义无匹配说话人标签
  python src/s3_speaker.py output/demo.mp3 output/demo.mlx_turbo.srt --unknown "未知"

参数:
  audio_file         输入音频路径（如 output/demo.mp3）
  srt_file           输入 SRT 路径（如 output/demo.mlx_turbo.srt）
  --backend          说话人识别后端: pyannote（目前仅支持此选项）
  --model NAME       pyannote 模型名（默认: pyannote/speaker-diarization-3.1）
  --token TOKEN      HuggingFace token；也可使用环境变量 HF_TOKEN 或 HUGGINGFACE_TOKEN
  --output PATH      输出 SRT 路径；默认在原 SRT 文件名后追加 .speaker
  --unknown LABEL    无匹配说话人时使用的标签（默认: SPEAKER_??）
"""

import argparse
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
_input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
LOG_DIR.mkdir(exist_ok=True)
MPLCONFIG_DIR = LOG_DIR / "matplotlib"
MPLCONFIG_DIR.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPLCONFIG_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "speaker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


DEFAULT_PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"


@dataclass
class SrtEntry:
    index: int
    start: float
    end: float
    text: str


@dataclass
class SpeakerSegment:
    start: float
    end: float
    speaker: str


def parse_srt_time(ts: str) -> float:
    """Parse SRT timestamp 'HH:MM:SS,mmm' to seconds."""
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", ts.strip())
    if not m:
        raise ValueError(f"无效 SRT 时间戳: {ts}")
    h, mi, s, ms = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    return h * 3600 + mi * 60 + s + ms / 1000.0


def format_srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp 'HH:MM:SS,mmm'."""
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    secs = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_srt(srt_path: Path) -> list[SrtEntry]:
    """Read SRT entries, preserving multiline subtitle text."""
    text = srt_path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []

    entries: list[SrtEntry] = []
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines()]
        if len(lines) < 3:
            continue
        if not lines[0].strip().isdigit():
            continue
        ts_match = re.match(r"(.+?)\s*-->\s*(.+?)(?:\s|$)", lines[1])
        if not ts_match:
            continue
        entries.append(
            SrtEntry(
                index=int(lines[0].strip()),
                start=parse_srt_time(ts_match.group(1)),
                end=parse_srt_time(ts_match.group(2)),
                text="\n".join(line.strip() for line in lines[2:]).strip(),
            )
        )
    return entries


def strip_existing_speaker_label(text: str) -> str:
    """Avoid stacking labels when re-running on an already labeled SRT."""
    return re.sub(r"^\[[^\]]+\]\s*", "", text.strip())


def output_path_for(srt_path: Path) -> Path:
    return srt_path.with_name(f"{srt_path.stem}.speaker{srt_path.suffix}")


def overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def choose_speaker(entry: SrtEntry, segments: list[SpeakerSegment], unknown: str) -> str:
    scores: dict[str, float] = {}
    for segment in segments:
        overlap = overlap_seconds(entry.start, entry.end, segment.start, segment.end)
        if overlap > 0:
            scores[segment.speaker] = scores.get(segment.speaker, 0.0) + overlap
    if not scores:
        return unknown
    return max(scores.items(), key=lambda item: item[1])[0]


def write_speaker_srt(entries: list[SrtEntry], segments: list[SpeakerSegment],
                      output_path: Path, unknown: str):
    with open(output_path, "w", encoding="utf-8") as f:
        for idx, entry in enumerate(entries, start=1):
            speaker = choose_speaker(entry, segments, unknown)
            text = strip_existing_speaker_label(entry.text)
            f.write(f"{idx}\n")
            f.write(f"{format_srt_time(entry.start)} --> {format_srt_time(entry.end)}\n")
            f.write(f"[{speaker}] {text}\n\n")


def load_pyannote_pipeline(model: str, token: str):
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        log.error("未安装 pyannote.audio，请先运行: pip install pyannote.audio")
        return None

    try:
        pipeline = Pipeline.from_pretrained(model, use_auth_token=token)
    except TypeError:
        try:
            pipeline = Pipeline.from_pretrained(model, token=token)
        except Exception as e:
            log.error(f"加载 pyannote 模型失败: {e}")
            log.error("请确认 Hugging Face token 有权限访问 pyannote 模型，并已接受模型授权条款。")
            return None
    except Exception as e:
        log.error(f"加载 pyannote 模型失败: {e}")
        log.error("请确认 Hugging Face token 有权限访问 pyannote 模型，并已接受模型授权条款。")
        return None
    if pipeline is None:
        log.error("加载 pyannote 模型失败: Pipeline.from_pretrained 返回 None")
        log.error("请确认 Hugging Face token 有权限访问 pyannote 模型，并已接受模型授权条款。")
        return None
    return pipeline


def run_pyannote(audio_path: Path, model: str, token: str) -> list[SpeakerSegment] | None:
    pipeline = load_pyannote_pipeline(model, token)
    if pipeline is None:
        return None

    log.info(f"加载 pyannote 模型: {model}")
    log.info(f"开始说话人分离: {audio_path}")
    try:
        output = pipeline(str(audio_path))
    except Exception as e:
        log.error(f"pyannote 说话人分离失败: {e}")
        return None

    diarization = (
        getattr(output, "exclusive_speaker_diarization", None)
        or getattr(output, "speaker_diarization", None)
        or output
    )
    segments: list[SpeakerSegment] = []
    if hasattr(diarization, "itertracks"):
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(SpeakerSegment(float(turn.start), float(turn.end), str(speaker)))
    else:
        for item in diarization:
            if len(item) != 2:
                continue
            turn, speaker = item
            segments.append(SpeakerSegment(float(turn.start), float(turn.end), str(speaker)))

    segments.sort(key=lambda item: (item.start, item.end, item.speaker))
    return segments


def resolve_token(cli_token: str | None) -> str | None:
    return cli_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")


def main():
    parser = argparse.ArgumentParser(description="为已有 SRT 增加 pyannote 说话人标签")
    parser.add_argument("audio_file", help="输入音频路径，例如 output/demo.mp3")
    parser.add_argument("srt_file", help="输入 SRT 路径，例如 output/demo.mlx_turbo.srt")
    parser.add_argument("--backend", default="pyannote", choices=["pyannote"],
                        help="多人识别后端，目前支持 pyannote")
    parser.add_argument("--model", default=DEFAULT_PYANNOTE_MODEL,
                        help=f"pyannote 模型名，默认: {DEFAULT_PYANNOTE_MODEL}")
    parser.add_argument("--token", default=None,
                        help="Hugging Face token；也可使用环境变量 HF_TOKEN 或 HUGGINGFACE_TOKEN")
    parser.add_argument("--output", default=None,
                        help="输出 SRT 路径；默认在原 SRT 文件名后追加 .speaker")
    parser.add_argument("--unknown", default="SPEAKER_??",
                        help="无匹配说话人时使用的标签，默认: SPEAKER_??")
    args = parser.parse_args()

    audio_path = Path(args.audio_file)
    srt_path = Path(args.srt_file)
    output_path = Path(args.output) if args.output else output_path_for(srt_path)

    if not audio_path.exists():
        log.error(f"音频文件不存在: {audio_path}")
        return
    if not srt_path.exists():
        log.error(f"SRT 文件不存在: {srt_path}")
        return

    token = resolve_token(args.token)
    if not token:
        log.error("pyannote 需要 Hugging Face token。请设置 HF_TOKEN，或使用 --token。")
        return

    entries = parse_srt(srt_path)
    if not entries:
        log.error(f"未解析到字幕条目: {srt_path}")
        return
    log.info(f"读取字幕: {srt_path} ({len(entries)} 条)")

    segments = run_pyannote(audio_path, args.model, token)
    if segments is None:
        return
    if not segments:
        log.error("pyannote 未返回任何说话人分段")
        return

    speakers = sorted({segment.speaker for segment in segments})
    log.info(f"识别到说话人: {', '.join(speakers)}")
    write_speaker_srt(entries, segments, output_path, args.unknown)
    log.info(f"说话人字幕生成成功: {output_path} ({len(entries)} 条)")


if __name__ == "__main__":
    main()
