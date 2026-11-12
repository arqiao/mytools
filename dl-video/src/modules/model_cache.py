"""本地 Hugging Face 模型共用的两周更新检查与缓存加载逻辑。"""

import fnmatch
import hashlib
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)
MODEL_CHECK_DIR = Path.home() / ".cache" / "dl-video" / "model-checks"
UPDATE_CHECK_INTERVAL = 14 * 24 * 60 * 60


def _read_check(repo_id, check_dir):
    path = check_dir / (repo_id.replace("/", "__") + ".json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning(f"读取模型检查记录失败，将重新检查: {e}")
        return {}


def _find_cached_path(repo_id, is_complete, check, legacy_path):
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    if check.get("path") and is_complete(Path(check["path"])):
        return Path(check["path"])
    if check.get("revision"):
        try:
            path = Path(snapshot_download(
                repo_id, revision=check["revision"], local_files_only=True,
            ))
            if is_complete(path):
                return path
        except LocalEntryNotFoundError:
            pass
    if legacy_path and is_complete(Path(legacy_path)):
        return Path(legacy_path)
    try:
        path = Path(snapshot_download(repo_id, local_files_only=True))
        if is_complete(path):
            return path
    except LocalEntryNotFoundError:
        pass
    return None


def get_cached_model_path(repo_id, is_complete, *, legacy_path=None, check_dir=None):
    """只读查找已记录的版本和旧缓存，不联网或检查更新。"""
    check_dir = Path(check_dir or MODEL_CHECK_DIR)
    check = _read_check(repo_id, check_dir)
    path = _find_cached_path(repo_id, is_complete, check, legacy_path)
    return str(path) if path else None


def _files_match_remote(path, info, allow_patterns):
    """旧目录没有提交号时，用远端文件哈希核对实际模型内容。"""
    files = [file for file in info.siblings if any(
        fnmatch.fnmatch(file.rfilename, pattern) for pattern in (allow_patterns or ["*"])
    )]
    if not files:
        return False
    for file in files:
        local = path / file.rfilename
        if not local.is_file() or local.stat().st_size != file.size:
            return False
        if file.lfs:
            digest = hashlib.sha256()
            expected = file.lfs.sha256
        else:
            digest = hashlib.sha1(f"blob {file.size}\0".encode())
            expected = file.blob_id
        with local.open("rb") as stream:
            while block := stream.read(8 * 1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != expected:
            return False
    return True


def resolve_cached_model(repo_id, is_complete, *, legacy_path=None, downloader=None,
                         allow_patterns=None, check_dir=None):
    """未满两周使用本地版本；到期比较提交号，更新失败时保留旧模型。"""
    from huggingface_hub import HfApi, snapshot_download

    check_dir = Path(check_dir or MODEL_CHECK_DIR)
    check = _read_check(repo_id, check_dir)
    cached_path = _find_cached_path(repo_id, is_complete, check, legacy_path)
    cached_revision = None
    if cached_path:
        if cached_path.parent.name == "snapshots":
            cached_revision = cached_path.name
        elif check.get("path") == str(cached_path):
            cached_revision = check.get("revision")
    if (cached_path and cached_revision == check.get("revision")
            and cached_revision and time.time() - check.get("checked_at", 0) <= UPDATE_CHECK_INTERVAL):
        log.info(f"  使用本地模型: {cached_path}")
        return str(cached_path)

    try:
        revision = "main"
        if cached_path:
            log.info(f"  检查模型更新（无检查记录或已超过 14 天）: {repo_id}")
            api = HfApi(endpoint=os.environ.get("HF_ENDPOINT"))
            if cached_revision:
                info = api.model_info(repo_id, timeout=10)
            else:
                info = api.model_info(repo_id, timeout=10, files_metadata=True)
                log.info("  旧缓存无版本记录，核对模型文件哈希")
                if _files_match_remote(cached_path, info, allow_patterns):
                    cached_revision = info.sha
            revision = info.sha
        if cached_path and revision == cached_revision:
            log.info("  远端版本未变化，继续使用本地模型")
            model_path = cached_path
        else:
            reason = "发现新版本" if cached_path else "模型缓存缺失或不完整"
            log.info(f"  {reason}，下载: {repo_id}")
            model_path = Path(downloader(revision) if downloader else snapshot_download(
                repo_id, revision=revision, allow_patterns=allow_patterns,
            ))
            if not is_complete(model_path):
                raise RuntimeError(f"模型下载不完整: {repo_id}")
            if revision == "main":
                revision = model_path.name
    except Exception as e:
        if not cached_path:
            raise
        log.warning(f"模型更新检查或下载失败，继续使用本地模型: {e}")
        return str(cached_path)

    record = {"checked_at": time.time(), "revision": revision}
    if model_path.parent.name != "snapshots":
        record["path"] = str(model_path)
    try:
        check_dir.mkdir(parents=True, exist_ok=True)
        (check_dir / (repo_id.replace("/", "__") + ".json")).write_text(
            json.dumps(record, indent=2), encoding="utf-8",
        )
    except OSError as e:
        log.warning(f"保存模型检查记录失败: {e}")
    log.info(f"  使用本地模型: {model_path}")
    return str(model_path)
