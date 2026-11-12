"""Whisper 模型下载工具 — 支持 HuggingFace 镜像加速

完整缓存优先；距上次成功检查超过 14 天时比较远端版本，有变化才下载。

用法:
  # 下载 faster-whisper 模型（默认后端）
  python src/tools/model_downloader.py medium
  python src/tools/model_downloader.py large-v3

  # 下载 MLX Whisper 模型（Apple Silicon Mac 专用）
  python src/tools/model_downloader.py --backend mlx large-v3-turbo
  python src/tools/model_downloader.py --backend mlx medium

  # 下载所有模型
  python src/tools/model_downloader.py --all
  python src/tools/model_downloader.py --backend mlx --all

  # 列出所有模型缓存状态
  python src/tools/model_downloader.py --list

  # 指定镜像（默认使用 hf-mirror.com）
  python src/tools/model_downloader.py --mirror https://huggingface.co medium

参数:
  model             模型大小: tiny / small / medium / large / large-v3 / large-v3-turbo
  --backend         下载后端: faster-whisper / mlx / all（默认: faster-whisper）
  --list            列出所有模型状态，不下载
  --all             下载指定后端的所有模型
  --mirror URL      指定 HuggingFace 镜像 URL（默认: https://hf-mirror.com）
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

# HuggingFace 镜像（国内加速）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from huggingface_hub import HfApi, scan_cache_dir, snapshot_download

# 兼容直接执行 python src/tools/model_downloader.py。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules.model_cache import MODEL_CHECK_DIR, get_cached_model_path, resolve_cached_model

log = logging.getLogger(__name__)
LOCAL_MODEL_ROOT = Path.home() / ".cache" / "dl-video" / "models"
MODEL_PATTERNS = ["*.json", "*.bin", "*.npz", "*.safetensors", "*.txt", "*.model"]

# faster-whisper 模型仓库映射
FASTER_WHISPER_MODELS = {
    "tiny": "Systran/faster-whisper-tiny",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large": "Systran/faster-whisper-large-v3",
}

# MLX Whisper 模型仓库映射
MLX_WHISPER_MODELS = {
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}

# 每类模型必须包含的文件
FASTER_REQUIRED_FILES = ["model.bin", "config.json", "tokenizer.json"]
MLX_REQUIRED_FILES = ["config.json"]

BACKEND_MODELS = {
    "faster-whisper": FASTER_WHISPER_MODELS,
    "mlx": MLX_WHISPER_MODELS,
}

BACKEND_REQUIRED_FILES = {
    "faster-whisper": FASTER_REQUIRED_FILES,
    "mlx": MLX_REQUIRED_FILES,
}


def normalize_backend(backend: str) -> str:
    if backend in {"faster", "faster_whisper", "whisper"}:
        return "faster-whisper"
    return backend


def normalize_model_name(model_size: str) -> str:
    return model_size.strip().lower().replace("_", "-")


def _model_files_ok(files: set[str], backend: str) -> bool:
    required = BACKEND_REQUIRED_FILES[backend]
    if not all(filename in files for filename in required):
        return False
    if backend == "mlx":
        return "weights.npz" in files or "weights.safetensors" in files
    return True


def _local_model_dir(repo_id: str) -> Path:
    return LOCAL_MODEL_ROOT / repo_id.replace("/", "__")


def _model_dir_ok(path: Path, backend: str) -> bool:
    if not path.exists():
        return False
    files = {p.name for p in path.iterdir() if p.is_file() and not p.name.startswith(".")}
    return _model_files_ok(files, backend)


def _download_with_requests(repo_id: str, backend: str, endpoint: str,
                            revision: str = "main") -> str:
    """Hub 客户端失败时的兜底下载：API 列文件，普通 GET 拉取。"""
    api = HfApi(endpoint=endpoint)
    info = api.model_info(repo_id, revision=revision, timeout=30)
    local_dir = _local_model_dir(repo_id) / "revisions" / info.sha
    filenames = [
        sibling.rfilename
        for sibling in info.siblings
        if sibling.rfilename and not sibling.rfilename.endswith("/")
    ]
    if not filenames:
        raise RuntimeError("模型仓库文件列表为空")

    local_dir.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        if ".." in Path(filename).parts:
            raise RuntimeError(f"跳过异常文件路径: {filename}")
        target = local_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > 0:
            continue

        url = f"{endpoint.rstrip('/')}/{repo_id}/resolve/{info.sha}/{quote(filename, safe='/')}"
        log.info(f"  下载 {filename}...")
        with requests.get(url, stream=True, timeout=(15, 60), allow_redirects=True) as resp:
            resp.raise_for_status()
            tmp = target.with_suffix(target.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp.replace(target)

    if not _model_dir_ok(local_dir, backend):
        files = {p.name for p in local_dir.rglob("*") if p.is_file()}
        raise RuntimeError(f"模型文件不完整: {sorted(files)}")
    return str(local_dir)


def get_repo_id(model_size: str, backend: str = "faster-whisper") -> str | None:
    backend = normalize_backend(backend)
    model_size = normalize_model_name(model_size)
    return BACKEND_MODELS.get(backend, {}).get(model_size)


def check_model(model_size: str, backend: str = "faster-whisper") -> bool:
    """检查模型是否已完整下载"""
    return get_model_path(model_size, backend) is not None


def _download_repository(repo_id: str, backend: str, revision: str) -> str:
    """下载指定版本，保留镜像、官方源和普通 HTTP 兜底路径。"""
    endpoint = os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
    endpoints = [endpoint]
    if endpoint.rstrip("/") != "https://huggingface.co":
        endpoints.append("https://huggingface.co")

    log.info(f"开始下载模型: {backend} -> {repo_id}@{revision} "
             f"(镜像: {endpoint})")
    last_error = None
    for current_endpoint in endpoints:
        try:
            log.info(f"  使用 endpoint: {current_endpoint}")
            local_path = snapshot_download(repo_id, revision=revision, endpoint=current_endpoint,
                                           allow_patterns=MODEL_PATTERNS)
            if not _model_dir_ok(Path(local_path), backend):
                files = {p.name for p in Path(local_path).rglob("*") if p.is_file()}
                raise RuntimeError(f"模型文件不完整: {sorted(files)}")
            log.info(f"模型下载完成: {local_path}")
            return local_path
        except Exception as e:
            log.warning(f"  snapshot 下载失败: {current_endpoint} ({e})")
            try:
                local_path = _download_with_requests(repo_id, backend, current_endpoint, revision)
                log.info(f"模型下载完成: {local_path}")
                return local_path
            except Exception as fallback_error:
                last_error = fallback_error
                log.warning(f"  普通 HTTP 下载失败: {current_endpoint} ({fallback_error})")

    raise RuntimeError(f"下载失败: {last_error}")


def resolve_model_path(model_size: str, backend: str = "faster-whisper") -> str | None:
    """执行两周检查策略，返回本次应加载的完整本地模型目录。"""
    backend = normalize_backend(backend)
    repo_id = get_repo_id(model_size, backend)
    if not repo_id:
        choices = ", ".join(BACKEND_MODELS.get(backend, {}))
        log.error(f"不支持的模型: backend={backend}, model={model_size}，可选: {choices}")
        return None
    try:
        return resolve_cached_model(
            repo_id, lambda path: _model_dir_ok(path, backend),
            legacy_path=get_model_path(model_size, backend),
            downloader=lambda revision: _download_repository(repo_id, backend, revision),
            allow_patterns=MODEL_PATTERNS, check_dir=MODEL_CHECK_DIR,
        )
    except Exception as e:
        log.error(f"模型不可用: {repo_id} ({e})")
        return None


def download_model(model_size: str, backend: str = "faster-whisper") -> bool:
    """确保模型完整；已有模型也按两周间隔检查更新。"""
    return resolve_model_path(model_size, backend) is not None


def ensure_model(model_size: str, backend: str = "faster-whisper", auto_download: bool = True) -> bool:
    """确保模型可用（已下载则跳过，未下载则自动下载）"""
    if not auto_download:
        return check_model(model_size, backend)
    return download_model(model_size, backend)


def get_model_path(model_size: str, backend: str = "faster-whisper") -> str | None:
    """返回已下载模型的本地缓存目录路径，未下载则返回 None"""
    backend = normalize_backend(backend)
    repo_id = get_repo_id(model_size, backend)
    if not repo_id:
        return None
    path = get_cached_model_path(
        repo_id, lambda path: _model_dir_ok(path, backend),
        legacy_path=_local_model_dir(repo_id), check_dir=MODEL_CHECK_DIR,
    )
    if path:
        return path
    try:
        cache_info = scan_cache_dir()
        for repo in cache_info.repos:
            if repo.repo_id == repo_id:
                for rev in sorted(repo.revisions, key=lambda item: item.last_modified, reverse=True):
                    files = {p.file_name for p in rev.files}
                    if _model_files_ok(files, backend):
                        return str(rev.snapshot_path)
    except Exception:
        pass
    return None


def list_models(backend: str = "faster-whisper"):
    """列出所有模型及其状态"""
    backend = normalize_backend(backend)
    for size, repo_id in BACKEND_MODELS.get(backend, {}).items():
        status = "已下载" if check_model(size, backend) else "未下载"
        log.info(f"  {backend:14s} {size:16s} ({repo_id}) - {status}")


def main():
    _project_dir = Path(__file__).parent.parent.parent
    _cfg_dir = _project_dir / "cfg"
    _icfg = yaml.safe_load((_cfg_dir / "input.yaml").read_text(encoding="utf-8")) or {}
    log_dir = _project_dir / _icfg.get("path_log_dir", "log-err")
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
    parser.add_argument("model", nargs="?", help="模型大小: tiny/small/medium/large/large-v3/large-v3-turbo")
    parser.add_argument("--backend", default="faster-whisper",
                        choices=["faster-whisper", "mlx", "all"],
                        help="模型后端: faster-whisper/mlx/all (默认: faster-whisper)")
    parser.add_argument("--list", action="store_true", help="列出所有模型状态")
    parser.add_argument("--all", action="store_true", help="下载所有模型")
    parser.add_argument("--mirror", default=None, help="指定 HuggingFace 镜像 URL")
    args = parser.parse_args()

    if args.mirror:
        os.environ["HF_ENDPOINT"] = args.mirror

    backends = ["faster-whisper", "mlx"] if args.backend == "all" else [args.backend]

    if args.list:
        for backend in backends:
            list_models(backend)
        return

    if args.all:
        for backend in backends:
            for size in BACKEND_MODELS[backend]:
                download_model(size, backend)
        return

    if not args.model:
        parser.print_help()
        print("\n可用模型:")
        for backend in backends:
            list_models(backend)
        return

    for backend in backends:
        download_model(args.model, backend)


if __name__ == "__main__":
    main()
