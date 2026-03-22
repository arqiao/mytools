"""Step1-tencentmeeting: 腾讯会议回放下载 — 逐字稿 + 纪要 + 时间轴

使用方式:
  1. 安装依赖: pip install playwright && playwright install chromium
  2. config.yaml 中配置 source_type: "tencent_meeting"
  3. python src/s1_huifang.py
"""

import logging
import re
import time
from pathlib import Path

import requests
import yaml
from playwright.sync_api import sync_playwright

from modules.ffmpeg_utils import extract_audio
from modules.config_utils import safe_filename

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
_input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
OUTPUT_DIR = PROJECT_DIR / _input_cfg.get("path_output_dir", "output")
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "s1w_tencentmeeting.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def parse_date_from_video_url(video_url: str) -> str:
    """从视频URL中提取日期前缀，如 TM-20260310183436 → '260310-'"""
    if not video_url:
        return ""
    m = re.search(r'TM-(\d{4})(\d{2})(\d{2})', video_url)
    if m:
        y, mo, d = m.groups()
        return f"{y[2:]}{mo}{d}-"
    return ""


def extract_sharing_id(url: str) -> str:
    """从 URL 提取 sharing_id（短代码）"""
    m = re.search(r'/c(?:w|rm)/([A-Za-z0-9]+)', url)
    if not m:
        raise ValueError(f"无法从 URL 提取 sharing_id: {url}")
    return m.group(1)


def fetch_meeting_page(sharing_id: str, cookie: str = "") -> tuple:
    """使用浏览器自动化获取 recording_id、视频URL、cookies、标题、日期"""
    url = f"https://meeting.tencent.com/cw/{sharing_id}"

    user_data_dir = PROJECT_DIR / ".browser_data"
    user_data_dir.mkdir(exist_ok=True)

    # 用于收集API请求
    api_requests = []

    def log_request(request):
        if 'wemeet-tapi' in request.url or 'record-detail' in request.url:
            api_requests.append({
                'url': request.url,
                'method': request.method,
            })

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,
            args=['--start-maximized'],
        )

        # 如果提供了cookie，添加到浏览器上下文
        if cookie:
            cookies = []
            for item in cookie.split(';'):
                item = item.strip()
                if '=' in item:
                    name, value = item.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.meeting.tencent.com',
                        'path': '/'
                    })
            if cookies:
                browser.add_cookies(cookies)
                log.info(f"已添加 {len(cookies)} 个 cookie")

        page = browser.pages[0] if browser.pages else browser.new_page()

        # 监听网络请求
        page.on("request", log_request)

        log.info(f"正在访问: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # 检查是否需要登录
        page_text = page.inner_text('body')
        if "扫码登录" in page_text or "其他登录方式" in page_text:
            log.warning("检测到登录页面，等待15秒供您完成登录...")
            log.warning("请在浏览器窗口中扫码或使用其他方式登录")
            time.sleep(15)

            # 检查是否登录成功
            page_text = page.inner_text('body')
            if "扫码登录" in page_text:
                raise RuntimeError("登录超时或失败，请重新运行程序")
            log.info("登录成功！")

        # 等待页面完全加载
        time.sleep(5)

        # 保存页面HTML用于提取ID
        page_html = page.content()
        log.info(f"已保存页面HTML: {len(page_html)} 字符")

        # 提取页面渲染后的文本内容（用于调试和fallback）
        page_text = page.inner_text('body')
        log.info(f"页面文本内容（前500字符）:\n{page_text[:500]}")

        # 从页面文本中提取标题和日期
        page_title = ""
        page_date_prefix = ""
        lines = page_text.split('\n')
        log.info(f"页面共有 {len(lines)} 行")

        for i, line in enumerate(lines[:30]):
            line = line.strip()
            if i < 10:  # 打印前10行用于调试
                log.info(f"第{i}行: [{line}]")

            # 提取日期
            date_match = re.match(r'(\d{4})/(\d{2})/(\d{2})', line)
            if date_match and not page_date_prefix:
                year, month, day = date_match.groups()
                page_date_prefix = f"{year[2:]}{month}{day}-"
                log.info(f"从页面提取日期: {page_date_prefix}")
                # 日期的前一行是标题
                if i > 0:
                    prev_line = lines[i-1].strip()
                    log.info(f"日期前一行: [{prev_line}]")
                    if prev_line and prev_line not in ['返回', '分享', '另存为', '翻译', '时间轴', '纪要']:
                        page_title = prev_line
                        log.info(f"从页面提取标题: {page_title}")
                break

        # 尝试从 DOM 元素获取标题
        try:
            title_elem = page.locator('h1, h2, .title, [class*="title"]').first
            if title_elem:
                title_text = title_elem.inner_text().strip()
                if title_text and title_text not in ['返回', '分享', '另存为']:
                    page_title = title_text
                    log.info(f"从DOM元素提取标题: {page_title}")
        except Exception as e:
            log.info(f"未找到标题元素: {e}")

        # 打印拦截到的API请求
        log.info(f"拦截到 {len(api_requests)} 个API请求:")
        for req in api_requests[:10]:  # 只打印前10个
            log.info(f"  {req['method']} {req['url']}")


        # 检查页面是否完整加载
        if len(page_html) < 300000:
            log.warning(f"页面HTML过小({len(page_html)}字符)，可能未完全加载")
            page_text = page.inner_text('body')[:500]
            if "登录" in page_text or "扫码" in page_text:
                log.error("页面需要登录，请检查cookie")
                browser.close()
                raise RuntimeError("页面需要登录，请更新credentials.yaml中的cookie")

        # 1. 提取cookies（后续API调用必需）
        cookies_dict = {c['name']: c['value'] for c in browser.cookies()}
        log.info(f"已提取 {len(cookies_dict)} 个 cookie")

        # 2. 提取 recording_id（用于API获取逐字稿、纪要）
        log.info("从页面提取关键ID...")
        long_ids = re.findall(r'\b(\d{19})\b', page_html)
        recording_ids = [i for i in long_ids if i.startswith('2')]

        recording_id_num = max(recording_ids) if recording_ids else ""

        log.info(f"提取到: recording_id={recording_id_num}")

        # 3. 提取视频URL
        video_url = None
        try:
            log.info("提取视频URL...")
            video_element = page.locator('video').first
            if video_element:
                video_url = video_element.get_attribute('src')
                if not video_url:
                    source = page.locator('video source').first
                    if source:
                        video_url = source.get_attribute('src')
                if video_url:
                    log.info(f"视频URL: {video_url[:100]}...")
        except Exception as e:
            log.warning(f"提取视频URL失败: {e}")

        browser.close()
        return recording_id_num, video_url, cookies_dict, page_title, page_date_prefix, page_text


def fetch_meeting_data_via_api(recording_id: str, cookies: dict) -> dict:
    """通过API获取会议数据（时间轴、纪要）"""
    import time
    import random
    import string

    def make_params():
        nonce = ''.join(random.choices(string.ascii_letters + string.digits, k=9))
        return {
            "c_app_id": "",
            "c_os_model": "web",
            "c_os": "web",
            "c_os_version": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "c_timestamp": str(int(time.time() * 1000)),
            "c_nonce": nonce,
            "c_app_version": "",
            "c_instance_id": "5",
            "rnds": nonce,
            "c_district": "0",
            "platform": "Web",
            "c_app_uid": "",
            "c_account_corp_id": "563556228",
            "c_lang": "zh-CN",
        }

    # API headers
    api_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://meeting.tencent.com",
        "Referer": "https://meeting.tencent.com/",
    }

    result = {}

    # 获取时间轴 - query-timeline API (POST)
    try:
        url = "https://meeting.tencent.com/wemeet-tapi/v2/meetlog/public/record-detail/query-timeline"
        body = {
            "record_id": recording_id,
            "pwd_token": "",
            "activity_uid": "",
            "lang": "zh",
        }
        resp = requests.post(url, params=make_params(), headers=api_headers, json=body, cookies=cookies, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            log.info(f"时间轴API响应: code={data.get('code')}, msg={data.get('msg', '')}")
            if data.get('code') == 0:
                # 实际路径: data.timeline_info.timeline_infos[]
                tl_infos = data.get('data', {}).get('timeline_info', {}).get('timeline_infos', [])
                timeline = []
                for item in tl_infos:
                    if isinstance(item, dict):
                        start_sec = item.get('start_time', 0)  # 秒数
                        h, rem = divmod(int(start_sec), 3600)
                        m, s = divmod(rem, 60)
                        time_str = f"{h:02d}:{m:02d}:{s:02d}"
                        desc = item.get('content', '')
                        if desc:
                            timeline.append((time_str, desc))
                result['timeline'] = timeline
                log.info(f"API获取时间轴成功: {len(timeline)} 条")
            else:
                result['timeline'] = []
        else:
            result['timeline'] = []
    except Exception as e:
        log.warning(f"时间轴API调用失败: {e}")
        result['timeline'] = []

    # 获取纪要 - query-summary-and-note API (POST)
    try:
        url = "https://meeting.tencent.com/wemeet-tapi/v2/meetlog/public/record-detail/query-summary-and-note"
        body = {
            "record_id": recording_id,
            "pwd_token": "",
            "activity_uid": "",
            "lang": "zh",
            "template_id": ""
        }
        resp = requests.post(url, params=make_params(), headers=api_headers, json=body, cookies=cookies, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            log.info(f"纪要API响应: code={data.get('code')}, msg={data.get('msg', '')}")
            if data.get('code') == 0:
                info = data.get('data', {})
                summary = _parse_api_summary(info)
                result['summary'] = summary
                log.info(f"API获取纪要: {len(summary)}字")
            else:
                result['summary'] = ''
        else:
            result['summary'] = ''
    except Exception as e:
        log.warning(f"纪要API调用失败: {e}")
        result['summary'] = ''

    return result


def _parse_api_summary(info: dict) -> str:
    """从 query-summary-and-note API 响应中提取结构化纪要

    数据路径: data.deepseek_summary.topic_summary
      - begin_summary: 总结段落
      - sub_points[].sub_point_title / sub_point_vec_items[].point
    待办路径: data.todo.todo_list[].todo_name
    """
    parts = []

    # 1. 解析纪要主体
    ds = info.get('deepseek_summary') or {}
    ts = ds.get('topic_summary') or {}

    begin = (ts.get('begin_summary') or '').strip()
    if begin:
        parts.append(begin)

    for sp in ts.get('sub_points') or []:
        title = (sp.get('sub_point_title') or '').strip()
        if title:
            parts.append(f'\n**{title}**')
        for item in sp.get('sub_point_vec_items') or []:
            point = (item.get('point') or '').strip()
            if point:
                parts.append(f'- {point}')

    end = (ts.get('end_summary') or '').strip()
    if end:
        parts.append(f'\n{end}')

    # 2. 解析会议待办
    todo_list = (info.get('todo') or {}).get('todo_list') or []
    if todo_list:
        parts.append('\n**会议待办**')
        for item in todo_list:
            name = (item.get('todo_name') or '').strip()
            if name:
                parts.append(f'- {name}')

    return '\n'.join(parts)


def fetch_transcript_via_api(recording_id: str, cookies: dict) -> dict:
    """通过API获取逐字稿数据"""
    api_url = "https://meeting.tencent.com/wemeet-cloudrecording-webapi/v1/minutes/detail"
    params = {
        "recording_id": recording_id,
        "lang": "zh",
        "pid": "0",
        "minutes_version": "0",
        "return_ori_minutes_translating": "1",
        "return_ori": "0",
    }
    try:
        resp = requests.get(api_url, params=params, cookies=cookies, timeout=30)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.warning(f"API获取逐字稿失败: {e}")
    return {}


def parse_transcript_to_srt(transcript_lines) -> str:
    """将逐字稿转换为 SRT 格式"""
    srt_lines = []
    for i, (time_str, text) in enumerate(transcript_lines, 1):
        parts = time_str.split(':')
        start_time = f"00:{parts[0]}:{parts[1]},000"

        if i < len(transcript_lines):
            next_parts = transcript_lines[i][0].split(':')
            end_time = f"00:{next_parts[0]}:{next_parts[1]},000"
        else:
            mm, ss = int(parts[0]), int(parts[1]) + 5
            if ss >= 60:
                mm += 1
                ss -= 60
            end_time = f"00:{mm:02d}:{ss:02d},000"

        srt_lines.append(f"{i}\n{start_time} --> {end_time}\n{text}\n")
    return "\n".join(srt_lines)


def parse_timeline_from_page_text(page_text: str) -> list:
    """从页面文本中解析时间轴

    页面文本格式示例：
    时间轴
    00:00:00

    [内容]

    00:04:13

    [内容]
    """
    timeline = []
    lines = page_text.split('\n')

    # 查找"时间轴"部分的起始位置
    timeline_start = -1
    for i, line in enumerate(lines):
        if '时间轴' in line and i > 2:  # 确保不是标题中的"时间轴"
            timeline_start = i
            break

    if timeline_start == -1:
        log.info("页面文本中未找到'时间轴'标记")
        return []

    # 时间轴的结束标记（遇到这些内容时停止解析）
    end_markers = ['模版', '视频播放器', '会议待办', '分发言人', '生成会议', '内容由 AI']

    # 在"时间轴"标记之后查找时间戳
    time_pattern = re.compile(r'^(\d{2}:\d{2}:\d{2})$')
    current_time = None
    current_content = []
    collecting_content = False

    for i in range(timeline_start + 1, len(lines)):
        line = lines[i].strip()

        # 检查是否到达结束标记
        if any(marker in line for marker in end_markers):
            # 保存之前收集的内容
            if current_time and current_content:
                content = ' '.join(current_content)
                if content:
                    timeline.append((current_time, content))
            break

        # 单独检查"纪要"（精确匹配），它是时间轴结束后下一区块的标记
        if line == '纪要' and collecting_content:
            # 保存之前收集的内容并停止
            if current_time and current_content:
                content = ' '.join(current_content)
                if content:
                    timeline.append((current_time, content))
            break

        time_match = time_pattern.match(line)

        if time_match:
            # 保存之前收集的内容
            if current_time and current_content:
                content = ' '.join(current_content)
                if content:
                    timeline.append((current_time, content))

            # 开始新的时间点
            current_time = time_match.group(1)
            current_content = []
            collecting_content = True
        elif collecting_content and line:
            # 收集时间戳之后的内容
            # 跳过空行和标题行
            if line not in ['返回', '分享', '另存为', '翻译', '时间轴', '纪要', '会议纪要']:
                current_content.append(line)

    # 保存最后一条
    if current_time and current_content:
        content = ' '.join(current_content)
        if content:
            timeline.append((current_time, content))

    # 去重：移除时间戳和内容都相同的连续重复项
    deduplicated = []
    for entry in timeline:
        if not deduplicated or entry[0] != deduplicated[-1][0] or entry[1] != deduplicated[-1][1]:
            deduplicated.append(entry)
    timeline = deduplicated

    log.info(f"从页面文本解析到 {len(timeline)} 条时间轴")
    return timeline


def parse_summary_from_page_text(page_text: str) -> str:
    """从页面文本中解析纪要内容

    纪要内容通常在页面底部的"模版："区域
    """
    lines = page_text.split('\n')

    # 纪要的结束标记
    end_keywords = ['内容由', 'AI生成', '视频播放器', '逐字稿加载', '纪要']

    # 查找"模版："区域（纪要内容所在位置）
    template_start = -1
    for i, line in enumerate(lines):
        if '模版：' in line:
            template_start = i
            break

    if template_start == -1:
        log.info("页面文本中未找到'模版：'内容")
        return ''

    # 跳过"模版："之后的第1-2行（"主题摘要"、"会议总结"是模板头部，不是正文）
    skip_count = 0

    # 提取纪要内容（从"模版："之后的内容到结束标记），保留段落层次
    numbered_heading = re.compile(r'^(\d+)、')  # 匹配 "1、xxx" 编号标题
    raw_lines = []  # 保留原始行（含空行）
    for i in range(template_start + 1, len(lines)):
        line = lines[i].strip()

        # 遇到包含结束关键字的行停止
        if any(keyword in line for keyword in end_keywords):
            break

        # 跳过模板头部（第1行"主题摘要"，第2行"会议总结"）
        if skip_count < 2:
            skip_count += 1
            continue

        raw_lines.append(line)

    # 格式化为带层次结构的 Markdown
    summary_parts = []
    for line in raw_lines:
        if not line:
            # 空行保留为段落分隔
            summary_parts.append('')
        elif numbered_heading.match(line):
            # 编号标题：加空行 + 加粗
            summary_parts.append('')
            summary_parts.append(f'**{line}**')
        elif line == '会议待办':
            # 会议待办：作为独立小节
            summary_parts.append('')
            summary_parts.append(f'**{line}**')
        else:
            # 编号标题下的子项 → 列表项；总结段落 → 原样保留
            if summary_parts and summary_parts[-1].startswith('**'):
                summary_parts.append(f'- {line}')
            elif summary_parts and summary_parts[-1].startswith('- '):
                summary_parts.append(f'- {line}')
            else:
                summary_parts.append(line)

    # 合并，去除首尾多余空行
    summary = '\n'.join(summary_parts).strip()

    if summary:
        log.info(f"从页面文本解析到纪要: {len(summary)}字")
    else:
        log.info("页面文本中纪要部分没有实际内容")

    return summary


def generate_abs_md(summary: str, timeline: list) -> str:
    """生成 abs.md 内容（纪要 + 时间轴）"""
    lines = ["# 会议纪要\n", summary, "\n\n# 时间轴\n"]
    for time_str, desc in timeline:
        lines.append(f"- {time_str} {desc}")
    return "\n".join(lines)


def download_video(video_url: str, output_path: Path, cookies: dict):
    """下载视频文件"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://meeting.tencent.com/'
    }
    try:
        log.info(f"开始下载: {output_path.name}")
        resp = requests.get(video_url, headers=headers, cookies=cookies, stream=True, timeout=60)
        if resp.status_code == 200:
            total = int(resp.headers.get('content-length', 0))
            with open(output_path, 'wb') as f:
                downloaded = 0
                last_time = time.time()
                for chunk in resp.iter_content(chunk_size=1024*1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        current_time = time.time()
                        # 每2秒输出一次进度
                        if current_time - last_time >= 2:
                            percent = int(downloaded/total*100)
                            print(f"\r下载进度: {percent}%", end='', flush=True)
                            last_time = current_time
            print()  # 换行
            log.info(f"下载完成: {output_path}")
            return True
        else:
            log.warning(f"下载失败: HTTP {resp.status_code}")
            return False
    except Exception as e:
        log.error(f"下载异常: {e}")
        return False


def process_tencent_meeting(task: dict, config: dict, creds: dict):
    """处理腾讯会议回放任务"""
    source_url = task.get("source_huifang_url", "")
    user_title = config.get("titleShougong", "") or task.get("title", "")

    log.info(f"开始处理腾讯会议: {source_url}")

    try:
        sharing_id = extract_sharing_id(source_url)
        log.info(f"Sharing ID: {sharing_id}")

        cookie = creds.get("tencent_meeting", {}).get("cookie", "")
        recording_id_num, video_url, cookies, page_title, page_date_prefix, page_text = fetch_meeting_page(sharing_id, cookie)

        log.info("浏览器已关闭，开始通过API获取会议数据...")

        # 通过API获取会议数据（纪要、时间轴）
        api_data = fetch_meeting_data_via_api(recording_id_num, cookies)

        # 调试：打印关键变量
        log.info(f"  标题: user=[{user_title}], page=[{page_title}]")
        log.info(f"  日期: video_url=[{parse_date_from_video_url(video_url)}], page=[{page_date_prefix}]")
        log.info(f"  API: timeline={len(api_data.get('timeline', []))}条, summary={len(api_data.get('summary', ''))}字")

        # 通过API获取逐字稿
        transcript = []
        transcript_data = fetch_transcript_via_api(recording_id_num, cookies)
        if transcript_data and 'minutes' in transcript_data:
            minutes = transcript_data['minutes']
            if 'paragraphs' in minutes:
                for para in minutes['paragraphs']:
                    if 'sentences' in para:
                        for sent in para['sentences']:
                            if 'words' in sent:
                                for word in sent['words']:
                                    if 'start_time' in word and 'text' in word:
                                        start_ms = word['start_time']
                                        end_ms = word.get('end_time', start_ms + 3000)
                                        ss_start, ms_start = divmod(start_ms, 1000)
                                        mm_start, ss_start = divmod(ss_start, 60)
                                        hh_start, mm_start = divmod(mm_start, 60)
                                        ss_end, ms_end = divmod(end_ms, 1000)
                                        mm_end, ss_end = divmod(ss_end, 60)
                                        hh_end, mm_end = divmod(mm_end, 60)
                                        time_str = f"{hh_start:02d}:{mm_start:02d}:{ss_start:02d},{ms_start:03d} --> {hh_end:02d}:{mm_end:02d}:{ss_end:02d},{ms_end:03d}"
                                        transcript.append((time_str, word['text']))
                log.info(f"从API提取到 {len(transcript)} 条逐字稿")

        # 汇总最终数据
        # 标题优先级：用户指定 > 页面解析 > sharing_id
        title = user_title or page_title or sharing_id
        # 日期优先级：视频URL提取 > 页面文本解析
        date_prefix = parse_date_from_video_url(video_url) or page_date_prefix
        summary = api_data.get('summary', '')
        timeline = api_data.get('timeline', [])

        # 如果API返回的时间轴为空，尝试从页面文本解析
        # if not timeline and page_text:
        #     log.info("API时间轴为空，从页面文本解析...")
        #     timeline = parse_timeline_from_page_text(page_text)

        # 如果API返回的纪要为空，尝试从页面文本解析
        # if not summary and page_text:
        #     log.info("API纪要为空，尝试从页面文本解析...")
        #     summary = parse_summary_from_page_text(page_text)

        safe_title = safe_filename(title)
        filename = f"{date_prefix}{safe_title}"
        log.info(f"最终文件名: {filename}")

        # 文本数据优先：先生成字幕和摘要
        # 生成 ori.srt
        if transcript:
            # 如果是API数据，time_str已经是完整的SRT格式
            if transcript and '-->' in transcript[0][0]:
                srt_lines = []
                for i, (time_str, text) in enumerate(transcript, 1):
                    srt_lines.append(f"{i}\n{time_str}\n{text}\n")
                srt_content = "\n".join(srt_lines)
            else:
                # 如果是页面解析的数据，使用原有函数
                srt_content = parse_transcript_to_srt(transcript)
            srt_path = OUTPUT_DIR / f"{filename}_ori.srt"
            srt_path.write_text(srt_content, encoding="utf-8")
            log.info(f"已生成字幕: {srt_path}")

        # 生成 abs.md
        if summary or timeline:
            abs_content = generate_abs_md(summary, timeline)
            abs_path = OUTPUT_DIR / f"{filename}_abs.md"
            abs_path.write_text(abs_content, encoding="utf-8")
            log.info(f"已生成摘要: {abs_path}")

        # 大文件最后：下载视频
        if video_url:
            video_path = OUTPUT_DIR / f"{filename}.mp4"
            download_video(video_url, video_path, cookies)

            # 从视频中提取音频
            audio_path = OUTPUT_DIR / f"{filename}.mp3"
            extract_audio(video_path, audio_path)

        log.info("腾讯会议处理完成")

    except Exception as e:
        log.error(f"处理失败: {e}", exc_info=True)
        raise
