#!/usr/bin/env python3
"""批量校订 SRT 字幕文件 — 对指定目录下所有 .srt 文件运行 s4_srt_fix.py，然后修改文件时间戳"""

import os
import subprocess
import sys
import time
from pathlib import Path

PYTHON = sys.executable
PROJECT_DIR = Path(__file__).resolve().parent
S4_SCRIPT = PROJECT_DIR / "src" / "s4_srt_fix.py"
TIME_OFFSET_SEC = 30 * 60  # mp4 时间 +30 分钟
MTIME_TOLERANCE_SEC = 5

# 要扫描的目录
SCAN_DIRS = [
    "/Volumes/NASFILES/4-Study/V会议课程-AI",
    "/Volumes/NASFILES/4-Study/V会议课程-OT",
    "/Volumes/NASFILES/4-Study/V会议课程-一堂",
    str(Path(__file__).parent / "out-yitang"),
    str(Path(__file__).parent / "output"),
]


def find_srt_files(directories):
    """递归查找所有待校订的 .srt 文件，跳过已生成的 _fix.srt"""
    srts = []
    for d in directories:
        dp = Path(d)
        if not dp.exists():
            print(f"  [跳过] 目录不存在: {d}")
            continue
        for p in sorted(dp.rglob("*.srt")):
            if p.stem.endswith("_fix"):
                continue
            srts.append(p)
    return srts


def find_mp4_for_srt(srt_path):
    """根据 srt 文件名找到对应的 mp4 文件"""
    stem = srt_path.stem
    # 去掉 srt 后缀标记 (_wm, _ori, _fix, .mlx_l3, .mlx_turbo 等)
    base = stem
    suffixes_to_strip = [
        "_wm_fix", "_ori_fix", "_fix",
        "_wm", "_ori", "_wl", "_ws", "_wt",
        ".mlx_turbo", ".mlx_l3", ".mlx_m", ".mlx_s", ".mlx_t",
        ".xunfei", ".aliyun", ".doubao", ".feishu", ".tencent",
    ]
    for sfx in sorted(suffixes_to_strip, key=len, reverse=True):
        if base.endswith(sfx):
            base = base[: -len(sfx)]
            break

    # 在同目录下查找 mp4
    parent = srt_path.parent
    for ext in [".mp4", ".ts", ".mp3"]:
        candidate = parent / f"{base}{ext}"
        if candidate.exists():
            return candidate
    # 也尝试带日期前缀的匹配
    return None


def is_mtime_marked_fixed(srt_path):
    """字幕 mtime 若等于对应媒体文件 mtime +30 分钟，则视为已修订。"""
    media = find_mp4_for_srt(srt_path)
    if not media:
        return False
    target_mtime = media.stat().st_mtime + TIME_OFFSET_SEC
    return abs(srt_path.stat().st_mtime - target_mtime) <= MTIME_TOLERANCE_SEC


def fix_file_mtime(srt_path):
    """将 srt 文件（及其 _fix 版本）的 mtime 设为对应 mp4 的 mtime + 30 分钟"""
    mp4 = find_mp4_for_srt(srt_path)
    if not mp4:
        print(f"  [时间戳] 未找到对应视频文件，跳过: {srt_path.name}")
        return False

    mp4_mtime = mp4.stat().st_mtime
    target_mtime = mp4_mtime + TIME_OFFSET_SEC

    # 修改 srt 本身的时间
    os.utime(str(srt_path), (target_mtime, target_mtime))

    # 如果有 _fix 版本也一并修改
    fix_path = srt_path.parent / f"{srt_path.stem}_fix.srt"
    if fix_path.exists():
        os.utime(str(fix_path), (target_mtime, target_mtime))

    mp4_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mp4_mtime))
    target_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(target_mtime))
    print(f"  [时间戳] {srt_path.name}: mp4={mp4_time_str} → srt={target_time_str}")
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="批量 SRT 校订 + 时间戳修正")
    parser.add_argument("--fix-only", action="store_true", help="仅运行校订，跳过时间戳修正")
    parser.add_argument("--mtime-only", action="store_true", help="仅修正时间戳，跳过校订")
    args = parser.parse_args()

    print("=" * 60)
    print("批量 SRT 校订 + 时间戳修正")
    print("=" * 60)

    # 1. 查找所有 srt 文件
    print("\n[1/3] 扫描 SRT 文件...")
    all_srts = find_srt_files(SCAN_DIRS)
    print(f"  共找到 {len(all_srts)} 个 SRT 文件")

    # 2. 逐个运行 s4_srt_fix.py
    if not args.mtime_only:
        print(f"\n[2/3] 开始校订 ({len(all_srts)} 个文件)...")
        success_count = 0
        fail_count = 0

        for i, srt_path in enumerate(all_srts, 1):
            print(f"\n--- [{i}/{len(all_srts)}] {srt_path.name} ---")
            print(f"  路径: {srt_path}")

            fix_path = srt_path.parent / f"{srt_path.stem}_fix.srt"
            if fix_path.exists():
                print(f"  [跳过] 已存在校订文件: {fix_path.name}")
                success_count += 1
                continue
            if is_mtime_marked_fixed(srt_path):
                print("  [跳过] 字幕时间戳已标记为修订完成（媒体时间+30分钟）")
                success_count += 1
                continue

            try:
                result = subprocess.run(
                    [PYTHON, str(S4_SCRIPT), "--subtitle", str(srt_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=7200,  # 单文件最长 2 小时，避免长字幕反复超时
                    cwd=str(PROJECT_DIR),
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        if "校订完成" in line or "修正应用" in line:
                            print(f"  {line.strip()}")
                    success_count += 1
                else:
                    print(f"  [失败] exit code {result.returncode}")
                    if result.stderr:
                        print(f"  {result.stderr[:300]}")
                    fail_count += 1
            except subprocess.TimeoutExpired:
                print(f"  [超时] 30 分钟未完成")
                fail_count += 1
            except Exception as e:
                print(f"  [异常] {e}")
                fail_count += 1
    else:
        success_count = 0
        fail_count = 0
        print("\n[2/3] 跳过校订 (--mtime-only)")

    # 3. 修改文件时间戳
    if not args.fix_only:
        print(f"\n[3/3] 修改文件时间戳 (mp4 mtime + 30min)...")
        mtime_success = 0
        mtime_skip = 0

        for srt_path in all_srts:
            if fix_file_mtime(srt_path):
                mtime_success += 1
            else:
                mtime_skip += 1
    else:
        mtime_success = 0
        mtime_skip = 0
        print("\n[3/3] 跳过时间戳修正 (--fix-only)")

    # 汇总
    print(f"\n{'=' * 60}")
    print("批量校订完成")
    print(f"  校订: 成功 {success_count}, 失败 {fail_count}")
    print(f"  时间戳: 修改 {mtime_success}, 跳过 {mtime_skip}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
