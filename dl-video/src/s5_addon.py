# 参数调用示例:
#   python src/s5_addon.py --subtitle <file_fix.srt>
#   python src/s5_addon.py --subtitle output/wx_demo.qwen3_asr.srt --no-transcript --subtitle-only --no-prefer-fix
#   python src/s5_addon.py --subtitle <file_fix.srt> --transcript <wiki.md> --discussion <chat.xlsx>
#   python src/s5_addon.py --subtitle output/demo.qwen3_asr.srt --no-prefer-fix --transcript output/demo_wiki.md
#   python src/s5_addon.py --subtitle <file_fix.srt> --subtitle-only
#   python src/s5_addon.py --subtitle <file_fix.srt> --discussion <chat.xlsx> --discussion-only
#   python src/s5_addon.py --subtitle <file_fix.srt> --no-digest
#   python src/s5_addon.py --subtitle <file_fix.srt> --provider minimax
#   python src/s5_addon.py --subtitle <file_fix.srt> --dry-run
"""Step5: 逐字稿信息拓展 — 从字幕和讨论区中挖掘逐字稿遗漏的信息

用法:
  # 完整运行（字幕分析 + 讨论区分析 + 精华摘要）
  python src/s5_addon.py --subtitle output/demo_fix.srt

  # 指定逐字稿和讨论区
  python src/s5_addon.py --subtitle output/demo_fix.srt --transcript output/demo.md --discussion output/demo_讨论.xlsx

  # 直接使用 Qwen3-ASR 原始字幕，不自动切换到同名 _fix.srt
  python src/s5_addon.py --subtitle output/demo.qwen3_asr.srt --no-prefer-fix --transcript output/demo_wiki.md

  # 逐字稿支持飞书 URL
  python src/s5_addon.py --subtitle output/demo_fix.srt --transcript https://xxx.feishu.cn/wiki/xxx

  # 仅分析字幕（跳过讨论区）
  python src/s5_addon.py --subtitle output/demo_fix.srt --subtitle-only

  # 仅分析讨论区（跳过字幕）
  python src/s5_addon.py --subtitle output/demo_fix.srt --discussion output/demo_讨论.xlsx --discussion-only

  # 跳过精华摘要生成
  python src/s5_addon.py --subtitle output/demo_fix.srt --no-digest

  # 覆盖 LLM 提供商
  python src/s5_addon.py --subtitle output/demo_fix.srt --provider minimax

  # 试运行（只解析文件，不调用 LLM）
  python src/s5_addon.py --subtitle output/demo_fix.srt --dry-run

参数:
  --subtitle PATH     字幕文件路径（支持相对/绝对路径）
  --no-prefer-fix     直接使用指定字幕，不自动切换到同名 _fix.srt
  --no-transcript     无 Wiki 模式，直接从字幕提取要点与摘要
  --transcript PATH   逐字稿/教学文档路径或飞书 URL（可选）
  --discussion PATH   讨论区 .xlsx 文件路径（可选）
  --subtitle-only     仅分析字幕（跳过讨论区）
  --discussion-only   仅分析讨论区（跳过字幕）
  --no-digest         跳过精华摘要生成
  --provider NAME     覆盖配置中的 LLM 提供商（如 minimax / volcengine / dashscope / deepseek）
  --dry-run           试运行：只解析文件，不调用 LLM
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path

import requests
import yaml
from openpyxl import load_workbook

from modules.config_utils import load_config
from modules.progress_utils import InlineProgress

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
PROMPT_DIR = PROJECT_DIR / "prompt"
_input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
OUTPUT_DIR = PROJECT_DIR / _input_cfg.get("path_output_dir", "output")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "addon.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 文件解析
# ---------------------------------------------------------------------------

def parse_srt(filepath):
    """解析 SRT 字幕文件，返回 [(序号, 开始时间, 结束时间, 文本), ...]"""
    text = Path(filepath).read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", text.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        seq = lines[0].strip()
        time_line = lines[1].strip()
        content = "\n".join(lines[2:]).strip()
        m = re.match(r"(\d[\d:,]+)\s*-->\s*(\d[\d:,]+)", time_line)
        if m:
            entries.append((int(seq), m.group(1), m.group(2), content))
    log.info(f"字幕解析完成: {len(entries)} 条, 来源: {filepath}")
    return entries


def srt_to_text(entries):
    """将 SRT 条目合并为纯文本（保留时间标记便于溯源）"""
    chunks = []
    for seq, start, end, text in entries:
        chunks.append(f"[{start}] {text}")
    return "\n".join(chunks)


def parse_discussion_xlsx(filepath):
    """解析讨论区 xlsx，返回 [{时间, 发言人, 内容, 标签...}, ...]"""
    wb = load_workbook(filepath, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    wb.close()
    if not rows:
        return []
    headers = [str(h) if h else f"col_{i}" for i, h in enumerate(rows[0])]
    result = []
    for row in rows[1:]:
        item = {}
        for i, val in enumerate(row):
            if i < len(headers):
                item[headers[i]] = str(val) if val else ""
        # 跳过空内容
        content = item.get("发言内容", "").strip()
        if content:
            result.append(item)
    log.info(f"讨论区解析完成: {len(result)} 条有效发言, 来源: {filepath}")
    return result


def parse_transcript(filepath):
    """解析逐字稿文件（md/txt），按章节拆分。
    返回 [(章节标题, 章节内容), ...]"""
    text = Path(filepath).read_text(encoding="utf-8")
    # 按 markdown 标题拆分
    sections = []
    current_title = "(开头)"
    current_lines = []
    for line in text.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = m.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))
    log.info(f"逐字稿解析完成: {len(sections)} 个章节, 来源: {filepath}")
    return sections


# ---------------------------------------------------------------------------
# LLM 调用层（OpenAI 兼容接口，支持 minimax/volcengine/dashscope/deepseek）
# ---------------------------------------------------------------------------

S5_SUBTITLE_RESPONSE_SCHEMA = {
    "name": "subtitle_additions",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "chapter": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": ["观点", "金句", "数字细节", "扩展案例", "方法论框架", "互动问答", "推荐资源"],
                        },
                        "time": {"type": "string"},
                        "content": {"type": "string"},
                        "context": {"type": "string"},
                        "correction": {"type": "string"},
                    },
                    "required": ["chapter", "category", "time", "content", "context", "correction"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}

S5_DISCUSSION_RESPONSE_SCHEMA = {
    "name": "discussion_insights",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "chapter": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": ["独立观点", "补充信息", "有价值问答", "推荐资源", "实践经验", "热议话题"],
                        },
                        "time": {"type": "string"},
                        "speaker": {"type": "string"},
                        "tags": {"type": "string"},
                        "content": {"type": "string"},
                        "thread": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "time": {"type": "string"},
                                    "speaker": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["time", "speaker", "content"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["chapter", "category", "time", "speaker", "tags", "content", "thread"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}


class LLMResponseError(RuntimeError):
    """LLM 返回了 HTTP 成功但不可用的业务响应。"""


class LLMClient:
    """统一的 LLM 调用客户端，基于 OpenAI 兼容的 chat/completions 接口"""

    def __init__(self, config, creds):
        provider = config["llm_plan"]["current_s5"]["name"]
        llm_plan = config["llm_plan"][provider]
        self.model = llm_plan["model"]
        self.base_url = llm_plan["base_url"].rstrip("/")
        self.max_tokens = llm_plan.get("max_tokens", 8192)
        self.temperature = config["llm_plan"]["current_s5"].get("temperature", 0.3)
        self.thinking = llm_plan.get("thinking", "")
        self.structured_output = llm_plan.get("structured_output", False)
        self.provider = provider

        # 支持 credential_source 映射：当 provider 在 credentials 中无对应条目时，
        # 从 credential_source 指定的 credentials 分组获取 api_key
        cred_source = llm_plan.get("credential_source", provider)
        cred_block = creds.get(cred_source, {})
        self.api_key = cred_block.get("api_key", "")
        if not self.api_key:
            raise ValueError(f"未配置 {cred_source} 的 api_key（provider={provider}）")

        # 仅旧按量付费 volcengine 配置使用 endpoint_id；Agent Plan 直接使用模型名。
        if provider == "volcengine":
            ep_id = cred_block.get("endpoint_id", "")
            if ep_id:
                self.model = ep_id

        # 统计
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        log.info(f"LLM 客户端初始化: provider={provider}, model={self.model}")

    def chat(self, system_prompt, user_prompt, temperature=None, response_schema=None):
        """发送 chat 请求，返回助手回复文本"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if self.thinking:
            payload["thinking"] = {"type": self.thinking}
        if response_schema and self.structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": response_schema,
            }

        for attempt in range(6):
            wait = 10 * (2 ** min(attempt, 5))
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=300)
                resp.raise_for_status()
                data = resp.json()
                # 统计 token
                usage = data.get("usage", {})
                self.total_calls += 1
                self.total_input_tokens += usage.get("prompt_tokens", 0)
                self.total_output_tokens += usage.get("completion_tokens", 0)
                choice = data["choices"][0]
                finish_reason = choice.get("finish_reason", "")
                content = choice.get("message", {}).get("content") or ""
                if finish_reason == "length":
                    raise LLMResponseError(
                        f"LLM 输出达到上限（max_tokens={self.max_tokens}）"
                    )
                if not content.strip():
                    raise LLMResponseError(
                        f"LLM 返回空 content（finish_reason={finish_reason or 'unknown'}）"
                    )
                return content
            except requests.exceptions.HTTPError as e:
                if resp.status_code == 429:
                    log.warning(f"速率限制，等待 {wait}s 后重试...")
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    log.warning(f"服务端错误 {resp.status_code}，等待 {wait}s 后重试...")
                    time.sleep(wait)
                    continue
                log.error(f"LLM 调用失败: {e}, 响应: {resp.text}")
                raise
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                remaining = 6 - attempt - 1
                log.warning(f"LLM 调用超时/连接错误，等待 {wait}s 后重试 ({attempt+1}/6, {remaining} 次剩余): {e}")
                time.sleep(wait)
                continue
            except LLMResponseError:
                raise
            except Exception as e:
                log.error(f"LLM 调用异常: {e}")
                raise
        raise RuntimeError("LLM 调用重试次数耗尽")

    def report_usage(self):
        """输出 token 用量统计"""
        log.info(
            f"LLM 用量统计: provider={self.provider}, model={self.model}, "
            f"调用 {self.total_calls} 次, "
            f"输入 {self.total_input_tokens} tokens, "
            f"输出 {self.total_output_tokens} tokens"
        )


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

def load_prompt(filename):
    """从 prompt 目录加载提示词文件"""
    prompt_path = PROMPT_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件不存在: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# JSON 解析与结果处理
# ---------------------------------------------------------------------------

def parse_llm_json(reply):
    """从 LLM 回复中提取 items；无效响应必须抛错。"""
    if not reply or not reply.strip():
        raise LLMResponseError("LLM 返回空文本")
    text = reply.strip()
    # 尝试提取 code fence 中的 JSON
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        # 兼容旧模型返回的顶层数组。
        if isinstance(data, list):
            return data
        raise LLMResponseError("LLM JSON 缺少 items 数组")
    except json.JSONDecodeError as e:
        log.error(f"JSON 解析失败: {e}")
        # 保存原始回复到日志目录便于排查
        dump_path = LOG_DIR / f"llm_raw_{time.time_ns()}.txt"
        dump_path.write_text(reply, encoding="utf-8")
        log.error(f"原始回复已保存: {dump_path}")
        raise LLMResponseError(f"LLM JSON 解析失败: {e}") from e


def request_json_items(client, system_prompt, user_prompt, response_schema):
    """请求结构化结果；业务响应错误额外重试一次。"""
    for attempt in range(2):
        try:
            reply = client.chat(
                system_prompt,
                user_prompt,
                response_schema=response_schema,
            )
            return parse_llm_json(reply)
        except LLMResponseError as e:
            if attempt == 0:
                log.warning(f"LLM 结构化响应无效，立即重试一次: {e}")
                continue
            raise


def merge_results(subtitle_items, discussion_items, sections):
    """按章节合并字幕和讨论区提取结果。
    sections 中可能有同名章节，用索引区分。
    返回 (merged_list, stats)：
    - merged_list: [(章节标题, {"subtitle": [...], "discussion": [...]}), ...]
      与 sections 一一对应
    - stats: 统计数据 dict
    """
    # 初始化：与 sections 一一对应
    merged = [{"subtitle": [], "discussion": []} for _ in sections]

    def add_item(item, source):
        chapter = item.get("chapter", "")
        idx = _match_chapter_idx(chapter, sections)
        merged[idx][source].append(item)

    for item in subtitle_items:
        add_item(item, "subtitle")
    for item in discussion_items:
        add_item(item, "discussion")

    # 去重：同一章节同一来源内，内容高度相似的条目只保留第一条
    dedup_count = 0
    for data in merged:
        for source in ("subtitle", "discussion"):
            items = data[source]
            if len(items) <= 1:
                continue
            unique = []
            for item in items:
                content = item.get("content", "")
                if not any(_is_similar(content, u.get("content", "")) for u in unique):
                    unique.append(item)
                else:
                    dedup_count += 1
            data[source] = unique
    if dedup_count:
        log.info(f"去重移除 {dedup_count} 条重复信息")

    # 统计
    sub_cats = {}
    disc_cats = {}
    for data in merged:
        for it in data["subtitle"]:
            cat = it.get("category", "其他")
            sub_cats[cat] = sub_cats.get(cat, 0) + 1
        for it in data["discussion"]:
            cat = it.get("category", "其他")
            disc_cats[cat] = disc_cats.get(cat, 0) + 1

    total_sub = sum(sub_cats.values())
    total_disc = sum(disc_cats.values())
    # 信息密度最高的章节（同名章节各自独立统计）
    density = []
    for i, (title, _) in enumerate(sections):
        n = len(merged[i]["subtitle"]) + len(merged[i]["discussion"])
        if n > 0:
            density.append((i, title, n))
    density.sort(key=lambda x: x[2], reverse=True)

    stats = {
        "total_subtitle": total_sub,
        "total_discussion": total_disc,
        "subtitle_categories": sub_cats,
        "discussion_categories": disc_cats,
        "top_chapters": [(t, n) for _, t, n in density[:5]],
    }
    return merged, stats


def _match_chapter_idx(chapter, sections):
    """模糊匹配章节，返回 sections 中的索引。
    精确匹配 > 包含匹配 > 归入第一个章节。"""
    if not chapter:
        return 0
    # 精确匹配
    for i, (title, _) in enumerate(sections):
        if chapter == title:
            return i
    # 包含匹配
    for i, (title, _) in enumerate(sections):
        if chapter in title or title in chapter:
            return i
    return 0


def _is_similar(a, b, threshold=0.6):
    """判断两段文本是否高度相似（基于字符集合的 Jaccard 相似度）。
    适用于检测 LLM 跨段重复输出的近似内容。"""
    if not a or not b:
        return False
    # 按字符 bigram 计算
    def bigrams(s):
        s = s.replace(" ", "")
        return set(s[i:i+2] for i in range(len(s) - 1)) if len(s) > 1 else {s}
    sa, sb = bigrams(a), bigrams(b)
    if not sa or not sb:
        return False
    intersection = len(sa & sb)
    union = len(sa | sb)
    return (intersection / union) >= threshold


def render_full_report(merged, stats, sections, prefix, output_path, no_transcript=False):
    """渲染完整版信息拓展 markdown 报告"""
    lines = []
    lines.append(f"# {prefix} — 信息拓展\n")
    subtitle_label = "字幕要点" if no_transcript else "字幕补充"
    if no_transcript:
        lines.append("> 基于视频字幕整理，未使用独立 Wiki 文稿。\n")

    # 摘要仪表盘
    lines.append("## 摘要统计\n")
    total = stats["total_subtitle"] + stats["total_discussion"]
    lines.append(f"- 提取信息总计：**{total}** 条")
    lines.append(f"  - {subtitle_label}：{stats['total_subtitle']} 条")
    lines.append(f"  - 讨论区精华：{stats['total_discussion']} 条")
    if stats["subtitle_categories"]:
        cats = "、".join(f"{k}({v})" for k, v in stats["subtitle_categories"].items())
        lines.append(f"  - 字幕分类：{cats}")
    if stats["discussion_categories"]:
        cats = "、".join(f"{k}({v})" for k, v in stats["discussion_categories"].items())
        lines.append(f"  - 讨论区分类：{cats}")
    if stats["top_chapters"]:
        top = "、".join(f"{t}({n}条)" for t, n in stats["top_chapters"][:3])
        lines.append(f"  - 信息密度最高：{top}")
    if total == 0:
        empty_label = "信息" if no_transcript else "增量信息"
        lines.append(f"\n> 所有输入分段均处理成功，但未发现可提取的{empty_label}。")
    lines.append("")

    # 按章节输出
    for i, (title, _) in enumerate(sections):
        data = merged[i]
        sub_items = data["subtitle"]
        disc_items = data["discussion"]
        if not sub_items and not disc_items:
            continue

        lines.append(f"## {title}\n")

        if sub_items:
            lines.append(f"### {subtitle_label}\n")
            _render_subtitle_items(sub_items, lines)
            lines.append("")

        if disc_items:
            lines.append("### 讨论区精华\n")
            _render_discussion_items(disc_items, lines)
            lines.append("")

    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    log.info(f"完整版输出: {output_path}")
    return content


def _render_subtitle_items(items, lines):
    """渲染字幕补充条目"""
    # 按类别分组
    by_cat = {}
    for it in items:
        cat = it.get("category", "其他")
        by_cat.setdefault(cat, []).append(it)
    for cat, cat_items in by_cat.items():
        lines.append(f"**{cat}**\n")
        for it in cat_items:
            t = it.get("time", "")
            content = it.get("content", "")
            context = it.get("context", "")
            correction = it.get("correction", "")
            line = f"- [{t}] {content}"
            if context:
                line += f"\n  - 上下文：{context}"
            if correction:
                line += f"\n  - 纠正：{correction}"
            lines.append(line)
        lines.append("")


def _render_discussion_items(items, lines):
    """渲染讨论区精华条目"""
    by_cat = {}
    for it in items:
        cat = it.get("category", "其他")
        by_cat.setdefault(cat, []).append(it)
    for cat, cat_items in by_cat.items():
        lines.append(f"**{cat}**\n")
        for it in cat_items:
            t = it.get("time", "")
            speaker = it.get("speaker", "")
            tags = it.get("tags", "")
            content = it.get("content", "")
            tag_str = f"（{tags}）" if tags else ""
            line = f"- [{t}] **{speaker}**{tag_str}：{content}"
            lines.append(line)
            # 渲染对话串
            thread = it.get("thread", [])
            if thread:
                for msg in thread:
                    mt = msg.get("time", "")
                    ms = msg.get("speaker", "")
                    mc = msg.get("content", "")
                    lines.append(f"  - [{mt}] {ms}：{mc}")
        lines.append("")


# ---------------------------------------------------------------------------
# 分段与分析
# ---------------------------------------------------------------------------

def chunk_srt_text(srt_entries, chunk_size=30000):
    """将字幕条目按字符数分段，返回 [(起始时间, 结束时间, 文本), ...]"""
    chunks = []
    current_text = []
    current_len = 0
    start_time = srt_entries[0][1] if srt_entries else "00:00:00"
    end_time = start_time

    for seq, s_time, e_time, text in srt_entries:
        line = f"[{s_time}] {text}"
        if current_len + len(line) > chunk_size and current_text:
            chunks.append((start_time, end_time, "\n".join(current_text)))
            current_text = []
            current_len = 0
            start_time = s_time
        current_text.append(line)
        current_len += len(line) + 1
        end_time = e_time

    if current_text:
        chunks.append((start_time, end_time, "\n".join(current_text)))

    return chunks


def format_discussion_text(discussions):
    """将讨论区数据格式化为文本"""
    lines = []
    for d in discussions:
        time_str = d.get("时间", "")
        name = d.get("发言人名称", "")
        content = d.get("发言内容", "")
        tags = []
        for tag_key in ("MBA", "年级", "NPC等级", "进步阶梯", "其他标签"):
            v = d.get(tag_key, "").strip()
            if v:
                tags.append(v)
        tag_str = f" ({', '.join(tags)})" if tags else ""
        lines.append(f"[{time_str}] {name}{tag_str}: {content}")
    return "\n".join(lines)


def chunk_discussions(discussions, chunk_size=30000):
    """将讨论区发言按字符数分段，返回 [(段序号, 文本), ...]"""
    chunks = []
    current_lines = []
    current_len = 0
    for d in discussions:
        line = format_discussion_text([d])
        if current_len + len(line) > chunk_size and current_lines:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_len = 0
        current_lines.append(line)
        current_len += len(line) + 1
    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="逐字稿信息拓展工具")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行：只解析文件，不调用 LLM")
    parser.add_argument("--provider", type=str, default=None,
                        help="覆盖配置中的 LLM 提供商")
    parser.add_argument("--subtitle", type=str, default="",
                        help="字幕文件路径")
    parser.add_argument("--no-prefer-fix", action="store_true",
                        help="直接使用指定字幕，不自动切换到同名 _fix.srt")
    parser.add_argument("--transcript", type=str, default="",
                        help="逐字稿/教学文档路径")
    parser.add_argument("--no-transcript", action="store_true",
                        help="无文稿模式：忽略 Wiki，直接从字幕提取信息（视频号使用）")
    parser.add_argument("--discussion", type=str, default="",
                        help="讨论区 xlsx 路径")
    parser.add_argument("--subtitle-only", action="store_true",
                        help="仅分析字幕（跳过讨论区）")
    parser.add_argument("--discussion-only", action="store_true",
                        help="仅分析讨论区（跳过字幕）")
    parser.add_argument("--no-digest", action="store_true",
                        help="跳过精华摘要生成")
    args = parser.parse_args()
    if args.no_transcript and (args.transcript or args.discussion_only):
        parser.error("--no-transcript 不能与 --transcript 或 --discussion-only 同时使用")

    config, creds = load_config()

    # 命令行覆盖 provider
    if args.provider:
        config["llm_plan"]["current_s5"]["name"] = args.provider

    # 输入文件：CLI 参数优先于配置文件
    inputs = config.get("input", {}).get("s5", {})
    subtitle_path = args.subtitle or inputs.get("subtitle", "")
    transcript_src = args.transcript or inputs.get("transcript", "")
    discussion_path = args.discussion or inputs.get("discussion", "")
    if args.no_transcript:
        transcript_src = ""
    if args.subtitle_only:
        discussion_path = ""

    # 相对路径基于 output 目录
    base_dir = PROJECT_DIR / config.get("path_output_dir", "output").rstrip("/")

    def resolve_path(p):
        """相对路径拼接 base_dir，绝对路径直接用"""
        if not p:
            return p
        pp = Path(p)
        if pp.is_absolute():
            return str(pp)
        if pp.exists():
            return str(pp)
        resolved = base_dir / pp
        return str(resolved)

    subtitle_path = resolve_path(subtitle_path)
    discussion_path = resolve_path(discussion_path)

    # 验证字幕后缀并自动关联 wiki 文档
    transcript_from_wiki = ""
    if subtitle_path and Path(subtitle_path).exists():
        config_path = CFG_DIR / "config.yaml"
        if config_path.exists():
            main_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            valid_suffixes = list(main_cfg.get("s3_engine_suffix", {}).values())
        else:
            valid_suffixes = [".wm", ".wl", ".ws", ".wt", ".mlx_turbo", ".mlx_l3",
                              ".mlx_m", ".mlx_s", ".mlx_t", ".xunfei", ".aliyun",
                              ".doubao", ".feishu", ".tencent",
                              "_wm", "_wl", "_ws", "_wt", "_xunfei", "_aliyun",
                              "_doubao", "_feishu", "_tencent"]

        stem = Path(subtitle_path).stem
        matched_suffix = None
        for sfx in valid_suffixes:
            if stem.endswith(sfx):
                matched_suffix = sfx
                break
        # 也支持 _fix 后缀（如 .wm_fix、_wm_fix）
        if matched_suffix is None:
            for sfx in valid_suffixes:
                if f"{sfx}_fix" in stem:
                    matched_suffix = f"{sfx}_fix"
                    break

        if matched_suffix:
            base_name = stem[:-len(matched_suffix)]
            # 自动关联 wiki 文档
            if not transcript_src and not args.no_transcript:
                wiki_path = Path(subtitle_path).parent / f"{base_name}_wiki.md"
                if not wiki_path.exists():
                    wiki_path = OUTPUT_DIR / f"{base_name}_wiki.md"
                if wiki_path.exists():
                    transcript_from_wiki = str(wiki_path.absolute())
                    log.info(f"自动关联教学文档: {wiki_path.name}")
                else:
                    log.warning(f"未找到教学文档: {base_name}_wiki.md")

    # 优先使用 _fix 版本的字幕
    if subtitle_path and not args.no_prefer_fix:
        sp = Path(subtitle_path)
        fix_path = sp.parent / f"{sp.stem}_fix{sp.suffix}"
        if fix_path.exists():
            log.info(f"检测到校订版字幕，优先使用: {fix_path.name}")
            subtitle_path = str(fix_path)

    # 确定逐字稿来源
    transcript_path = transcript_src or transcript_from_wiki
    if not transcript_path and not args.no_transcript:
        raise FileNotFoundError("未配置逐字稿来源，且未找到关联的 wiki 文档")

    # 解析逐字稿
    if args.no_transcript:
        sections = [("视频内容", "")]
        log.info("无文稿模式：直接提取字幕信息，不作 Wiki 增量对比")
    else:
        tp = Path(transcript_path)
        if not tp.is_absolute() and not tp.exists():
            tp = base_dir / transcript_path
        if not tp.exists():
            raise FileNotFoundError(f"逐字稿文件不存在: {tp}")
        log.info(f"逐字稿来源: 本地文件 {tp}")
        sections = parse_transcript(str(tp))

    section_titles = [t for t, _ in sections]
    log.info(f"章节列表: {section_titles}")

    # 解析字幕
    srt_entries = []
    if subtitle_path and Path(subtitle_path).exists():
        srt_entries = parse_srt(subtitle_path)
        log.info(f"字幕文本总长: {sum(len(e[3]) for e in srt_entries)} 字符")
    elif not args.discussion_only:
        log.warning(f"字幕文件不存在或未配置: {subtitle_path}")
    if args.no_transcript and not srt_entries:
        raise ValueError("无文稿模式需要有效且非空的字幕文件")

    # 解析讨论区
    discussions = []
    if discussion_path and Path(discussion_path).exists():
        discussions = parse_discussion_xlsx(discussion_path)
    elif not args.subtitle_only:
        log.warning(f"讨论区文件不存在或未配置: {discussion_path}")

    if args.dry_run:
        log.info("=== 试运行模式，不调用 LLM ===")
        log.info(f"逐字稿: {len(sections)} 章节")
        log.info(f"字幕: {len(srt_entries)} 条")
        log.info(f"讨论区: {len(discussions)} 条发言")
        return

    if not srt_entries and not discussions:
        raise ValueError("没有可供 S5 分析的字幕或讨论区内容")

    # 初始化 LLM 客户端
    llm = LLMClient(config, creds)
    analysis_cfg = config.get("s5_analysis", {})
    prompt_subtitle = load_prompt(
        analysis_cfg.get("prompt_subtitle_noref", "addon-subtitle-noref.md") if args.no_transcript
        else analysis_cfg.get("prompt_subtitle", "addon-subtitle.md"))
    prompt_discussion = load_prompt(analysis_cfg.get("prompt_discussion", "addon-discussion.md"))
    chunk_size = analysis_cfg.get("chunk_size", 30000)
    # 输出目录跟随输入文件所在目录
    if subtitle_path and Path(subtitle_path).exists():
        output_dir = Path(subtitle_path).parent
    elif discussion_path and Path(discussion_path).exists():
        output_dir = Path(discussion_path).parent
    elif transcript_path and Path(transcript_path).exists():
        output_dir = Path(transcript_path).parent
    else:
        output_dir = PROJECT_DIR / config.get("path_output_dir", "output").rstrip("/")
    if not output_dir.is_absolute():
        output_dir = PROJECT_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # 生成输出文件名前缀：优先字幕文件名 > 讨论区文件名 > 逐字稿
    prefix = config.get("output", {}).get("s5", {}).get("fnprefix", "")
    if not prefix:
        if subtitle_path and Path(subtitle_path).exists():
            # 去掉常见 s3 后缀，如 AI落地Live_069.wm.srt → AI落地Live_069
            stem = Path(subtitle_path).stem
            suffixes = valid_suffixes if "valid_suffixes" in locals() else [".wm", "_wm"]
            for sfx in sorted([s + "_fix" for s in suffixes] + suffixes, key=len, reverse=True):
                if stem.endswith(sfx):
                    stem = stem[:-len(sfx)]
                    break
            prefix = stem
        elif discussion_path and Path(discussion_path).exists():
            prefix = Path(discussion_path).stem
        elif transcript_path:
            prefix = Path(transcript_path).stem
        else:
            prefix = "output"

    # ── 字幕对比分析 ──
    subtitle_items = []
    if srt_entries and not args.discussion_only:
        log.info("=== 开始字幕%s分析 ===", "要点" if args.no_transcript else "对比")
        transcript_text = "\n\n".join(
            f"## {title}\n{content}" for title, content in sections
        )
        chunks = chunk_srt_text(srt_entries, chunk_size)
        log.info(f"字幕分为 {len(chunks)} 段")

        subtitle_progress = InlineProgress(log, interval_sec=5)
        for i, (start, end, chunk_text) in enumerate(chunks):
            subtitle_progress.update(
                f"字幕对比分析: {i + 1}/{len(chunks)} 段 ({start} ~ {end})",
                force=True,
            )
            user_prompt = (
                f"## 逐字稿内容\n\n{transcript_text}\n\n"
                f"---\n\n"
                f"## 字幕原文（{start} ~ {end}）\n\n{chunk_text}"
            )
            if args.no_transcript:
                user_prompt = f"## 视频字幕（{start} ~ {end}）\n\n{chunk_text}"
            try:
                items = request_json_items(
                    llm,
                    prompt_subtitle,
                    user_prompt,
                    S5_SUBTITLE_RESPONSE_SCHEMA,
                )
            except Exception as e:
                raise RuntimeError(f"字幕段 {i+1} 处理失败: {e}") from e
            subtitle_items.extend(items)
            subtitle_progress.update(
                f"字幕对比分析: {i + 1}/{len(chunks)} 段完成，"
                f"提取 {len(items)} 条信息",
                force=True,
            )
        subtitle_progress.done()

        log.info(f"字幕对比分析完成，共提取 {len(subtitle_items)} 条")

    # ── 讨论区分析 ──
    discussion_items = []
    if discussions and not args.subtitle_only:
        log.info("=== 开始讨论区分析 ===")
        # 章节标题 + 首段摘要，提升关联准确度
        section_info = []
        for title, content in sections:
            # 取前 200 字作为摘要
            plain = re.sub(r"^#{1,6}\s+.*\n?", "", content).strip()
            summary = plain[:200] + ("..." if len(plain) > 200 else "")
            section_info.append(f"- {title}：{summary}")
        section_list = "\n".join(section_info)

        disc_chunks = chunk_discussions(discussions, chunk_size)
        log.info(f"讨论区分为 {len(disc_chunks)} 段")

        discussion_progress = InlineProgress(log, interval_sec=5)
        for i, chunk_text in enumerate(disc_chunks):
            discussion_progress.update(
                f"讨论区分析: {i + 1}/{len(disc_chunks)} 段",
                force=True,
            )
            user_prompt = (
                f"## 逐字稿章节（标题 + 摘要）\n\n{section_list}\n\n"
                f"---\n\n"
                f"## 讨论区发言记录（第 {i+1}/{len(disc_chunks)} 段）\n\n{chunk_text}"
            )
            try:
                items = request_json_items(
                    llm,
                    prompt_discussion,
                    user_prompt,
                    S5_DISCUSSION_RESPONSE_SCHEMA,
                )
            except Exception as e:
                raise RuntimeError(f"讨论区段 {i+1} 处理失败: {e}") from e
            discussion_items.extend(items)
            discussion_progress.update(
                f"讨论区分析: {i + 1}/{len(disc_chunks)} 段完成，"
                f"提取 {len(items)} 条信息",
                force=True,
            )
        discussion_progress.done()

        log.info(f"讨论区分析完成，共提取 {len(discussion_items)} 条")

    # ── 合并与渲染 ──
    merged, stats = merge_results(subtitle_items, discussion_items, sections)
    report_path = output_dir / f"{prefix}_信息拓展.md"
    report_content = render_full_report(
        merged, stats, sections, prefix, report_path, no_transcript=args.no_transcript
    )

    # ── 精华摘要 ──
    if not args.no_digest:
        log.info("=== 生成精华摘要 ===")
        digest_path = output_dir / f"{prefix}_精华摘要.md"
        if subtitle_items or discussion_items:
            prompt_digest = load_prompt(
                analysis_cfg.get("prompt_digest", "addon-digest.md")
            )
            digest_reply = llm.chat(prompt_digest, report_content)
            # 清理 LLM 可能包裹的 code fence
            digest_text = re.sub(
                r"^```(?:markdown)?\s*\n(.*)\n```\s*$", r"\1",
                digest_reply.strip(), flags=re.DOTALL,
            )
        else:
            empty_label = "信息" if args.no_transcript else "增量信息"
            digest_text = f"# 精华摘要：{prefix}\n\n未发现可提取的{empty_label}。\n"
        digest_path.write_text(digest_text, encoding="utf-8")
        log.info(f"精华摘要输出: {digest_path}")

    llm.report_usage()
    log.info("完成")


if __name__ == "__main__":
    main()
