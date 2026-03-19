"""飞书文档 URL → 本地 Markdown 文件下载工具"""

import argparse
import logging
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
LOG_DIR = PROJECT_DIR / "log-err"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "url2md.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# yitang_wiki.py 仍在 yitang 项目中，通过 sys.path 引入
YITANG_SRC = PROJECT_DIR.parent / "yitang" / "src"


def _ensure_yitang_path():
    if str(YITANG_SRC) not in sys.path:
        sys.path.insert(0, str(YITANG_SRC))


def blocks_to_md(copier, blocks_data: dict) -> str:
    """将 blocks 数据转换为 markdown 文本"""
    root = blocks_data.get("blocks", {})
    flat = copier._flatten_blocks(root)
    lines = []
    for b in flat:
        md = copier._block_to_md(b)
        if md:
            lines.append(md)
    return "\n\n".join(lines)


def feishu_url_to_md(url: str) -> str:
    """从飞书/一堂 URL 获取文档内容，返回 markdown 文本。"""
    _ensure_yitang_path()
    from yitang_wiki import YitangCopier

    copier = YitangCopier()

    if "yitang.top/" in url:
        blocks_data = copier.fetch_source_blocks(url)
    elif "/wiki/" in url:
        doc_id = copier.resolve_wiki_token(url)
        docx_url = f"https://arqiaoknow.feishu.cn/docx/{doc_id}"
        blocks_data = copier.fetch_feishu_blocks(docx_url)
    else:
        blocks_data = copier.fetch_feishu_blocks(url)

    return blocks_to_md(copier, blocks_data)


def url_to_filename(url: str) -> str:
    """从飞书 URL 提取 token 作为默认文件名"""
    token = url.rstrip("/").split("/")[-1].split("?")[0]
    return f"{token}.md"


def main():
    parser = argparse.ArgumentParser(
        description="飞书文档 URL → 本地 Markdown 文件")
    parser.add_argument("url", help="飞书文档 URL")
    parser.add_argument("-o", "--output", default="",
                        help="输出文件路径（默认保存到 localscript/）")
    args = parser.parse_args()

    url = args.url
    if "feishu.cn/" not in url and "yitang.top/" not in url:
        log.error("仅支持飞书文档或一堂文档 URL")
        return

    log.info(f"正在获取飞书文档: {url}")
    md_text = feishu_url_to_md(url)
    log.info(f"文档内容: {len(md_text)} 字符")

    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = PROJECT_DIR / "localscript"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / url_to_filename(url)

    out_path.write_text(md_text, encoding="utf-8")
    log.info(f"已保存: {out_path}")
    print(f"\n输出文件: {out_path}")


if __name__ == "__main__":
    main()
