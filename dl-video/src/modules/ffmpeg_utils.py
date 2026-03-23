"""ffmpeg 公共工具 — 查找、音频提取、HLS 下载、TS→MP4 转封装"""

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件路径"""
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in [r"D:\tools\ffmpeg\bin\ffmpeg.exe",
                      r"C:\tools\ffmpeg\bin\ffmpeg.exe"]:
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"


FFMPEG = find_ffmpeg()


def _get_duration(file_path: Path) -> float:
    """用 ffprobe 获取媒体文件时长（秒），失败返回 0"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        ffprobe_dir = Path(FFMPEG).parent
        candidate = ffprobe_dir / "ffprobe.exe"
        ffprobe = str(candidate) if candidate.exists() else "ffprobe"
    try:
        r = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        return 0


def extract_audio(video_path: Path, audio_path: Path,
                  err_detect: bool = True) -> bool:
    """从视频提取音频为 MP3（-vn -acodec libmp3lame -q:a 2）

    使用临时文件写入，ffmpeg 成功后再重命名，避免中断后留下不完整的 mp3。
    """
    if audio_path.exists() and audio_path.stat().st_size > 1024:
        log.info(f"音频已存在，跳过: {audio_path}")
        return True
    if not video_path.exists():
        log.warning(f"视频不存在，无法提取音频: {video_path}")
        return False

    duration = _get_duration(video_path)

    # 写入临时文件，成功后再 rename
    tmp_path = audio_path.with_suffix(".tmp.mp3")
    log.info(f"提取音频: {audio_path.name}"
             + (f" (时长 {int(duration // 60)}:{int(duration % 60):02d})" if duration else ""))
    cmd = [FFMPEG]
    if err_detect:
        cmd += ["-err_detect", "ignore_err"]
    cmd += [
        "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        "-progress", "pipe:1",
        str(tmp_path), "-y",
    ]
    stderr_tmp = tmp_path.with_suffix(".stderr")
    try:
        stderr_f = open(stderr_tmp, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_f,
                                text=True, encoding="utf-8", errors="replace")
        last_min = -1
        for line in proc.stdout:
            m = re.match(r"out_time_ms=(\d+)", line.strip())
            if m and duration > 0:
                cur_sec = int(m.group(1)) / 1_000_000
                cur_min = int(cur_sec // 60)
                if cur_min > last_min:
                    last_min = cur_min
                    pct = min(int(cur_sec / duration * 100), 99)
                    print(f"\r  提取音频: {pct}% ({int(cur_sec // 60)}:{int(cur_sec % 60):02d}"
                          f" / {int(duration // 60)}:{int(duration % 60):02d})", end="", flush=True)
        proc.wait()
        stderr_f.close()
        if duration > 0:
            print(f"\r  提取音频: 100% ({int(duration // 60)}:{int(duration % 60):02d}"
                  f" / {int(duration // 60)}:{int(duration % 60):02d})")
    except FileNotFoundError:
        log.warning("ffmpeg 未安装或不在 PATH 中")
        return False
    if proc.returncode != 0:
        stderr_text = stderr_tmp.read_text(encoding="utf-8", errors="replace") if stderr_tmp.exists() else ""
        log.error(f"ffmpeg 提取音频失败: {stderr_text[-500:]}")
        tmp_path.unlink(missing_ok=True)
        stderr_tmp.unlink(missing_ok=True)
        return False
    stderr_tmp.unlink(missing_ok=True)

    # 成功后原子替换
    tmp_path.replace(audio_path)
    size_mb = audio_path.stat().st_size / 1024 / 1024
    log.info(f"音频提取完成: {audio_path} ({size_mb:.1f} MB)")
    return True


def download_hls(m3u8_url: str, output_path: Path,
                 referer: str = None, headers: str = None) -> bool:
    """ffmpeg 下载 HLS 视频流（-c copy -bsf:a aac_adtstoasc）

    使用临时文件写入，成功后再重命名，避免中断后留下不完整的视频。
    """
    if output_path.exists() and output_path.stat().st_size > 1024:
        log.info(f"视频已存在，跳过: {output_path}")
        return True

    tmp_path = output_path.with_stem(output_path.stem + ".tmp")
    log.info(f"ffmpeg 下载 HLS: {output_path.name}")
    cmd = [FFMPEG]
    if headers:
        cmd += ["-headers", headers]
    elif referer:
        cmd += ["-headers",
                f"Referer: {referer}\r\nOrigin: {referer.rstrip('/')}\r\n"]
    cmd += [
        "-i", m3u8_url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        str(tmp_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=1800)
    except FileNotFoundError:
        log.error("ffmpeg 未安装或不在 PATH 中")
        return False
    except subprocess.TimeoutExpired:
        log.error("ffmpeg 下载超时（30分钟）")
        tmp_path.unlink(missing_ok=True)
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg HLS 下载失败: {result.stderr[-500:]}")
        tmp_path.unlink(missing_ok=True)
        return False

    tmp_path.replace(output_path)
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"HLS 下载完成: {output_path} ({size_mb:.1f} MB)")
    return True


def remux_ts_to_mp4(ts_path: Path, mp4_path: Path) -> bool:
    """ffmpeg 将 TS 转封装为 MP4，使用临时文件保护"""
    if mp4_path.exists() and mp4_path.stat().st_size > 1024:
        log.info(f"MP4 已存在，跳过: {mp4_path}")
        return True

    tmp_path = mp4_path.with_suffix(".tmp.mp4")
    cmd = [
        FFMPEG, "-i", str(ts_path),
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        str(tmp_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=300)
    except FileNotFoundError:
        log.error("ffmpeg 未安装或不在 PATH 中")
        return False
    if result.returncode != 0:
        log.error(f"remux 失败: {result.stderr[-500:]}")
        tmp_path.unlink(missing_ok=True)
        return False

    tmp_path.replace(mp4_path)
    size_mb = mp4_path.stat().st_size / 1024 / 1024
    log.info(f"MP4 转封装完成: {mp4_path} ({size_mb:.1f} MB)")
    return True


def concat_ts(concat_list: Path, output_path: Path,
              timeout: int = 600) -> bool:
    """ffmpeg concat 合并 TS 分片为视频，使用临时文件保护"""
    if output_path.exists() and output_path.stat().st_size > 1024:
        log.info(f"合并文件已存在，跳过: {output_path}")
        return True

    tmp_path = output_path.with_stem(output_path.stem + ".tmp")
    cmd = [
        FFMPEG, "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy", str(tmp_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout)
    except FileNotFoundError:
        log.error("ffmpeg 未安装或不在 PATH 中")
        return False
    except subprocess.TimeoutExpired:
        log.error(f"ffmpeg 合并超时（{timeout}秒）")
        tmp_path.unlink(missing_ok=True)
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg 合并失败: {result.stderr[-500:]}")
        tmp_path.unlink(missing_ok=True)
        return False

    tmp_path.replace(output_path)
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"TS 合并完成: {output_path} ({size_mb:.1f} MB)")
    return True


def mp3_to_wav(audio_path: Path, wav_path: Path = None,
               sample_rate: int = 16000) -> Path:
    """MP3 转 16kHz 单声道 WAV（Whisper 预处理用）

    使用临时文件写入，成功后再重命名，避免中断后留下不完整的 WAV。
    优先用 PyAV，不可用时回退 ffmpeg。
    """
    if wav_path is None:
        wav_path = audio_path.with_suffix(".16k.wav")
    if wav_path.exists() and wav_path.stat().st_size > 1024:
        log.info(f"预处理音频已存在，跳过: {wav_path}")
        return wav_path

    tmp_path = wav_path.with_suffix(".tmp.wav")

    try:
        import av
        import wave

        log.info("预处理音频为 16kHz WAV（使用 PyAV）...")
        container = av.open(str(audio_path))
        resampler = av.AudioResampler(format="s16", layout="mono",
                                      rate=sample_rate)
        with wave.open(str(tmp_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for frame in container.decode(audio=0):
                resampled = resampler.resample(frame)
                for r in resampled:
                    wf.writeframes(r.to_ndarray().tobytes())
        container.close()

    except ImportError:
        cmd = [
            FFMPEG, "-y", "-i", str(audio_path),
            "-ar", str(sample_rate), "-ac", "1",
            str(tmp_path),
        ]
        log.info("预处理音频为 16kHz WAV（使用 ffmpeg）...")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg 转换失败: {result.stderr.decode(errors='replace')}")

    tmp_path.replace(wav_path)
    log.info(f"预处理完成: {wav_path} "
             f"({wav_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return wav_path


def mp3_to_pcm(audio_path: Path, pcm_path: Path = None,
               sample_rate: int = 16000) -> Path:
    """MP3 转 16kHz 单声道 PCM（Sherpa 预处理用）

    使用临时文件写入，成功后再重命名，避免中断后留下不完整的 PCM。
    优先用 PyAV，不可用时回退 ffmpeg。
    """
    if pcm_path is None:
        pcm_path = audio_path.with_suffix(".pcm")
    if pcm_path.exists() and pcm_path.stat().st_size > 1024:
        log.info(f"PCM 文件已存在，跳过转换: {pcm_path}")
        return pcm_path

    tmp_path = pcm_path.with_suffix(".tmp.pcm")

    try:
        import av

        log.info(f"转换音频为 PCM（使用 PyAV）: {audio_path}")
        container = av.open(str(audio_path))
        resampler = av.AudioResampler(format="s16", layout="mono",
                                      rate=sample_rate)
        with open(tmp_path, "wb") as f:
            for frame in container.decode(audio=0):
                resampled = resampler.resample(frame)
                for r in resampled:
                    f.write(r.to_ndarray().tobytes())
        container.close()

    except ImportError:
        cmd = [
            FFMPEG, "-y", "-i", str(audio_path),
            "-ar", str(sample_rate), "-ac", "1", "-f", "s16le",
            str(tmp_path),
        ]
        log.info(f"转换音频为 PCM（使用 ffmpeg）: {audio_path}")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg 转换失败: {result.stderr.decode(errors='replace')}")

    tmp_path.replace(pcm_path)
    log.info(f"PCM 转换完成: {pcm_path} "
             f"({pcm_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return pcm_path
