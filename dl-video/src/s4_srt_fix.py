"""Step4: 字幕修订 — LLM 对比字幕与教学文档，修订字幕"""

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
import yaml

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
OUTPUT_DIR = PROJECT_DIR / "output"
LOG_DIR = PROJECT_DIR / "log-err"
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "s4_srt_fix.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load_config():
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(CFG_DIR / "credentials.yaml", encoding="utf-8") as f:
        creds = yaml.safe_load(f)
    return config, creds


def load_custom_dict(config):
    """加载用户自定义纠正词典，返回 {错误: 正确} 映射"""
    dict_file = config.get("srt_fix", {}).get("custom_dict", "")
    if not dict_file:
        return {}
    dict_path = CFG_DIR / dict_file
    if not dict_path.exists():
        log.info(f"自定义词典不存在，跳过: {dict_path}")
        return {}
    with open(dict_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    log.info(f"加载自定义词典: {len(data)} 条映射")
    return data


def safe_filename(title: str) -> str:
    return ILLEGAL_CHARS.sub("_", title).strip()


# ---------------------------------------------------------------------------
# SRT 解析与生成
# ---------------------------------------------------------------------------

def parse_srt(filepath):
    """解析 SRT 文件，返回 [(seq, start, end, text), ...]"""
    text = Path(filepath).read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", text.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        seq = int(lines[0].strip())
        time_line = lines[1].strip()
        content = "\n".join(lines[2:]).strip()
        m = re.match(r"(\d[\d:,]+)\s*-->\s*(\d[\d:,]+)", time_line)
        if m:
            entries.append((seq, m.group(1), m.group(2), content))
    log.info(f"字幕解析完成: {len(entries)} 条, 来源: {filepath}")
    return entries


def write_srt(entries, output_path):
    """将条目列表写回 SRT 文件"""
    lines = []
    for seq, start, end, text in entries:
        lines.append(str(seq))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    log.info(f"修正后字幕已写入: {output_path}")


# ---------------------------------------------------------------------------
# 逐字稿加载（本地文件或飞书 URL）
# ---------------------------------------------------------------------------

def load_transcript(path_or_url, creds=None, srt_stem=""):
    """加载逐字稿内容，自适应三种模式：
    1. 空值 → 无参照修订
    2. 本地文件路径 → 直接读取
    3. 飞书 URL → 下载为与 SRT 同名的 .md 文件后读取
    """
    if not path_or_url:
        log.info("未配置逐字稿，将进行无参照修订")
        return ""

    if path_or_url.startswith("http") and "feishu.cn/" in path_or_url:
        local_md = _download_feishu_to_md(path_or_url, srt_stem)
        if local_md:
            return local_md.read_text(encoding="utf-8")
        log.warning("飞书文档下载失败，将进行无参照修订")
        return ""

    fp = Path(path_or_url)
    if not fp.is_absolute():
        fp = PROJECT_DIR / "localscript" / path_or_url
    if not fp.exists():
        log.warning(f"逐字稿文件不存在: {fp}，将进行无参照修订")
        return ""
    log.info(f"使用本地逐字稿: {fp}")
    return fp.read_text(encoding="utf-8")


def _srt_stem_to_base(srt_stem):
    """从 SRT 文件名中去掉模型后缀，得到内容基础名。
    如 'AI落地Live_069_wm' → 'AI落地Live_069'"""
    if "_" in srt_stem:
        return srt_stem.rsplit("_", 1)[0]
    return srt_stem


def _download_feishu_to_md(url, srt_stem=""):
    """从飞书 URL 下载文档并保存为本地 MD 文件，返回 Path 或 None。"""
    from url2md import feishu_url_to_md

    out_dir = PROJECT_DIR / "localscript"
    out_dir.mkdir(exist_ok=True)

    if srt_stem:
        base = _srt_stem_to_base(srt_stem)
        md_name = f"{base}.md"
    else:
        token = url.rstrip("/").split("/")[-1].split("?")[0]
        md_name = f"{token}.md"

    out_path = out_dir / md_name

    if out_path.exists():
        log.info(f"本地已有逐字稿，直接使用: {out_path}")
        return out_path

    try:
        md_text = feishu_url_to_md(url)
        out_path.write_text(md_text, encoding="utf-8")
        log.info(f"飞书逐字稿已下载: {out_path} ({len(md_text)} 字符)")
        return out_path
    except Exception as e:
        log.error(f"飞书文档下载失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 专有名词提取
# ---------------------------------------------------------------------------

def extract_terms_from_transcript(transcript_text):
    """从逐字稿中提取专有名词列表，供 LLM 参考。"""
    terms = set()
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9\s\.\-]{1,30}[A-Za-z0-9]", transcript_text):
        term = m.group().strip()
        if len(term) >= 3 and not term.isspace():
            terms.add(term)
    quote_pat = re.compile(r"[\u300a\u300c\u201c](.*?)[\u300b\u300d\u201d]")
    for m in quote_pat.finditer(transcript_text):
        term = m.group(1).strip()
        if 1 < len(term) <= 20:
            terms.add(term)
    stopwords = {"the", "and", "for", "with", "that", "this", "from", "are",
                 "was", "were", "been", "have", "has", "had", "not", "but",
                 "can", "will", "just", "more", "also", "very", "than"}
    terms = {t for t in terms if t.lower() not in stopwords}

    def _looks_like_hash(s):
        if not re.match(r"^[A-Za-z0-9.]+$", s):
            return False
        if len(s) > 18:
            return True
        has_upper = any(c.isupper() for c in s)
        has_lower = any(c.islower() for c in s)
        has_digit = any(c.isdigit() for c in s)
        if len(s) > 8 and has_upper and has_lower and " " not in s:
            if has_digit:
                return True
            transitions = sum(1 for i in range(1, len(s))
                              if s[i].isupper() != s[i-1].isupper())
            if transitions > 3:
                return True
        return False

    terms = {t for t in terms
             if not _looks_like_hash(t)
             and not t.startswith("**") and not t.startswith("##")}
    log.info(f"从逐字稿提取专有名词: {len(terms)} 个")
    return sorted(terms)


# ---------------------------------------------------------------------------
# LLM 客户端（OpenAI 兼容接口）
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, config, creds):
        provider = config["llm"]["provider"]
        llm_cfg = config["llm"][provider]
        self.model = llm_cfg["model"]
        self.base_url = llm_cfg["base_url"].rstrip("/")
        self.max_tokens = llm_cfg.get("max_tokens", 8192)
        self.temperature = llm_cfg.get("temperature", 0.2)
        self.provider = provider
        cred_block = creds.get(provider, {})
        self.api_key = cred_block.get("api_key", "")
        if not self.api_key:
            raise ValueError(f"未配置 {provider} 的 api_key")
        if provider == "volcengine":
            ep_id = cred_block.get("endpoint_id", "")
            if ep_id:
                self.model = ep_id
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        log.info(f"LLM 客户端: provider={provider}, model={self.model}")

    def chat(self, system_prompt, user_prompt, temperature=None):
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
            "temperature": temperature or self.temperature,
        }
        max_retries = 6
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=300)
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {})
                self.total_calls += 1
                self.total_input_tokens += usage.get("prompt_tokens", 0)
                self.total_output_tokens += usage.get("completion_tokens", 0)
                return data["choices"][0]["message"]["content"]
            except requests.exceptions.HTTPError:
                if resp.status_code == 429:
                    wait = min(2 ** attempt * 10, 120)
                    log.warning(f"速率限制(attempt {attempt+1}/{max_retries})"
                                f"，等待 {wait}s...")
                    time.sleep(wait)
                    continue
                log.error(f"LLM 调用失败: {resp.status_code}, {resp.text}")
                raise
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.ProxyError,
                    requests.exceptions.ReadTimeout) as e:
                wait = min(2 ** attempt * 10, 120)
                log.warning(f"连接异常(attempt {attempt+1}/{max_retries})"
                            f"，等待 {wait}s 后重试: {e}")
                time.sleep(wait)
                continue
            except Exception as e:
                log.error(f"LLM 调用异常: {e}")
                raise
        raise RuntimeError("LLM 调用重试次数耗尽")

    def report_usage(self):
        log.info(
            f"LLM 用量: provider={self.provider}, model={self.model}, "
            f"{self.total_calls} 次调用, "
            f"输入 {self.total_input_tokens} tokens, "
            f"输出 {self.total_output_tokens} tokens"
        )


# ---------------------------------------------------------------------------
# 核心处理
# ---------------------------------------------------------------------------

def apply_dict_fixes(entries, custom_dict):
    """第一轮：用自定义词典做字符串替换，返回 (修正后entries, 修正日志)"""
    if not custom_dict:
        return entries, []
    fixed = []
    changelog = []
    for seq, start, end, text in entries:
        new_text = text
        for wrong, correct in custom_dict.items():
            if wrong in new_text:
                new_text = new_text.replace(wrong, correct)
        if new_text != text:
            changelog.append({
                "seq": seq, "original": text,
                "fixed": new_text, "reason": "词典替换"
            })
        fixed.append((seq, start, end, new_text))
    log.info(f"词典替换: {len(changelog)} 条修正")
    return fixed, changelog


def parse_llm_json(reply):
    """从 LLM 回复中提取 JSON 数组"""
    text = reply.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as e:
        log.error(f"JSON 解析失败: {e}")
        dump_path = LOG_DIR / f"srtfix_raw_{int(time.time())}.txt"
        dump_path.write_text(reply, encoding="utf-8")
        log.error(f"原始回复已保存: {dump_path}")
        return []


def build_user_prompt(chunk_entries, transcript_excerpt, terms, custom_dict):
    """构建发送给 LLM 的 user prompt"""
    parts = []
    if transcript_excerpt:
        parts.append("## 逐字稿参考内容（节选）\n")
        parts.append(transcript_excerpt[:6000])
        parts.append("\n")
    if terms:
        parts.append("## 逐字稿中的专有名词（供参考）\n")
        parts.append(", ".join(terms[:100]))
        parts.append("\n")
    if custom_dict:
        parts.append("## 已知的常见错误映射\n")
        for wrong, correct in list(custom_dict.items())[:30]:
            parts.append(f"- {wrong} → {correct}")
        parts.append("\n")
    parts.append("## 待校订的字幕\n")
    for seq, start, end, text in chunk_entries:
        parts.append(f"[{seq}] [{start}] {text}")
    return "\n".join(parts)


def find_transcript_excerpt(transcript_text, chunk_entries):
    """根据字幕时间段，从逐字稿中截取相关段落作为参考。"""
    if not transcript_text or not chunk_entries:
        return ""
    total_lines = transcript_text.count("\n") + 1
    first_seq = chunk_entries[0][0]
    last_seq = chunk_entries[-1][0]
    ratio_start = max(0, (first_seq - 1) / max(last_seq + 50, 1))
    ratio_end = min(1, (last_seq + 10) / max(last_seq + 50, 1))
    lines = transcript_text.split("\n")
    start_line = int(len(lines) * ratio_start)
    end_line = int(len(lines) * ratio_end)
    excerpt = "\n".join(lines[start_line:end_line])
    return excerpt[:6000]


def run_llm_fix(entries, transcript_text, terms, custom_dict, config, creds,
                cache_path=None):
    """第二轮：LLM 逐段纠正，返回 (修正日志列表, client)。
    支持断点续传：cache_path 指定缓存文件，已完成的段会跳过。"""
    srt_fix_cfg = config.get("srt_fix", {})
    if transcript_text:
        prompt_file = srt_fix_cfg.get("prompt", "prompt-srtfix-ref.md")
    else:
        prompt_file = srt_fix_cfg.get("prompt_noref", "prompt-srtfix-noref.md")
    prompt_path = CFG_DIR / prompt_file
    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件不存在: {prompt_path}")
    system_prompt = prompt_path.read_text(encoding="utf-8").strip()
    log.info(f"使用提示词: {prompt_file} "
             f"({'有参照' if transcript_text else '无参照'})")

    chunk_size = srt_fix_cfg.get("chunk_size", 80)
    client = LLMClient(config, creds)
    all_fixes = []

    cache = {}
    if cache_path and Path(cache_path).exists():
        try:
            cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            cached_count = sum(len(v) for v in cache.values())
            log.info(f"加载断点缓存: {len(cache)} 段, {cached_count} 条修正")
        except Exception as e:
            log.warning(f"缓存文件损坏，忽略: {e}")
            cache = {}

    for i in range(0, len(entries), chunk_size):
        chunk = entries[i:i + chunk_size]
        chunk_num = i // chunk_size + 1
        total_chunks = (len(entries) + chunk_size - 1) // chunk_size
        chunk_key = str(chunk_num)

        if chunk_key in cache:
            fixes = cache[chunk_key]
            log.info(f"LLM 校订: 段 {chunk_num}/{total_chunks} "
                     f"(字幕 {chunk[0][0]}-{chunk[-1][0]}) "
                     f"[缓存] {len(fixes)} 条")
            all_fixes.extend(fixes)
            continue

        log.info(f"LLM 校订: 段 {chunk_num}/{total_chunks} "
                 f"(字幕 {chunk[0][0]}-{chunk[-1][0]})")

        try:
            excerpt = find_transcript_excerpt(transcript_text, chunk)
            user_prompt = build_user_prompt(
                chunk, excerpt, terms, custom_dict)
            reply = client.chat(system_prompt, user_prompt)
            fixes = parse_llm_json(reply)
            fixes = [f for f in fixes
                     if f.get("fixed") and f.get("original")
                     and f["fixed"].strip() != f["original"].strip()]
            log.info(f"  段 {chunk_num} 返回 {len(fixes)} 条有效修正")
            all_fixes.extend(fixes)

            if cache_path:
                cache[chunk_key] = fixes
                Path(cache_path).write_text(
                    json.dumps(cache, ensure_ascii=False, indent=2),
                    encoding="utf-8")
        except RuntimeError as e:
            log.error(f"  段 {chunk_num} 失败（重试耗尽），跳过: {e}")
            if cache_path:
                Path(cache_path).write_text(
                    json.dumps(cache, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            continue

    return all_fixes, client


def apply_llm_fixes(entries, llm_fixes):
    """将 LLM 返回的修正应用到字幕条目上"""
    fix_map = {}
    for fix in llm_fixes:
        seq = fix.get("seq")
        if seq is None:
            continue
        try:
            fix_map[int(seq)] = fix
        except (ValueError, TypeError):
            log.warning(f"跳过无效序号: {seq}")
            continue

    result = []
    applied = 0
    for seq, start, end, text in entries:
        if seq in fix_map:
            new_text = fix_map[seq].get("fixed", text)
            if new_text and new_text != text:
                result.append((seq, start, end, new_text))
                applied += 1
                continue
        result.append((seq, start, end, text))
    log.info(f"LLM 修正应用: {applied} 条")
    return result


def write_changelog(changelog, llm_fixes, output_path):
    """写修正日志"""
    lines = [
        f"# 字幕校订日志\n",
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
    ]
    if changelog:
        lines.append(f"## 词典替换 ({len(changelog)} 条)\n")
        for item in changelog:
            lines.append(f"- [{item['seq']}] {item['original']}")
            lines.append(f"  → {item['fixed']} ({item['reason']})")
    if llm_fixes:
        lines.append(f"\n## LLM 纠正 ({len(llm_fixes)} 条)\n")
        for item in llm_fixes:
            lines.append(
                f"- [{item.get('seq', '?')}] {item.get('original', '')}")
            lines.append(
                f"  → {item.get('fixed', '')} ({item.get('reason', '')})")
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    log.info(f"修正日志已写入: {output_path}")


# ---------------------------------------------------------------------------
# Pipeline 入口
# ---------------------------------------------------------------------------

def find_subtitle(output_dir, base_name):
    """查找字幕文件，优先 _ori.srt，其次 _wm.srt"""
    ori = output_dir / f"{base_name}_ori.srt"
    if ori.exists():
        return ori
    wm = output_dir / f"{base_name}_wm.srt"
    if wm.exists():
        return wm
    return None


def find_wiki_doc(output_dir, base_name):
    """查找教学文档"""
    wiki = output_dir / f"{base_name}_wiki.md"
    if wiki.exists():
        return wiki
    return None


def run_srt_fix(srt_path, wiki_path, output_dir, config, creds):
    """执行字幕修订流程"""
    entries = parse_srt(str(srt_path))
    log.info(f"字幕: {len(entries)} 条")

    transcript_text = ""
    if wiki_path:
        transcript_text = wiki_path.read_text(encoding="utf-8")
        log.info(f"教学文档: {len(transcript_text)} 字符")

    terms = (extract_terms_from_transcript(transcript_text)
             if transcript_text else [])

    custom_dict = load_custom_dict(config)

    # 词典替换
    entries, dict_changelog = apply_dict_fixes(entries, custom_dict)

    # LLM 修订
    llm_fixes, llm_client = run_llm_fix(
        entries, transcript_text, terms, custom_dict, config, creds)
    entries = apply_llm_fixes(entries, llm_fixes)

    # 输出
    stem = srt_path.stem
    out_srt = output_dir / f"{stem}_fix.srt"
    out_log = output_dir / f"{stem}_fix_changelog.md"
    write_srt(entries, str(out_srt))
    write_changelog(dict_changelog, llm_fixes, str(out_log))

    total = len(dict_changelog) + len([f for f in llm_fixes if f.get("fixed")])
    log.info(f"修订完成: {total} 处修正, 输出: {out_srt}")
    llm_client.report_usage()
    return out_srt


# ---------------------------------------------------------------------------
# 主流程（合并 pipeline 模式 + CLI 模式）
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="字幕校订工具")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅解析文件，不调用 LLM")
    parser.add_argument("--dict-only", action="store_true",
                        help="仅做词典替换，不调用 LLM")
    parser.add_argument("--subtitle", type=str, default="",
                        help="指定字幕文件路径（CLI 模式）")
    parser.add_argument("--transcript", type=str, default="",
                        help="指定逐字稿路径/URL（CLI 模式）")
    parser.add_argument("--no-cache", action="store_true",
                        help="不使用断点缓存，强制重新调用 LLM")
    args = parser.parse_args()

    config, creds = load_config()

    # --subtitle 参数 → CLI 模式（独立字幕校订）
    if args.subtitle:
        _run_cli_mode(args, config, creds)
        return

    # 无 --subtitle → pipeline 模式（扫描 output 目录）
    _run_pipeline_mode(args, config, creds)


def _run_cli_mode(args, config, creds):
    """CLI 模式：指定字幕文件，独立运行校订"""
    custom_dict = load_custom_dict(config)

    srt_path = Path(args.subtitle)
    if not srt_path.is_absolute():
        srt_path = PROJECT_DIR / "localscript" / args.subtitle
    if not srt_path.exists():
        log.error(f"字幕文件不存在: {srt_path}")
        return

    entries = parse_srt(srt_path)
    transcript_text = load_transcript(
        args.transcript, creds, srt_stem=srt_path.stem)
    terms = (extract_terms_from_transcript(transcript_text)
             if transcript_text else [])

    if args.dry_run:
        log.info(f"[dry-run] 字幕: {len(entries)} 条")
        log.info(f"[dry-run] 逐字稿: {len(transcript_text)} 字符")
        log.info(f"[dry-run] 专有名词: {len(terms)} 个")
        log.info(f"[dry-run] 自定义词典: {len(custom_dict)} 条")
        if terms:
            log.info(f"[dry-run] 前20个专有名词: {terms[:20]}")
        return

    output_dir = srt_path.parent
    stem = srt_path.stem

    # 第一轮：词典替换
    entries, dict_changelog = apply_dict_fixes(entries, custom_dict)

    # 第二轮：LLM 纠正
    llm_fixes = []
    if not args.dict_only:
        cache_path = None
        if not args.no_cache:
            cache_path = output_dir / f"{stem}_llm_cache.json"
        llm_fixes, llm_client = run_llm_fix(
            entries, transcript_text, terms, custom_dict, config, creds,
            cache_path=cache_path)
        entries = apply_llm_fixes(entries, llm_fixes)
        if cache_path and cache_path.exists():
            cache_path.unlink()
            log.info("LLM 缓存已清理")

    suffix = config.get("srt_fix", {}).get("suffix", "_fix")
    out_srt = output_dir / f"{stem}{suffix}.srt"
    out_log = output_dir / f"{stem}{suffix}_changelog.md"

    write_srt(entries, out_srt)
    write_changelog(dict_changelog, llm_fixes, out_log)

    total = len(dict_changelog) + len(
        [f for f in llm_fixes if f.get("fixed")])
    log.info(f"校订完成: 共 {total} 处修正")
    if not args.dict_only:
        llm_client.report_usage()


def _run_pipeline_mode(args, config, creds):
    """Pipeline 模式：扫描 output 目录，批量处理"""
    input_file = os.environ.get("DL_VIDEO_INPUT_FILE", "")
    input_type = os.environ.get("DL_VIDEO_INPUT_TYPE", "")
    srt_input = input_file if input_type == "srt" else ""

    if srt_input:
        srt_path_provided = Path(srt_input)
        output_dir = srt_path_provided.parent
        log.info(f"SRT 输入模式，输出目录: {output_dir}")
        srt_files = [srt_path_provided]
    else:
        output_dir = PROJECT_DIR / config.get("output_dir", "output")
        output_dir.mkdir(exist_ok=True)
        srt_files = (sorted(output_dir.glob("*_ori.srt"))
                     + sorted(output_dir.glob("*_wm.srt")))
        if not srt_files:
            log.warning(f"未找到字幕文件: {output_dir}")
            return

    for i, srt_path in enumerate(srt_files):
        log.info(f"=== 任务 {i+1}/{len(srt_files)} ===")

        if not srt_path.exists():
            log.warning(f"SRT 文件不存在: {srt_path}")
            continue

        fix_path = output_dir / f"{srt_path.stem}_fix.srt"
        if fix_path.exists():
            log.info(f"修订字幕已存在，跳过: {fix_path}")
            continue

        base_name = srt_path.stem
        for suffix in ["_ori", "_wm"]:
            if base_name.endswith(suffix):
                base_name = base_name[:-len(suffix)]
                break

        wiki_path = find_wiki_doc(output_dir, base_name)
        log.info(f"字幕: {srt_path.name}, 教学文档: "
                 f"{wiki_path.name if wiki_path else '无'}")

        run_srt_fix(srt_path, wiki_path, output_dir, config, creds)

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
