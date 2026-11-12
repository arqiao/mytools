"""淘宝回放测试脚本 — 验证链接参数、标题、播放地址和下载

用法:
  # 读取 input.yaml 中第一条 taobao 任务进行测试
  python src/test_taobao_replay.py

  # 指定 URL
  python src/test_taobao_replay.py --url https://81025.tbkflow.cn/pcLive/xxx

  # 下载选中的测试视频
  python src/test_taobao_replay.py --download

  # 手动指定输出文件名
  python src/test_taobao_replay.py --url https://... --download --output-name my_video

参数:
  --url URL          淘宝回放 URL，不传则读 input.yaml
  --download         下载选中的测试视频
  --output-name NAME 手动指定输出文件名（不含扩展名）
"""

import argparse
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

import requests
import yaml

from modules.config_utils import safe_filename

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
OUTPUT_DIR = PROJECT_DIR / "output" / "_test_taobao"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in [r"D:\tools\ffmpeg\bin\ffmpeg.exe", r"C:\tools\ffmpeg\bin\ffmpeg.exe"]:
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"


def find_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if path:
        return path
    ffmpeg_dir = Path(find_ffmpeg()).parent
    candidate = ffmpeg_dir / "ffprobe.exe"
    if candidate.exists():
        return str(candidate)
    return "ffprobe"


FFMPEG = find_ffmpeg()
FFPROBE = find_ffprobe()


def load_task_and_creds(url_override: str | None):
    input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
    try:
        from secrets_loader import load as _secrets_load
    except ImportError:
        import sys
        sys.path.insert(0, "/Volumes/DATADRIVE/workspace/sys")
        from secrets_loader import load as _secrets_load
    creds = dict(_secrets_load("gtokens", "gfeishu", "gkeys"))
    if url_override:
        return {"source_huifang_url": url_override, "title": ""}, creds.get("taobao", {})
    for task in input_cfg.get("tasks", []):
        if task.get("source_type") == "taobao":
            return task, creds.get("taobao", {})
    raise RuntimeError("input.yaml 中未找到 taobao 任务，且未传 --url")


def parse_taobao_url(source_url: str) -> tuple[str, str]:
    m = re.match(r"https?://(\d+)\.tbkflow\.cn/pcLive/([0-9a-fA-F]+)", source_url)
    if not m:
        raise ValueError(f"无法解析淘宝直播 URL: {source_url}")
    return m.group(1), m.group(2)


def make_session(creds_taobao: dict) -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {creds_taobao['bearer_token']}",
        "X-AuthorizationAccess": f"Bearer {creds_taobao['access_token']}",
        "cid": creds_taobao["company_id"],
        "scene": "browser",
        "Origin": f"https://{creds_taobao['company_id']}.tbkflow.cn",
        "Referer": f"https://{creds_taobao['company_id']}.tbkflow.cn/",
    })
    return s


def get_link_params(session: requests.Session, api_base: str, link_code: str) -> dict:
    url = f"{api_base}/scrm-course-api/pass/linkParam/getParamByCode"
    resp = session.post(url, json={"linkCode": link_code}, timeout=20)
    data = resp.json()
    if data.get("code") != 200 or not data.get("data"):
        raise RuntimeError(f"getParamByCode 失败: {data}")
    return data["data"].get("params", data["data"])


def get_course_info(session: requests.Session, api_base: str, company_id: str, union_id: str, course_id: str, flag: str) -> dict:
    url = f"{api_base}/scrm-course-api/pass/selectCourseInfo"
    body = {"companyId": company_id, "unionId": union_id, "id": course_id, "flag": flag}
    resp = session.post(url, json=body, timeout=20)
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"selectCourseInfo 失败: {data}")
    return data["data"]


def get_h5_play_urls(session: requests.Session, api_base: str, live_id: str) -> dict:
    url = f"{api_base}/micor-live-guest/video/watch/play/url"
    resp = session.post(url, json={"liveId": live_id, "deviceType": "H5"}, timeout=20)
    data = resp.json()
    if data.get("code") != 200 or not data.get("data"):
        raise RuntimeError(f"video/watch/play/url 失败: {data}")
    return data["data"]


def get_pc_play_urls(session: requests.Session, api_base: str, live_id: str) -> list[dict]:
    url = f"{api_base}/micor-live-guest/agora/live/video"
    resp = session.post(url, json={"liveId": live_id, "mediaType": "PC"}, timeout=20)
    data = resp.json()
    if data.get("code") != 200:
        resp = session.post(url, json={"liveId": live_id}, timeout=20)
        data = resp.json()
    if data.get("code") != 200 or not data.get("data"):
        raise RuntimeError(f"agora/live/video 失败: {data}")
    return data["data"]


def choose_download_url(h5_urls: dict, pc_urls: list[dict]) -> str:
    for key in ["VOLCANO", "VOLCANO_TENC", "HUAWEI", "HUAWEI_TENC", "VOLCANO_VOLCANO", "HUAWEI_VOLCANO"]:
        if h5_urls.get(key):
            return h5_urls[key]
    first = pc_urls[0]
    return first.get("domain") or first.get("volcanoUrl") or first.get("huaweiUrl") or ""


def get_media_duration(input_source: str) -> float:
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_source],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        return 0


def download_with_progress(media_url: str, output_path: Path, referer: str):
    duration = get_media_duration(media_url)
    log.info("开始下载测试视频: %s", output_path.name)
    if duration > 0:
        log.info("远程媒体时长: %d:%02d", int(duration // 60), int(duration % 60))

    tmp_path = output_path.with_suffix(".tmp" + output_path.suffix)
    cmd = [
        FFMPEG,
        "-headers", f"Referer: {referer}\r\nOrigin: {referer.rstrip('/')}\r\n",
        "-i", media_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-progress", "pipe:1",
        str(tmp_path),
        "-y",
    ]
    stderr_tmp = tmp_path.with_suffix(".stderr")
    stderr_f = open(stderr_tmp, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_f, text=True, encoding="utf-8", errors="replace")
        last_sec = -1
        for line in proc.stdout:
            line = line.strip()
            m = re.match(r"out_time_ms=(\d+)", line)
            if not m:
                continue
            cur_sec = int(m.group(1)) / 1_000_000
            if int(cur_sec) <= last_sec:
                continue
            last_sec = int(cur_sec)
            if duration > 0:
                pct = min(int(cur_sec / duration * 100), 99)
                print(f"\r  下载进度: {pct}% ({int(cur_sec // 60)}:{int(cur_sec % 60):02d}"
                      f" / {int(duration // 60)}:{int(duration % 60):02d})", end="", flush=True)
            else:
                size_mb = tmp_path.stat().st_size / 1024 / 1024 if tmp_path.exists() else 0
                print(f"\r  下载进度: {int(cur_sec // 60)}:{int(cur_sec % 60):02d}"
                      f" 已写入 {size_mb:.1f} MB", end="", flush=True)
        proc.wait(timeout=1800)
        stderr_f.close()
    finally:
        stderr_f.close()

    if proc.returncode != 0:
        stderr_text = stderr_tmp.read_text(encoding="utf-8", errors="replace") if stderr_tmp.exists() else ""
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg 下载失败: {stderr_text[-800:]}")

    if duration > 0:
        print(f"\r  下载进度: 100% ({int(duration // 60)}:{int(duration % 60):02d}"
              f" / {int(duration // 60)}:{int(duration % 60):02d})")
    else:
        size_mb = tmp_path.stat().st_size / 1024 / 1024 if tmp_path.exists() else 0
        print(f"\r  下载进度: 完成，已写入 {size_mb:.1f} MB")

    stderr_tmp.unlink(missing_ok=True)
    tmp_path.replace(output_path)
    log.info("下载完成: %s (%.1f MB)", output_path, output_path.stat().st_size / 1024 / 1024)


def summarize_course(label: str, info: dict):
    title = info.get("courseExternalShowName") or info.get("showName") or info.get("videoName") or info.get("name")
    log.info("[%s] title=%s", label, title)
    log.info("[%s] liveNum=%s, liveMode=%s, liveType=%s, start=%s", label,
             info.get("liveNum"), info.get("liveMode"), info.get("liveType"), info.get("startTime"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="", help="淘宝回放 URL，不传则读 input.yaml")
    parser.add_argument("--download", action="store_true", help="下载选中的测试视频")
    parser.add_argument("--output-name", default="", help="手动指定输出文件名")
    args = parser.parse_args()

    task, taobao_creds = load_task_and_creds(args.url or None)
    source_url = task["source_huifang_url"]
    api_base = taobao_creds.get("api_base", "https://cg.infyrasys.cn")
    company_id_from_url, link_code = parse_taobao_url(source_url)
    session = make_session(taobao_creds)

    params = get_link_params(session, api_base, link_code)
    union_id = params.get("unionId") or taobao_creds.get("union_id", "")
    course_id = params.get("id", "")
    flag_from_link = params.get("flag") or "CAMP-COURSE"

    print("\n=== linkParam ===")
    print(json.dumps(params, ensure_ascii=False, indent=2))

    camp_info = get_course_info(session, api_base, params.get("companyId", company_id_from_url), union_id, course_id, "CAMP-COURSE")
    actual_info = get_course_info(session, api_base, params.get("companyId", company_id_from_url), union_id, course_id, flag_from_link)

    print("\n=== selectCourseInfo(CAMP-COURSE) ===")
    print(json.dumps({
        "name": camp_info.get("name"),
        "liveNum": camp_info.get("liveNum"),
        "liveMode": camp_info.get("liveMode"),
        "liveType": camp_info.get("liveType"),
        "videoName": camp_info.get("videoName"),
        "startTime": camp_info.get("startTime"),
    }, ensure_ascii=False, indent=2))

    print(f"\n=== selectCourseInfo({flag_from_link}) ===")
    print(json.dumps({
        "name": actual_info.get("name"),
        "liveNum": actual_info.get("liveNum"),
        "liveMode": actual_info.get("liveMode"),
        "liveType": actual_info.get("liveType"),
        "videoName": actual_info.get("videoName"),
        "startTime": actual_info.get("startTime"),
    }, ensure_ascii=False, indent=2))

    summarize_course("CAMP", camp_info)
    summarize_course(flag_from_link, actual_info)

    preferred_title = actual_info.get("courseExternalShowName") or actual_info.get("showName") or actual_info.get("videoName") or actual_info.get("name")
    preferred_live_id = actual_info.get("liveNum") or params.get("liveId") or actual_info.get("id")

    log.info("测试脚本判定的真实标题: %s", preferred_title)
    log.info("测试脚本判定的真实 liveId: %s", preferred_live_id)

    h5_urls = get_h5_play_urls(session, api_base, preferred_live_id)
    pc_urls = get_pc_play_urls(session, api_base, preferred_live_id)

    print("\n=== H5 play urls ===")
    print(json.dumps(h5_urls, ensure_ascii=False, indent=2))
    print("\n=== PC play urls[0] ===")
    print(json.dumps(pc_urls[0], ensure_ascii=False, indent=2))

    chosen_url = choose_download_url(h5_urls, pc_urls)
    if not chosen_url:
        raise RuntimeError("未找到可下载地址")
    log.info("选中的下载地址: %s", chosen_url[:160] + ("..." if len(chosen_url) > 160 else ""))

    if args.download:
        output_name = args.output_name or safe_filename(preferred_title or f"taobao_test_{preferred_live_id}")
        suffix = ".mp4" if ".mp4" in chosen_url.lower() else ".mp4"
        output_path = OUTPUT_DIR / f"{output_name}{suffix}"
        download_with_progress(chosen_url, output_path, f"https://{taobao_creds['company_id']}.tbkflow.cn/")


if __name__ == "__main__":
    main()
