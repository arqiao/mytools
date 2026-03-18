#!/usr/bin/env python3
"""
新课一键处理脚本
修改下方 CONFIG 后直接运行，自动完成：
  1. yitang_wiki.py  — 写入 wiki + 生成本地 md
  2. yitang_video.py — 下载视频/音频/讨论区
  3. 重命名 md 文件与视频文件名一致
  4. subtitle_from_mp3.py — 生成字幕
  5. 移动 ts/mp3 到 NAS
"""

# ── 配置区（每次新课修改这里）────────────────────────────────
CONFIG = {
    # 课程逐字稿（飞书文档或一堂文档 URL）
    "transcript_url": "https://yitang.top/fs-doc/c625f36d3f91712f021d5bfbc47e7ae1/SCaodfwWnomwojxHxKocInkun1x",

    # 课程回放 URL（air.yitang.top/live/xxx 格式）
    "replay_url": "https://air.yitang.top/live/49l_a4ZSEb",

    # 飞书 wiki 目标写入地址
    "target_wiki_url": "https://arqiaoknow.feishu.cn/wiki/F0DewQbmniETxPkhOaYcKaVSnXe",

    # wiki 内容过滤起止标题（留空则全文复制）
    "start_heading": "",
    "end_heading": "",

    # 标题自动编号（留空则不编号）
    "heading_number_start": "",

    # NAS 目标目录
    "nas_dir": r"\\192.168.3.8\nasfiles\4-Study\V会议课程-一堂",
}
# ─────────────────────────────────────────────────────────────

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

FEISHU_NOTIFY_CHAT_ID = "oc_42e15484900d10f7f30bcd18d72d1397"

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
OUTPUT_DIR = PROJECT_DIR / "localscript"


def _get_feishu_token() -> str:
    """从 credentials.yaml 读取飞书 user_access_token"""
    creds_path = CFG_DIR / "credentials.yaml"
    creds = yaml.safe_load(creds_path.read_text(encoding="utf-8"))
    token = creds.get("feishu", {}).get("user_access_token", "")
    expire = creds.get("feishu", {}).get("user_token_expire_time", 0)
    if time.time() > expire - 60:
        print("  [警告] 飞书 token 已过期，通知可能失败")
    return token


def notify_feishu(msg: str):
    """发送文本消息到飞书群"""
    try:
        token = _get_feishu_token()
        content = json.dumps({"text": msg}, ensure_ascii=False)
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "receive_id": FEISHU_NOTIFY_CHAT_ID,
                "msg_type": "text",
                "content": content,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"  [飞书通知失败] {data.get('msg')}")
        else:
            print(f"  [飞书通知已发送]")
    except Exception as e:
        print(f"  [飞书通知异常] {e}")


def run(cmd: list, desc: str):
    print(f"\n{'='*50}")
    print(f"▶ {desc}")
    print(f"{'='*50}")
    result = subprocess.run(cmd, cwd=str(PROJECT_DIR))
    if result.returncode != 0:
        msg = f"[go-newlesson] ✗ {desc} 失败（exit code {result.returncode}）\n回放: {CONFIG['replay_url']}"
        print(f"✗ {desc} 失败（exit code {result.returncode}）")
        notify_feishu(msg)
        sys.exit(1)
    print(f"✓ {desc} 完成")


def update_yaml(path: Path, updates: dict):
    """读取 yaml，合并 updates，写回"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _deep_update(data, updates)
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _deep_update(base: dict, updates: dict):
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def step1_wiki():
    """更新 config-wiki.yaml 并运行 yitang_wiki.py"""
    cfg_path = CFG_DIR / "config-wiki.yaml"
    mapping = {
        "source_url": CONFIG["transcript_url"],
        "target_url": CONFIG["target_wiki_url"],
    }
    if CONFIG.get("start_heading"):
        mapping["heading_number"] = {
            "start_heading": CONFIG["heading_number_start"] or CONFIG["start_heading"],
            "end_heading": "",
        }
    # 不设 local_export，让程序自动以文档标题命名
    if "local_export" in mapping:
        del mapping["local_export"]

    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if CONFIG.get("start_heading"):
        data["content_range"] = {
            "start_heading": CONFIG["start_heading"],
            "end_heading": CONFIG.get("end_heading", ""),
        }
    data["mappings"] = [mapping]
    cfg_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    run([sys.executable, "src/yitang_wiki.py"], "Step 1: yitang_wiki.py")

    # 找到刚生成的 md 文件（最新的）
    mds = sorted(OUTPUT_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mds:
        notify_feishu(f"[go-newlesson] ✗ Step 1 失败：未找到生成的 md 文件\n回放: {CONFIG['replay_url']}")
        print("✗ 未找到生成的 md 文件")
        sys.exit(1)
    md_path = mds[0]
    print(f"  生成 md: {md_path.name}")
    return md_path


def step2_video():
    """更新 config-video.yaml 并运行 yitang_video.py，返回输出文件名前缀"""
    cfg_path = CFG_DIR / "config-video.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    data["livestreams"] = [{"source_url": CONFIG["replay_url"]}]
    cfg_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # 记录运行前的文件列表
    before = set(OUTPUT_DIR.glob("*.ts")) | set(OUTPUT_DIR.glob("*.mp3"))

    run([sys.executable, "src/yitang_video.py"], "Step 2: yitang_video.py")

    # 找到新生成的 ts/mp3
    after_ts = set(OUTPUT_DIR.glob("*.ts")) - before
    after_mp3 = set(OUTPUT_DIR.glob("*.mp3")) - before

    if not after_ts and not after_mp3:
        # fallback：取最新的
        all_ts = sorted(OUTPUT_DIR.glob("*.ts"), key=lambda p: p.stat().st_mtime, reverse=True)
        all_mp3 = sorted(OUTPUT_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        ts_path = all_ts[0] if all_ts else None
        mp3_path = all_mp3[0] if all_mp3 else None
    else:
        ts_path = next(iter(after_ts), None)
        mp3_path = next(iter(after_mp3), None)

    if not mp3_path and not ts_path:
        notify_feishu(f"[go-newlesson] ✗ Step 2 失败：未找到生成的视频/音频文件\n回放: {CONFIG['replay_url']}")
        print("✗ 未找到生成的视频/音频文件")
        sys.exit(1)

    base = (mp3_path or ts_path).stem
    print(f"  视频/音频文件名前缀: {base}")
    return base, ts_path, mp3_path


def step3_rename_md(md_path: Path, video_stem: str):
    """将 md 文件重命名为与视频文件一致"""
    new_md = md_path.parent / f"{video_stem}.md"
    if md_path != new_md:
        if new_md.exists():
            print(f"  目标 md 已存在，跳过重命名: {new_md.name}")
        else:
            md_path.rename(new_md)
            print(f"  md 重命名: {md_path.name} → {new_md.name}")
    return new_md


def step4_subtitle(mp3_path: Path):
    """运行 subtitle_from_mp3.py 生成字幕。
    Whisper/ctranslate2 进程退出时 C 库可能崩溃（0xC0000409），
    但字幕文件已成功生成，因此检查输出文件而非 returncode。"""
    if not mp3_path or not mp3_path.exists():
        print("  跳过字幕生成（无 mp3 文件）")
        return

    srt_path = mp3_path.with_suffix(".srt")
    srt_existed = srt_path.exists()
    srt_mtime = srt_path.stat().st_mtime if srt_existed else 0

    desc = "Step 4: subtitle_from_mp3.py"
    print(f"\n{'='*50}")
    print(f"▶ {desc}")
    print(f"{'='*50}")
    result = subprocess.run(
        [sys.executable, "src/subtitle_from_mp3.py", str(mp3_path)],
        cwd=str(PROJECT_DIR),
    )

    # 判断成功：srt 文件存在且比运行前更新
    srt_ok = srt_path.exists() and srt_path.stat().st_mtime > srt_mtime
    if srt_ok:
        if result.returncode != 0:
            print(f"  [注意] 进程退出码 {result.returncode}（C 库清理崩溃），但字幕已生成")
        print(f"✓ {desc} 完成")
    else:
        msg = f"[go-newlesson] ✗ {desc} 失败（exit code {result.returncode}）\n回放: {CONFIG['replay_url']}"
        print(f"✗ {desc} 失败（exit code {result.returncode}）")
        notify_feishu(msg)
        sys.exit(1)


def step5_move_to_nas(ts_path: Path, mp3_path: Path):
    """移动 ts/mp3 到 NAS"""
    nas = Path(CONFIG["nas_dir"])
    if not nas.exists():
        print(f"  NAS 目录不可访问，跳过移动: {nas}")
        return
    for f in [ts_path, mp3_path]:
        if f and f.exists():
            dest = nas / f.name
            shutil.move(str(f), str(dest))
            print(f"  已移动: {f.name} → {dest}")


def main():
    print("=== go-newlesson 新课处理流程 ===")
    print(f"逐字稿: {CONFIG['transcript_url']}")
    print(f"回放:   {CONFIG['replay_url']}")
    print(f"目标:   {CONFIG['target_wiki_url']}")

    md_path = step1_wiki()
    video_stem, ts_path, mp3_path = step2_video()
    step3_rename_md(md_path, video_stem)
    step4_subtitle(mp3_path)
    step5_move_to_nas(ts_path, mp3_path)

    print("\n=== 全部完成 ===")


if __name__ == "__main__":
    main()
