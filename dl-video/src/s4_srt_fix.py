"""Step4: 字幕修订 — LLM 对比字幕与教学文档，修订字幕"""

import logging
import re
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
OUTPUT_DIR = PROJECT_DIR / "output"
LOG_DIR = PROJECT_DIR / "log-err"
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "s4_srt_fix.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')


def load_config():
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(CFG_DIR / "credentials.yaml", encoding="utf-8") as f:
        creds = yaml.safe_load(f)
    return config, creds


def safe_filename(title: str) -> str:
    return ILLEGAL_CHARS.sub("_", title).strip()


def setup_yitang_path(config):
    yitang_dir = Path(PROJECT_DIR / config.get("yitang_dir", "../yitang"))
    yitang_src = yitang_dir / "src"
    if str(yitang_src) not in sys.path:
        sys.path.insert(0, str(yitang_src))
    return yitang_dir


def find_subtitle(output_dir, base_name):
    """查找字幕文件，优先 _ori.srt，其次 _wm.srt"""
    ori = output_dir / f"{base_name}_ori.srt"
    if ori.exists():
        return ori
    wm = output_dir / f"{base_name}_wm.srt"
    if wm.exists():
        return wm
    return None


def find_wiki_doc(output_dir, base_name):
    """查找教学文档"""
    wiki = output_dir / f"{base_name}_wiki.md"
    if wiki.exists():
        return wiki
    return None


def run_srt_fix(srt_path, wiki_path, output_dir, config, creds):
    """调用 yitang 的字幕修订逻辑"""
    from yitang_srt_fix import (
        parse_srt, write_srt, load_custom_dict,
        extract_terms_from_transcript, run_llm_fix, apply_llm_fixes,
        write_changelog,
    )

    # 解析字幕
    entries = parse_srt(str(srt_path))
    log.info(f"字幕: {len(entries)} 条")

    # 加载教学文档作为参考
    transcript_text = ""
    if wiki_path:
        transcript_text = wiki_path.read_text(encoding="utf-8")
        log.info(f"教学文档: {len(transcript_text)} 字符")

    # 提取专有名词
    terms = extract_terms_from_transcript(transcript_text) if transcript_text else []

    # 构造 srtfix 兼容的 config 结构
    fix_config = {
        "llm": config.get("llm", {}),
        "fix": {
            "prompt": "prompt-srtfix-ref.md" if transcript_text else "prompt-srtfix-noref.md",
            "prompt_noref": "prompt-srtfix-noref.md",
            "chunk_size": 80,
            "custom_dict": config.get("srt_fix", {}).get("custom_dict", ""),
        },
        "output": {"dir": str(output_dir), "suffix": "_fix"},
    }

    # 加载自定义词典（从 yitang 的 cfg 目录）
    custom_dict = load_custom_dict(fix_config)

    # 词典替换
    from yitang_srt_fix import apply_dict_fixes
    entries, dict_changelog = apply_dict_fixes(entries, custom_dict)

    # LLM 修订
    llm_fixes, llm_client = run_llm_fix(
        entries, transcript_text, terms, custom_dict, fix_config, creds
    )
    entries = apply_llm_fixes(entries, llm_fixes)

    # 输出
    stem = srt_path.stem
    out_srt = output_dir / f"{stem}_fix.srt"
    out_log = output_dir / f"{stem}_fix_changelog.md"
    write_srt(entries, str(out_srt))
    write_changelog(dict_changelog, llm_fixes, str(out_log))

    total = len(dict_changelog) + len([f for f in llm_fixes if f.get("fixed")])
    log.info(f"修订完成: {total} 处修正, 输出: {out_srt}")
    llm_client.report_usage()
    return out_srt


def main():
    import os
    config, creds = load_config()
    setup_yitang_path(config)

    # 检查是否有环境变量传入的文件
    input_file = os.environ.get("DL_VIDEO_INPUT_FILE", "")
    input_type = os.environ.get("DL_VIDEO_INPUT_TYPE", "")
    srt_input = input_file if input_type == "srt" else ""

    # 确定输出目录
    if srt_input:
        srt_path_provided = Path(srt_input)
        output_dir = srt_path_provided.parent
        log.info(f"SRT 输入模式，输出目录: {output_dir}")
        srt_files = [srt_path_provided]
    else:
        output_dir = PROJECT_DIR / config.get("output_dir", "output")
        output_dir.mkdir(exist_ok=True)
        # 扫描 output 目录中的 _ori.srt 和 _wm.srt 文件
        srt_files = sorted(output_dir.glob("*_ori.srt")) + sorted(output_dir.glob("*_wm.srt"))
        if not srt_files:
            log.warning(f"未找到字幕文件: {output_dir}")
            return

    for i, srt_path in enumerate(srt_files):
        log.info(f"=== 任务 {i+1}/{len(srt_files)} ===")

        if not srt_path.exists():
            log.warning(f"SRT 文件不存在: {srt_path}")
            continue

        # 检查是否已有修订版
        fix_path = output_dir / f"{srt_path.stem}_fix.srt"
        if fix_path.exists():
            log.info(f"修订字幕已存在，跳过: {fix_path}")
            continue

        # 提取 base_name（去掉 _ori 或 _wm 后缀）
        base_name = srt_path.stem
        for suffix in ["_ori", "_wm"]:
            if base_name.endswith(suffix):
                base_name = base_name[:-len(suffix)]
                break

        wiki_path = find_wiki_doc(output_dir, base_name)
        log.info(f"字幕: {srt_path.name}, 教学文档: "
                 f"{wiki_path.name if wiki_path else '无'}")

        run_srt_fix(srt_path, wiki_path, output_dir, config, creds)

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
