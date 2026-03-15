"""Step2: 下载教学文档 + 写入飞书 wiki"""

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
        logging.FileHandler(LOG_DIR / "s2_wiki.log", encoding="utf-8"),
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
    """将 yitang/src 加入 sys.path"""
    yitang_dir = Path(PROJECT_DIR / config.get("yitang_dir", "../yitang"))
    yitang_src = yitang_dir / "src"
    if str(yitang_src) not in sys.path:
        sys.path.insert(0, str(yitang_src))
    return yitang_dir


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

    from yitang_wiki import YitangCopier
    copier = YitangCopier()

    # 解析目标文档 ID
    if "/wiki/" in target_wiki_url:
        doc_id = copier.resolve_wiki_token(target_wiki_url)
    else:
        doc_id = target_wiki_url.rstrip("/").split("/")[-1].split("?")[0]

    log.info(f"写入飞书 wiki: doc_id={doc_id}")

    # 将 markdown 内容转为飞书 blocks 并写入
    # 使用 docx API 追加内容
    import requests
    import time
    from s1_huifang import ensure_feishu_token

    session = requests.Session()
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
    """获取任务标题（优先手动指定，否则从妙记获取）"""
    manual_title = task.get("title", "")
    if manual_title:
        return manual_title

    source_type = task.get("source_type", "")
    if source_type == "feishu_minutes":
        from s1_huifang import extract_minutes_token, get_minutes_info
        from s1_huifang import ensure_feishu_token
        import requests
        session = requests.Session()
        token = extract_minutes_token(task["source_url"])
        user_token = ensure_feishu_token(creds, session)
        title, _ = get_minutes_info(token, user_token, session)
        return title

    return "untitled"


def main():
    config, creds = load_config()
    setup_yitang_path(config)

    tasks = config.get("tasks", [])
    if not tasks:
        log.warning("config.yaml 中无任务")
        return

    output_dir = PROJECT_DIR / config.get("output_dir", "output")
    output_dir.mkdir(exist_ok=True)

    for i, task in enumerate(tasks):
        log.info(f"=== 任务 {i+1}/{len(tasks)} ===")
        title = get_task_title(task, config, creds)
        base_name = safe_filename(title)

        # 下载教学文档
        wiki_url = task.get("wiki_url", "")
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
