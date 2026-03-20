#!/usr/bin/env python3
"""
五步流水线脚本
依次执行：s1 → s2 → s3 → s4 → s5
每个 step 输出日志到 log-err 目录，错误时通知到飞书 debug 群

用法：
  python run_pipeline.py                    # 从 S1 开始执行完整流水线
  python run_pipeline.py /path/to/audio.mp3  # 从 S3 开始，跳过 S1/S2
"""

import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

# Windows 控制台 UTF-8 模式
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
OUTPUT_DIR = PROJECT_DIR / "output"
LOG_DIR = PROJECT_DIR / "log-err"
LOG_DIR.mkdir(exist_ok=True)

# 飞书群通知配置
FEISHU_NOTIFY_CHAT_ID = "oc_42e15484900d10f7f30bcd18d72d1397"

STEPS = [
    ("s1", "视频下载", "src/s1_huifang.py"),
    ("s2", "教学文档", "src/s2_wiki.py"),
    ("s3", "Whisper字幕", "src/s3_subtitle.py"),
    ("s4", "字幕修订", "src/s4_srt_fix.py"),
    ("s5", "生成Addon", "src/s5_addon.py"),
]


def load_credentials():
    creds_path = CFG_DIR / "credentials.yaml"
    return yaml.safe_load(creds_path.read_text(encoding="utf-8"))


def refresh_token(creds):
    """使用 refresh_token 刷新 access_token"""
    fs = creds.get("feishu", {})
    refresh_token = fs.get("user_refresh_token", "")
    if not refresh_token:
        return None

    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/authen/v1/oidc/refresh_access_token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"  [Token刷新失败] {data.get('msg')}")
            return None

        td = data["data"]
        creds["feishu"]["user_access_token"] = td["access_token"]
        creds["feishu"]["user_refresh_token"] = td["refresh_token"]
        creds["feishu"]["user_token_expire_time"] = int(time.time()) + td["expires_in"]

        # 写回 credentials.yaml
        creds_path = CFG_DIR / "credentials.yaml"
        with open(creds_path, "w", encoding="utf-8") as f:
            yaml.dump(creds, f, allow_unicode=True, default_flow_style=False)

        print("  [Token 刷新成功]")
        return creds
    except Exception as e:
        print(f"  [Token刷新异常] {e}")
        return None


def ensure_token(creds):
    """确保 token 有效，必要时刷新"""
    expire = creds.get("feishu", {}).get("user_token_expire_time", 0)
    if time.time() > expire - 60:
        print("[INFO] 飞书 token 过期，尝试刷新...")
        new_creds = refresh_token(creds)
        if new_creds:
            return new_creds
    return creds


def get_feishu_token(creds):
    token = creds.get("feishu", {}).get("user_access_token", "")
    expire = creds.get("feishu", {}).get("user_token_expire_time", 0)
    if time.time() > expire - 60:
        print("[警告] 飞书 token 已过期")
    return token


def notify_feishu(creds, msg: str):
    """发送文本消息到飞书群（使用 app token）"""
    try:
        # 先获取 app token
        fs = creds.get("feishu", {})
        app_id = fs.get("app_id", "")
        app_secret = fs.get("app_secret", "")

        app_resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        ).json()

        app_token = app_resp.get("app_access_token")
        if not app_token:
            print(f"  [飞书通知失败] 获取 app token 失败")
            return

        content = json.dumps({"text": msg}, ensure_ascii=False)
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={
                "Authorization": f"Bearer {app_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "receive_id": FEISHU_NOTIFY_CHAT_ID,
                "msg_type": "text",
                "content": content,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"  [飞书通知失败] {data.get('msg')}")
        else:
            print(f"  [飞书通知已发送]")
    except Exception as e:
        print(f"  [飞书通知异常] {e}")


def run_step(step_id, step_name, script_path, extra_args=None):
    """运行单个 step，返回 (success, error_msg)"""
    log_file = LOG_DIR / f"{step_id}_pipeline.log"

    print(f"\n{'='*60}")
    print(f"▶ Step {step_id}: {step_name}")
    print(f"{'='*60}")

    start_time = time.time()

    # 使用 python 运行脚本，日志同时输出到文件和控制台
    cmd = [sys.executable, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)

    try:
        # 设置环境让子进程输出 UTF-8
        env = {"PYTHONIOENCODING": "utf-8"}
        env.update(__import__("os").environ)

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # 实时输出并写入日志
        with open(log_file, "w", encoding="utf-8") as log_f:
            for line in proc.stdout:
                print(line, end="")
                log_f.write(line)
                log_f.flush()

        proc.wait()
        elapsed = time.time() - start_time

        if proc.returncode == 0:
            print(f"\n✓ Step {step_id} 完成 (耗时 {elapsed:.1f}s)")
            return True, None
        else:
            error_msg = f"Step {step_id} 失败，exit code {proc.returncode}"
            print(f"\n✗ {error_msg}")
            return False, error_msg

    except Exception as e:
        error_msg = f"Step {step_id} 异常: {e}"
        print(f"\n✗ {error_msg}")
        # 写入错误日志
        with open(log_file, "a", encoding="utf-8") as log_f:
            log_f.write(f"\n[EXCEPTION] {e}\n")
        return False, error_msg


def main():
    """解析命令行参数：
    -s2         : 从 S2 开始执行（参数取自配置文件）
    <mp3_path>  : 从 S3 开始执行，传入 MP3 文件路径
    <srt_path>  : 从 S4 开始执行，传入 SRT 文件路径（不含 _fix）
    <fix_srt>   : 从 S5 开始执行，传入 _fix.srt 文件路径
    """
    # 解析命令行参数
    start_step = "s1"
    input_file = None
    input_type = None  # "mp3", "srt", "fix_srt"

    for arg in sys.argv[1:]:
        if arg == "-s2":
            start_step = "s2"
            print(f"[INFO] 从 S2 开始执行（参数取自配置文件）")
        else:
            input_file = arg.strip()
            if input_file:
                # 检查文件类型
                if input_file.lower().endswith(".mp3"):
                    start_step = "s3"
                    input_type = "mp3"
                    print(f"[INFO] 从 S3 开始执行: MP3={input_file}")
                elif "_fix.srt" in input_file.lower():
                    start_step = "s5"
                    input_type = "fix_srt"
                    print(f"[INFO] 从 S5 开始执行: _fix.srt={input_file}")
                elif input_file.lower().endswith(".srt"):
                    start_step = "s4"
                    input_type = "srt"
                    print(f"[INFO] 从 S4 开始执行: SRT={input_file}")
                else:
                    print(f"[警告] 未识别的文件类型: {input_file}")

    print(f"{'='*60}")
    print("五步流水线开始")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    creds = load_credentials()

    # 设置环境变量供后续步骤使用
    if input_file and input_type:
        input_path = Path(input_file)
        # 从文件名提取标题（去掉后缀作为默认标题）
        default_title = input_path.stem
        # 去掉可能的 _ori、_wm、_fix 后缀
        for suffix in ["_ori", "_wm", "_fix"]:
            if default_title.endswith(suffix):
                default_title = default_title[:-len(suffix)]
                break

        os.environ["DL_VIDEO_INPUT_FILE"] = str(input_path.absolute())
        os.environ["DL_VIDEO_INPUT_TYPE"] = input_type
        os.environ["DL_VIDEO_DEFAULT_TITLE"] = default_title
        print(f"[INFO] 输入标题: {default_title}")
    else:
        # 确保 token 有效（仅在全流程模式需要）
        creds = ensure_token(creds)

    feishu_token = get_feishu_token(creds)

    # 读取 config.yaml 获取任务信息
    config_path = CFG_DIR / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    tasks = config.get("tasks", [])
    task_count = len(tasks)

    # 发送开始通知
    if task_count > 0:
        task_info = f"任务: {task_count} 个\n"
        if tasks:
            task_info += f"首个: {tasks[0].get('source_url', 'N/A')[:60]}"
        notify_feishu(creds, f"[dl-video] 流水线开始\n{task_info}")

    failed_steps = []
    error_details = []

    # 每次执行 step 后可能刷新了 token，需要重新加载
    creds = load_credentials()
    feishu_token = get_feishu_token(creds)

    # 确定起始步骤的索引
    step_indices = {"s1": 0, "s2": 1, "s3": 2, "s4": 3, "s5": 4}
    start_index = step_indices.get(start_step, 0)

    # mp3_path 用于 s3 传参调用
    mp3_path = None
    # srt_path 用于 s4 传参调用
    srt_path = None
    # fix_srt_path 用于 s5 传参调用
    fix_srt_path = None

    # 手动传入 srt 模式：直接从参数确定 srt 路径
    if input_file and input_type == "srt":
        srt_path = Path(input_file).absolute()

    # 手动传入 fix_srt 模式：直接传给 s5
    if input_file and input_type == "fix_srt":
        fix_srt_path = Path(input_file).absolute()

    if input_file and input_type == "mp3":
        mp3_path = Path(input_file).absolute()

    for i, (step_id, step_name, script_path) in enumerate(STEPS):
        # 跳过起始步骤之前的步骤
        if i < start_index:
            print(f"\n⏭️ 跳过 Step {step_id} ({step_name})")
            continue

        script = PROJECT_DIR / script_path
        if not script.exists():
            error = f"脚本不存在: {script}"
            print(f"✗ {error}")
            failed_steps.append((step_id, error))
            error_details.append(error)
            break

        # s1 完成后扫描 output 目录获取 mp3 路径
        if step_id == "s3" and mp3_path is None:
            mp3_files = sorted(OUTPUT_DIR.glob("*.mp3"))
            if mp3_files:
                mp3_path = mp3_files[-1].absolute()
                os.environ["DL_VIDEO_INPUT_FILE"] = str(mp3_path)
                os.environ["DL_VIDEO_INPUT_TYPE"] = "mp3"
                print(f"[INFO] 扫描到 MP3: {mp3_path.name}")

        # 扫描到 mp3 后也推导 srt 路径
        if step_id == "s3" and mp3_path and srt_path is None:
            wm_srt = mp3_path.with_name(f"{mp3_path.stem}_wm.srt")
            ori_srt = mp3_path.with_name(f"{mp3_path.stem}_ori.srt")
            if ori_srt.exists():
                srt_path = ori_srt.absolute()
            elif wm_srt.exists():
                srt_path = wm_srt.absolute()

        # s3 完成后，推导 srt 路径供 s4 使用
        if step_id == "s3" and mp3_path:
            # 基于 mp3 路径推导 _wm.srt 或 _ori.srt
            wm_srt = mp3_path.with_name(f"{mp3_path.stem}_wm.srt")
            ori_srt = mp3_path.with_name(f"{mp3_path.stem}_ori.srt")
            if ori_srt.exists():
                srt_path = ori_srt.absolute()
            elif wm_srt.exists():
                srt_path = wm_srt.absolute()
            else:
                # 扫描 output 目录
                candidates = sorted(OUTPUT_DIR.glob("*_wm.srt")) + sorted(OUTPUT_DIR.glob("*_ori.srt"))
                if candidates:
                    srt_path = candidates[-1].absolute()
            if srt_path:
                print(f"[INFO] S4 输入字幕: {srt_path.name}")

        # s3 使用命令行参数传入音频文件
        extra_args = None
        if step_id == "s3" and mp3_path:
            extra_args = [str(mp3_path), "--whisper"]

        # s4 使用命令行参数传入字幕文件
        if step_id == "s4" and srt_path:
            extra_args = ["--subtitle", str(srt_path)]

        # s4 完成后，推导 fix_srt 路径供 s5 使用
        if step_id == "s4" and srt_path:
            fix_srt = srt_path.parent / f"{srt_path.stem}_fix.srt"
            if fix_srt.exists():
                fix_srt_path = fix_srt.absolute()
                print(f"[INFO] S5 输入字幕: {fix_srt_path.name}")
            else:
                # 没有 _fix 版本，用原始 srt
                fix_srt_path = srt_path
                print(f"[INFO] S5 输入字幕(无fix): {srt_path.name}")

        # s5 使用命令行参数传入字幕文件
        if step_id == "s5" and fix_srt_path:
            extra_args = ["--subtitle", str(fix_srt_path)]

        success, error_msg = run_step(step_id, step_name, script, extra_args)

        if not success:
            failed_steps.append((step_id, error_msg))
            error_details.append(error_msg)

            # 错误时通知飞书（重新加载 token）
            creds = load_credentials()
            notify_feishu(
                creds,
                f"[dl-video] ✗ Step {step_id} ({step_name}) 失败\n"
                f"错误: {error_msg}\n"
                f"日志: {LOG_DIR / f'{step_id}_pipeline.log'}"
            )
            break

        # step 成功后重新加载 token（可能被刷新了）
        creds = load_credentials()
        feishu_token = get_feishu_token(creds)

    # 汇总结果
    print(f"\n{'='*60}")
    print("流水线执行结果")
    print(f"{'='*60}")

    if not failed_steps:
        print("✓ 全部步骤执行成功")

        # 列出输出文件
        if OUTPUT_DIR.exists():
            md_files = list(OUTPUT_DIR.glob("*.md"))
            ts_files = list(OUTPUT_DIR.glob("*.ts"))
            mp3_files = list(OUTPUT_DIR.glob("*.mp3"))
            srt_files = list(OUTPUT_DIR.glob("*.srt"))

            print(f"\n输出文件:")
            if ts_files:
                print(f"  视频: {len(ts_files)} 个")
            if mp3_files:
                print(f"  音频: {len(mp3_files)} 个")
            if srt_files:
                print(f"  字幕: {len(srt_files)} 个")
            if md_files:
                print(f"  文档: {len(md_files)} 个")

        notify_feishu(creds, f"[dl-video] ✓ 流水线执行成功\n5个步骤全部完成")

    else:
        print(f"✗ {len(failed_steps)} 个步骤失败:")
        for step_id, error in failed_steps:
            print(f"  - {step_id}: {error}")

    print(f"\n日志文件目录: {LOG_DIR}")
    return len(failed_steps) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
