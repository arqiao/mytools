# 参数调用示例:
#   python src/s2_wiki.py
#   python src/s2_wiki.py <source_wiki_url> [target_wiki_url]
#   python src/s2_wiki.py <source_wiki_url> [target_wiki_url] --title <output_title>
#   python src/s2_wiki.py <source_wiki_url> --attachments-only
#   python src/s2_wiki.py <source_wiki_url> [target_wiki_url] --no-attachments
"""Step2: 下载教学文档 + 写入飞书 wiki

用法:
  # 从 cfg/input.yaml 的 tasks 中读取 source_wiki_url 和 target_wiki_url
  python src/s2_wiki.py

  # 直接传入源 wiki URL 和可选目标飞书 wiki URL
  python src/s2_wiki.py <source_wiki_url> [target_wiki_url]

  # 只下载附件（视频、音频、PDF 等），不下载全文
  python src/s2_wiki.py <source_wiki_url> --attachments-only

  # 一堂文稿不下载附件
  python src/s2_wiki.py <source_wiki_url> [target_wiki_url] --no-attachments

参数:
  source_wiki_url       源 wiki/文稿 URL
  target_wiki_url       目标飞书 wiki URL（可选；--attachments-only 模式下不需要）
  --title TITLE         直接参数模式下的输出标题
  --no-attachments      一堂文稿不下载附件
  --attachments-only    只下载附件（视频、音频、PDF 等），不下载全文；
                        会解析页面中的普通附件和 iframe 嵌入的飞书妙记视频

配置:
  cfg/input.yaml  — tasks 列表（source_wiki_url: 来源文稿URL, target_wiki_url: 目标飞书wiki URL）；
                    一堂任务还可设置 full_copy、exclude_before_heading、
                    exclude_after_heading、include_start_heading、include_end_heading
  cfg/config.yaml — 一堂任务未设范围时，使用 s1_yitang_wikicopy.default_* 标题边界
  cfg/credentials.yaml — 飞书 app_id / app_secret / user_access_token / browser_cookie
"""

import argparse
import logging
import re
from pathlib import Path

import yaml

from modules.config_utils import (
    TARGET_WIKI_URL_ERROR,
    is_valid_target_wiki_url,
    load_config,
    safe_filename,
)

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
try:
    _input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
except yaml.YAMLError:
    _input_cfg = {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
OUTPUT_DIR = PROJECT_DIR / _input_cfg.get("path_output_dir", "output")
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "s2_wiki.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def download_wiki(wiki_url, output_dir, base_name):
    """下载飞书 wiki 文档为 markdown"""
    if not wiki_url:
        log.info("未配置 wiki_url，跳过下载")
        return None

    out_path = output_dir / f"{base_name}_wiki.md"
    if out_path.exists():
        log.info(f"wiki 文档已存在，跳过: {out_path}")
        return out_path

    from url2md import feishu_url_to_md
    log.info(f"下载教学文档: {wiki_url}")
    md_text = feishu_url_to_md(wiki_url)
    out_path.write_text(md_text, encoding="utf-8")
    log.info(f"教学文档已保存: {out_path} ({len(md_text)} 字符)")
    return out_path


def write_to_wiki(target_wiki_url, content, creds):
    """将内容写入飞书 wiki 文档"""
    if not target_wiki_url:
        log.info("未配置 target_wiki_url，跳过写入")
        return False

    import requests
    import time
    from modules.feishu_token import resolve_wiki_token, ensure_feishu_token

    session = requests.Session()

    # 解析目标文档 ID
    if "/wiki/" in target_wiki_url:
        doc_id = resolve_wiki_token(target_wiki_url, creds, session)
    else:
        doc_id = target_wiki_url.rstrip("/").split("/")[-1].split("?")[0]

    log.info(f"写入飞书 wiki: doc_id={doc_id}")

    # 将 markdown 内容转为飞书 blocks 并写入
    # 使用 docx API 追加内容
    user_token = ensure_feishu_token(creds, session)
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
    }

    # 将 markdown 按行转为飞书 text blocks
    blocks = []
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        # heading
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m:
            level = len(m.group(1))
            blocks.append({
                "block_type": 2 + level,  # heading1=3, heading2=4, ...
                "heading": {
                    f"heading{level}": {
                        "elements": [{"text_run": {"content": m.group(2)}}]
                    }
                },
            })
        else:
            blocks.append({
                "block_type": 2,  # text
                "text": {
                    "elements": [{"text_run": {"content": line}}]
                },
            })

    # 获取文档根 block
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks"
    resp = session.get(url, headers=headers, params={"page_size": 1}, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        log.error(f"获取文档 blocks 失败: {data}")
        return False

    # 批量创建 blocks（追加到文档末尾）
    root_block_id = doc_id
    batch_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks/{root_block_id}/children"

    # 分批写入（API 限制每次最多 50 个 block）
    batch_size = 50
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i + batch_size]
        body = {"children": batch, "index": -1}
        resp = session.post(batch_url, headers=headers, json=body, timeout=30)
        result = resp.json()
        if result.get("code") != 0:
            log.error(f"写入 blocks 失败 (batch {i//batch_size+1}): {result}")
            return False
        log.info(f"写入 {len(batch)} 个 blocks (batch {i//batch_size+1})")

    log.info(f"飞书 wiki 写入完成: {len(blocks)} 个 blocks")
    return True


def get_task_title(task, config, creds):
    """获取任务标题（优先 titleShougong > task.title > 自动获取）"""
    manual_title = config.get("titleShougong", "") or task.get("title", "")
    if manual_title:
        return manual_title

    source_type = task.get("source_type", "")
    if source_type == "feishu_minutes":
        from modules.feishu_minutes import extract_minutes_token, get_minutes_info
        from modules.feishu_token import ensure_feishu_token
        import requests
        session = requests.Session()
        token = extract_minutes_token(task["source_huifang_url"])
        user_token = ensure_feishu_token(creds, session)
        title, _, _ = get_minutes_info(token, user_token, session)
        return title

    return "untitled"


def _is_yitang_wiki_url(url: str) -> bool:
    """判断文稿 URL 是否应交给一堂专用脚本处理。"""
    return bool(re.search(r"yitang\.top|yitanger\.feishu\.cn", url or "", re.IGNORECASE))


def main():
    parser = argparse.ArgumentParser(description="下载教学文档并可写入飞书 wiki")
    parser.add_argument("source_wiki_url", nargs="?", help="源 wiki/文稿 URL")
    parser.add_argument("target_wiki_url", nargs="?", default="", help="目标飞书 wiki URL")
    parser.add_argument("--title", default="", help="直接参数模式下的输出标题")
    parser.add_argument("--no-attachments", action="store_true", help="一堂文稿不下载附件")
    parser.add_argument("--attachments-only", action="store_true", help="只下载附件（视频、音频、PDF 等），不下载全文")
    args = parser.parse_args()
    if args.attachments_only and args.no_attachments:
        parser.error("--attachments-only 与 --no-attachments 不能同时使用")
    if not args.attachments_only and not is_valid_target_wiki_url(args.target_wiki_url):
        parser.error(TARGET_WIKI_URL_ERROR)

    config, creds = load_config(include_input=not bool(args.source_wiki_url))
    config.setdefault("path_output_dir", "output/")
    config.setdefault("path_log_dir", "log-err/")
    config.setdefault("path_yitang_dir", "out-yitang/")
    if args.source_wiki_url:
        tasks = [{
            "source_wiki_url": args.source_wiki_url,
            "target_wiki_url": args.target_wiki_url,
            "title": args.title,
            "no_attachments": args.no_attachments,
            "attachments_only": args.attachments_only,
        }]
    else:
        tasks = config.get("tasks", [])
    if not tasks:
        log.warning("config.yaml 中无任务")
        return
    if any(not is_valid_target_wiki_url(task.get("target_wiki_url", "")) for task in tasks):
        log.error(TARGET_WIKI_URL_ERROR)
        return False

    output_dir = PROJECT_DIR / config.get("path_output_dir", "output").rstrip("/")
    output_dir.mkdir(exist_ok=True)

    for i, task in enumerate(tasks):
        source_type = task.get("source_type", "")
        log.info(f"=== 任务 {i+1}/{len(tasks)} ===")

        wiki_url = task.get("source_wiki_url", "")
        if source_type == "yitang" or _is_yitang_wiki_url(wiki_url):
            from s2w_yitang_wiki import process_yitang_wiki
            process_yitang_wiki(task, config, creds)
            continue

        if task.get("attachments_only") or args.attachments_only:
            from s2w_yitang_wiki import YitangCopier
            copier = YitangCopier(include_input=False)
            copier.download_attachments = True
            copier._source_is_yitang = False
            copier._downloaded_attachment_tokens = set()
            log.info("附件模式：获取 wiki 页面 blocks...")
            blocks_data = copier.fetch_feishu_wiki_blocks(wiki_url)
            root_block = blocks_data.get("blocks", {})
            flattened = copier._flatten_blocks(root_block)
            # 从页面 block 中提取标题
            page_attr = root_block.get("blockAttr", {}).get("page", {})
            doc_title = ""
            for el in page_attr.get("elements", []):
                tr = el.get("text_run") or el.get("textRun")
                if tr:
                    doc_title += tr.get("content", "")
            doc_title = doc_title or task.get("title", "") or "untitled"
            log.info(f"页面标题: {doc_title}，共 {len(flattened)} blocks，开始下载附件...")
            copier._download_attachments(flattened, doc_title)
            log.info("附件下载完成")
            continue

        title = get_task_title(task, config, creds)
        base_name = safe_filename(title)

        # 下载教学文档
        download_wiki(wiki_url, output_dir, base_name)

        # 写入飞书 wiki（如果配置了 target_wiki_url）
        target_wiki_url = task.get("target_wiki_url", "")
        if target_wiki_url:
            # 读取要写入的内容（优先 addon，其次修订字幕）
            addon_path = output_dir / f"{base_name}_addon.md"
            fix_srt_path = output_dir / f"{base_name}_ori_fix.srt"
            wm_fix_path = output_dir / f"{base_name}_wm_fix.srt"

            content = ""
            if addon_path.exists():
                content = addon_path.read_text(encoding="utf-8")
                log.info(f"写入 addon 内容到 wiki")
            elif fix_srt_path.exists():
                content = fix_srt_path.read_text(encoding="utf-8")
                log.info(f"写入修订字幕到 wiki")
            elif wm_fix_path.exists():
                content = wm_fix_path.read_text(encoding="utf-8")
                log.info(f"写入 Whisper 修订字幕到 wiki")

            if content:
                write_to_wiki(target_wiki_url, content, creds)
            else:
                log.info("无可写入内容，跳过 wiki 写入")

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
