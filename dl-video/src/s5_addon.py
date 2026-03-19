"""Step5: 生成 addon — 提取字幕中有但教学文档中没有的补充内容"""

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
        logging.FileHandler(LOG_DIR / "s5_addon.log", encoding="utf-8"),
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


def find_fix_subtitle(output_dir, base_name):
    """查找修订后的字幕，优先 _ori_fix.srt，其次 _wm_fix.srt，
    再 fallback 到 _ori.srt / _wm.srt"""
    for suffix in ("_ori_fix.srt", "_wm_fix.srt", "_ori.srt", "_wm.srt"):
        p = output_dir / f"{base_name}{suffix}"
        if p.exists():
            return p
    return None


def find_wiki_doc(output_dir, base_name):
    wiki = output_dir / f"{base_name}_wiki.md"
    return wiki if wiki.exists() else None


def run_addon(srt_path, wiki_path, output_dir, base_name, config, creds):
    """调用 yitang 的 addon 对比逻辑"""
    from yitang_addon import (
        parse_srt, srt_to_text, parse_transcript,
        LLMClient, load_prompt, parse_llm_json,
        chunk_srt_text, merge_results, render_full_report,
    )

    # 解析字幕
    srt_entries = parse_srt(str(srt_path))
    log.info(f"字幕: {len(srt_entries)} 条")

    # 解析教学文档为章节
    if wiki_path:
        sections = parse_transcript(str(wiki_path))
        log.info(f"教学文档: {len(sections)} 个章节")
    else:
        log.warning("无教学文档，addon 分析可能不完整")
        sections = [("(全文)", "")]

    # 初始化 LLM
    llm = LLMClient(config, creds)

    # 加载提示词（从 yitang 的 cfg 目录）
    yitang_cfg = Path(PROJECT_DIR / config.get("yitang_dir", "../yitang")) / "cfg"
    addon_config = {}
    addon_cfg_path = yitang_cfg / "config-addon.yaml"
    if addon_cfg_path.exists():
        with open(addon_cfg_path, encoding="utf-8") as f:
            addon_config = yaml.safe_load(f) or {}

    analysis_cfg = addon_config.get("analysis", {})
    prompt_file = analysis_cfg.get("prompt_subtitle", "prompt-subtitle.md")
    prompt_path = yitang_cfg / prompt_file
    if not prompt_path.exists():
        log.error(f"提示词文件不存在: {prompt_path}")
        return None
    system_prompt = prompt_path.read_text(encoding="utf-8").strip()

    # 字幕对比分析
    chunk_size = analysis_cfg.get("chunk_size", 30000)
    transcript_text = "\n\n".join(
        f"## {title}\n{content}" for title, content in sections
    )
    chunks = chunk_srt_text(srt_entries, chunk_size)
    log.info(f"字幕分为 {len(chunks)} 段")

    subtitle_items = []
    for idx, (start, end, chunk_text) in enumerate(chunks):
        log.info(f"  分析字幕段 {idx+1}/{len(chunks)}: {start} ~ {end}")
        user_prompt = (
            f"## 逐字稿内容\n\n{transcript_text}\n\n"
            f"---\n\n"
            f"## 字幕原文（{start} ~ {end}）\n\n{chunk_text}"
        )
        try:
            reply = llm.chat(system_prompt, user_prompt)
            items = parse_llm_json(reply)
            subtitle_items.extend(items)
            log.info(f"    提取 {len(items)} 条信息")
        except Exception as e:
            log.error(f"    字幕段 {idx+1} 处理失败: {e}")

    log.info(f"字幕对比完成，共提取 {subtitle_items.__len__()} 条")

    # 合并与渲染
    if subtitle_items:
        merged, stats = merge_results(subtitle_items, [], sections)
        report_path = output_dir / f"{base_name}_addon.md"
        render_full_report(merged, stats, sections, base_name, report_path)
        log.info(f"addon 输出: {report_path}")
    else:
        log.info("未提取到补充信息")

    llm.report_usage()
    return output_dir / f"{base_name}_addon.md"


def main():
    import os
    config, creds = load_config()
    setup_yitang_path(config)

    # 检查是否有环境变量传入的文件
    input_file = os.environ.get("DL_VIDEO_INPUT_FILE", "")
    input_type = os.environ.get("DL_VIDEO_INPUT_TYPE", "")
    fix_srt_input = input_file if input_type == "fix_srt" else ""

    # 确定输出目录
    if fix_srt_input:
        fix_path_provided = Path(fix_srt_input)
        output_dir = fix_path_provided.parent
        log.info(f"_fix.srt 输入模式，输出目录: {output_dir}")
        fix_files = [fix_path_provided]
    else:
        output_dir = PROJECT_DIR / config.get("output_dir", "output")
        output_dir.mkdir(exist_ok=True)
        # 扫描 output 目录中的 _ori_fix.srt 和 _wm_fix.srt 文件
        fix_files = sorted(output_dir.glob("*_ori_fix.srt")) + sorted(output_dir.glob("*_wm_fix.srt"))
        if not fix_files:
            log.warning(f"未找到修订字幕文件: {output_dir}")
            return

    for i, srt_path in enumerate(fix_files):
        log.info(f"=== 任务 {i+1}/{len(fix_files)} ===")

        if not srt_path.exists():
            log.warning(f"_fix.srt 文件不存在: {srt_path}")
            continue

        # 提取 base_name（去掉 _ori_fix 或 _wm_fix 后缀）
        base_name = srt_path.stem
        for suffix in ["_ori_fix", "_wm_fix"]:
            if base_name.endswith(suffix):
                base_name = base_name[:-len(suffix)]
                break

        # 检查是否已有 addon
        addon_path = output_dir / f"{base_name}_addon.md"
        if addon_path.exists():
            log.info(f"addon 已存在，跳过: {addon_path}")
            continue

        wiki_path = find_wiki_doc(output_dir, base_name)
        log.info(f"字幕: {srt_path.name}, 教学文档: "
                 f"{wiki_path.name if wiki_path else '无'}")

        run_addon(srt_path, wiki_path, output_dir, base_name, config, creds)

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
