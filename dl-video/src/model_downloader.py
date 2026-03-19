"""Whisper 模型下载工具 - 支持 HuggingFace 镜像加速"""

import argparse
import logging
import os
from pathlib import Path

# HuggingFace 镜像（国内加速）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from huggingface_hub import hf_hub_download, scan_cache_dir

log = logging.getLogger(__name__)

# 模型仓库映射
WHISPER_MODELS = {
    "tiny": "Systran/faster-whisper-tiny",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large": "Systran/faster-whisper-large-v3",
}

# 每个模型必须包含的文件
REQUIRED_FILES = ["model.bin", "config.json", "tokenizer.json"]


def check_model(model_size: str) -> bool:
    """检查模型是否已完整下载"""
    repo_id = WHISPER_MODELS.get(model_size)
    if not repo_id:
        return False
    try:
        cache_info = scan_cache_dir()
        for repo in cache_info.repos:
            if repo.repo_id == repo_id:
                files = {p.file_name for rev in repo.revisions for p in rev.files}
                return "model.bin" in files
    except Exception:
        pass
    return False


def download_model(model_size: str) -> bool:
    """下载指定大小的 Whisper 模型"""
    repo_id = WHISPER_MODELS.get(model_size)
    if not repo_id:
        log.error(f"不支持的模型: {model_size}，可选: {', '.join(WHISPER_MODELS)}")
        return False

    if check_model(model_size):
        log.info(f"模型 {model_size} 已存在，跳过下载")
        return True

    log.info(f"开始下载模型: {repo_id} (镜像: {os.environ.get('HF_ENDPOINT', 'default')})")
    try:
        for filename in REQUIRED_FILES:
            log.info(f"  下载 {filename}...")
            hf_hub_download(repo_id, filename)
        log.info(f"模型 {model_size} 下载完成")
        return True
    except Exception as e:
        log.error(f"下载失败: {e}")
        return False


def ensure_model(model_size: str) -> bool:
    """确保模型可用（已下载则跳过，未下载则自动下载）"""
    if check_model(model_size):
        return True
    log.info(f"模型 {model_size} 未找到，自动下载...")
    return download_model(model_size)


def get_model_path(model_size: str) -> str | None:
    """返回已下载模型的本地缓存目录路径，未下载则返回 None"""
    repo_id = WHISPER_MODELS.get(model_size)
    if not repo_id:
        return None
    try:
        cache_info = scan_cache_dir()
        for repo in cache_info.repos:
            if repo.repo_id == repo_id:
                for rev in repo.revisions:
                    files = {p.file_name for p in rev.files}
                    if "model.bin" in files:
                        return str(rev.snapshot_path)
    except Exception:
        pass
    return None


def list_models():
    """列出所有模型及其状态"""
    for size, repo_id in WHISPER_MODELS.items():
        status = "已下载" if check_model(size) else "未下载"
        log.info(f"  {size:8s} ({repo_id}) - {status}")


def main():
    log_dir = Path(__file__).parent.parent / "log-err"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "model_download.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    parser = argparse.ArgumentParser(description="Whisper 模型下载工具")
    parser.add_argument("model", nargs="?", help="模型大小: tiny/small/medium/large")
    parser.add_argument("--list", action="store_true", help="列出所有模型状态")
    parser.add_argument("--all", action="store_true", help="下载所有模型")
    parser.add_argument("--mirror", default=None, help="指定 HuggingFace 镜像 URL")
    args = parser.parse_args()

    if args.mirror:
        os.environ["HF_ENDPOINT"] = args.mirror

    if args.list:
        list_models()
        return

    if args.all:
        for size in WHISPER_MODELS:
            download_model(size)
        return

    if not args.model:
        parser.print_help()
        print("\n可用模型:")
        list_models()
        return

    download_model(args.model)


if __name__ == "__main__":
    main()
