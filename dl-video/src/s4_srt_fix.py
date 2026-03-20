"""字幕校订工具 - 基于逐字稿参考和 LLM 纠正 Whisper 字幕错误"""

import argparse
import json
import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "srtfix.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


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
    dict_file = config.get("s4_fix", {}).get("custom_dict", "")
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

    # 模式3：飞书 URL → 下载为本地 MD
    if path_or_url.startswith("http") and "feishu.cn/" in path_or_url:
        local_md = _download_feishu_to_md(path_or_url, srt_stem)
        if local_md:
            return local_md.read_text(encoding="utf-8")
        log.warning("飞书文档下载失败，将进行无参照修订")
        return ""

    # 模式2：本地文件
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
    # 去掉最后一个 _ 及之后的部分（即模型后缀如 _wm）
    if "_" in srt_stem:
        return srt_stem.rsplit("_", 1)[0]
    return srt_stem


def _download_feishu_to_md(url, srt_stem=""):
    """从飞书 URL 下载文档并保存为本地 MD 文件，返回 Path 或 None。
    文件名与 SRT 内容名对齐：如 SRT 为 xxx_wm.srt，MD 为 xxx.md。
    若本地已存在同名 MD，直接复用不重复下载。"""
    import sys
    sys.path.insert(0, str(SCRIPT_DIR))
    from url2md import feishu_url_to_md

    out_dir = PROJECT_DIR / "localscript"
    out_dir.mkdir(exist_ok=True)

    # 用 SRT 基础名推导 MD 文件名（去掉模型后缀）
    if srt_stem:
        base = _srt_stem_to_base(srt_stem)
        md_name = f"{base}.md"
    else:
        # 兜底：用飞书 token
        token = url.rstrip("/").split("/")[-1].split("?")[0]
        md_name = f"{token}.md"

    out_path = out_dir / md_name

    # 已存在则直接复用
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
    """从逐字稿中提取专有名词列表，供 LLM 参考。
    提取规则：英文单词/短语、中英混合词、带引号的术语。"""
    terms = set()
    # 英文单词/短语（2个以上字母）
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9\s\.\-]{1,30}[A-Za-z0-9]", transcript_text):
        term = m.group().strip()
        if len(term) >= 3 and not term.isspace():
            terms.add(term)
    # 带书名号或引号的术语
    quote_pat = re.compile(r"[\u300a\u300c\u201c](.*?)[\u300b\u300d\u201d]")
    for m in quote_pat.finditer(transcript_text):
        term = m.group(1).strip()
        if 1 < len(term) <= 20:
            terms.add(term)
    # 去掉太常见的英文词
    stopwords = {"the", "and", "for", "with", "that", "this", "from", "are",
                 "was", "were", "been", "have", "has", "had", "not", "but",
                 "can", "will", "just", "more", "also", "very", "than"}
    terms = {t for t in terms if t.lower() not in stopwords}
    # 过滤飞书 block ID（纯字母数字混合串，看起来像 hash）和 markdown 标记
    def _looks_like_hash(s):
        # 纯字母数字，无空格
        if not re.match(r"^[A-Za-z0-9.]+$", s):
            return False
        # 长串一律过滤
        if len(s) > 18:
            return True
        # 大小写混杂的无意义串（非常见英文单词特征）
        has_upper = any(c.isupper() for c in s)
        has_lower = any(c.islower() for c in s)
        has_digit = any(c.isdigit() for c in s)
        if len(s) > 8 and has_upper and has_lower and " " not in s:
            # 有数字或者大小写交替频繁 → 像 hash
            if has_digit:
                return True
            # 大小写交替次数多 → 像 camelCase ID
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
# LLM 客户端（与 addon 相同的 OpenAI 兼容接口）
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, config, creds):
        provider = config["llm_plan"]["current_s4"]["name"]
        llm_plan = config["llm_plan"][provider]
        self.model = llm_plan["model"]
        self.base_url = llm_plan["base_url"].rstrip("/")
        self.max_tokens = llm_plan.get("max_tokens", 8192)
        self.temperature = config["llm_plan"]["current_s4"].get("temperature", 0.2)
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
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=300)
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {})
                self.total_calls += 1
                self.total_input_tokens += usage.get("prompt_tokens", 0)
                self.total_output_tokens += usage.get("completion_tokens", 0)
                return data["choices"][0]["message"]["content"]
            except requests.exceptions.HTTPError:
                if resp.status_code == 429:
                    wait = 2 ** attempt * 5
                    log.warning(f"速率限制，等待 {wait}s...")
                    time.sleep(wait)
                    continue
                log.error(f"LLM 调用失败: {resp.status_code}, {resp.text}")
                raise
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.ProxyError,
                    requests.exceptions.ReadTimeout) as e:
                wait = 2 ** attempt * 10
                log.warning(f"连接异常(attempt {attempt+1}/3)，等待 {wait}s 后重试: {e}")
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
                "fixed": new_text, "reason": f"词典替换"
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
    # 逐字稿参考
    if transcript_excerpt:
        parts.append("## 逐字稿参考内容（节选）\n")
        parts.append(transcript_excerpt[:6000])
        parts.append("\n")
    # 专有名词表
    if terms:
        parts.append("## 逐字稿中的专有名词（供参考）\n")
        parts.append(", ".join(terms[:100]))
        parts.append("\n")
    # 自定义词典
    if custom_dict:
        parts.append("## 已知的常见错误映射\n")
        for wrong, correct in list(custom_dict.items())[:30]:
            parts.append(f"- {wrong} → {correct}")
        parts.append("\n")
    # 字幕内容
    parts.append("## 待校订的字幕\n")
    for seq, start, end, text in chunk_entries:
        parts.append(f"[{seq}] [{start}] {text}")
    return "\n".join(parts)


def find_transcript_excerpt(transcript_text, chunk_entries):
    """根据字幕时间段，从逐字稿中截取相关段落作为参考。
    简单策略：取逐字稿的对应比例段落。"""
    if not transcript_text or not chunk_entries:
        return ""
    # 用字幕序号估算在全文中的位置比例
    total_lines = transcript_text.count("\n") + 1
    # 取逐字稿中对应比例的段落
    first_seq = chunk_entries[0][0]
    last_seq = chunk_entries[-1][0]
    # 假设字幕总数和逐字稿行数大致对应
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
    # 根据是否有逐字稿选择提示词
    if transcript_text:
        prompt_file = config.get("s4_fix", {}).get("prompt", "prompt-srtfix-ref.md")
    else:
        prompt_file = config.get("s4_fix", {}).get("prompt_noref", "prompt-srtfix-noref.md")
    prompt_path = CFG_DIR / prompt_file
    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件不存在: {prompt_path}")
    system_prompt = prompt_path.read_text(encoding="utf-8").strip()
    log.info(f"使用提示词: {prompt_file} ({'有参照' if transcript_text else '无参照'})")

    chunk_size = config.get("s4_fix", {}).get("chunk_size", 80)
    client = LLMClient(config, creds)
    all_fixes = []

    # 加载断点缓存
    cache = {}  # {chunk_num: [fixes]}
    if cache_path and Path(cache_path).exists():
        try:
            cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            cached_count = sum(len(v) for v in cache.values())
            log.info(f"加载断点缓存: {len(cache)} 段, {cached_count} 条修正")
        except Exception as e:
            log.warning(f"缓存文件损坏，忽略: {e}")
            cache = {}

    # 分段处理
    for i in range(0, len(entries), chunk_size):
        chunk = entries[i:i + chunk_size]
        chunk_num = i // chunk_size + 1
        total_chunks = (len(entries) + chunk_size - 1) // chunk_size
        chunk_key = str(chunk_num)

        # 断点续传：已有缓存则跳过
        if chunk_key in cache:
            fixes = cache[chunk_key]
            log.info(f"LLM 校订: 段 {chunk_num}/{total_chunks} "
                     f"(字幕 {chunk[0][0]}-{chunk[-1][0]}) [缓存] {len(fixes)} 条")
            all_fixes.extend(fixes)
            continue

        log.info(f"LLM 校订: 段 {chunk_num}/{total_chunks} "
                 f"(字幕 {chunk[0][0]}-{chunk[-1][0]})")

        excerpt = find_transcript_excerpt(transcript_text, chunk)
        user_prompt = build_user_prompt(chunk, excerpt, terms, custom_dict)
        reply = client.chat(system_prompt, user_prompt)
        fixes = parse_llm_json(reply)
        # 过滤无效条目：original == fixed 或缺少必要字段
        fixes = [f for f in fixes
                 if f.get("fixed") and f.get("original")
                 and f["fixed"].strip() != f["original"].strip()]
        log.info(f"  段 {chunk_num} 返回 {len(fixes)} 条有效修正")
        all_fixes.extend(fixes)

        # 实时写入缓存
        if cache_path:
            cache[chunk_key] = fixes
            Path(cache_path).write_text(
                json.dumps(cache, ensure_ascii=False, indent=2),
                encoding="utf-8")

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
            # LLM 可能返回 "4276,4277" 这样的合并序号，跳过
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
    lines = [f"# 字幕校订日志\n", f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"]
    if changelog:
        lines.append(f"## 词典替换 ({len(changelog)} 条)\n")
        for item in changelog:
            lines.append(f"- [{item['seq']}] {item['original']}")
            lines.append(f"  → {item['fixed']} ({item['reason']})")
    if llm_fixes:
        lines.append(f"\n## LLM 纠正 ({len(llm_fixes)} 条)\n")
        for item in llm_fixes:
            lines.append(f"- [{item.get('seq', '?')}] {item.get('original', '')}")
            lines.append(f"  → {item.get('fixed', '')} ({item.get('reason', '')})")
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    log.info(f"修正日志已写入: {output_path}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="字幕校订工具")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅解析文件，不调用 LLM")
    parser.add_argument("--dict-only", action="store_true",
                        help="仅做词典替换，不调用 LLM")
    parser.add_argument("--subtitle", type=str, default="",
                        help="覆盖配置中的字幕文件路径")
    parser.add_argument("--transcript", type=str, default="",
                        help="覆盖配置中的逐字稿路径/URL")
    parser.add_argument("--no-cache", action="store_true",
                        help="不使用断点缓存，强制重新调用 LLM")
    args = parser.parse_args()

    config, creds = load_config()
    custom_dict = load_custom_dict(config)

    # 确定输入文件
    subtitle_path = args.subtitle or config["s4_input"]["subtitle"]
    transcript_src = args.transcript or config["s4_input"].get("transcript", "")

    # 解析字幕（支持多种路径查找）
    srt_path = Path(subtitle_path)
    if not srt_path.is_absolute():
        # 尝试多种路径
        for base_dir in [Path("."), PROJECT_DIR, PROJECT_DIR / "output", PROJECT_DIR / "localscript"]:
            candidate = base_dir / subtitle_path
            if candidate.exists():
                srt_path = candidate
                break
    if not srt_path.exists():
        log.error(f"字幕文件不存在: {srt_path}")
        return

    # 验证后缀并自动关联 wiki 文档
    # 1. 读取 config.yaml 获取有效后缀列表
    config_path = CFG_DIR / "config.yaml"
    if config_path.exists():
        import yaml as yaml2
        main_cfg = yaml2.safe_load(config_path.read_text(encoding="utf-8"))
        valid_suffixes = list(main_cfg.get("s3_engine_suffix", {}).values())
    else:
        valid_suffixes = ["_wm", "_wl", "_ws", "_wt", "_xunfei", "_aliyun", "_doubao", "_feishu", "_tencent"]

    # 2. 检查文件名后缀是否在列表中
    stem = srt_path.stem
    matched_suffix = None
    for suffix in valid_suffixes:
        if stem.endswith(suffix):
            matched_suffix = suffix
            break

    if matched_suffix is None:
        log.error(f"字幕文件后缀不在有效列表中: {srt_path.name}")
        log.error(f"有效后缀: {valid_suffixes}")
        return

    # 3. 查找对应的 wiki 文档
    base_name = stem[:-len(matched_suffix)]  # 去掉后缀得到基础名
    wiki_path = srt_path.parent / f"{base_name}_wiki.md"
    if not wiki_path.exists():
        # 尝试在 output 目录查找
        wiki_path = OUTPUT_DIR / f"{base_name}_wiki.md"

    # 4. 加载逐字稿（优先级：用户指定 > wiki 文档 > 无参照）
    transcript_text = ""
    if wiki_path.exists():
        # 自动关联 wiki 文档
        transcript_text = wiki_path.read_text(encoding="utf-8")
        log.info(f"自动关联教学文档: {wiki_path.name} ({len(transcript_text)} 字符)")
    elif transcript_src:
        # 用户手动指定了 transcript
        transcript_text = load_transcript(transcript_src, creds, srt_stem=srt_path.stem)
    else:
        log.info("未找到教学文档，将进行无参照修订")

    # 解析字幕
    entries = parse_srt(srt_path)

    # 提取专有名词
    terms = extract_terms_from_transcript(transcript_text) if transcript_text else []

    if args.dry_run:
        log.info(f"[dry-run] 字幕: {len(entries)} 条")
        log.info(f"[dry-run] 逐字稿: {len(transcript_text)} 字符")
        log.info(f"[dry-run] 专有名词: {len(terms)} 个")
        log.info(f"[dry-run] 自定义词典: {len(custom_dict)} 条")
        if terms:
            log.info(f"[dry-run] 前20个专有名词: {terms[:20]}")
        return

    # 输出路径准备
    output_dir = PROJECT_DIR / config["s4_output"]["dir"]
    output_dir.mkdir(exist_ok=True)
    suffix = config["s4_output"].get("suffix", "_fix")
    stem = srt_path.stem

    # 第一轮：词典替换
    entries, dict_changelog = apply_dict_fixes(entries, custom_dict)

    # 第二轮：LLM 纠正
    llm_fixes = []
    if not args.dict_only:
        # 断点缓存路径
        cache_path = None
        if not args.no_cache:
            cache_path = output_dir / f"{stem}_llm_cache.json"
        llm_fixes, llm_client = run_llm_fix(
            entries, transcript_text, terms, custom_dict, config, creds,
            cache_path=cache_path
        )
        entries = apply_llm_fixes(entries, llm_fixes)
        # 完成后删除缓存文件
        if cache_path and cache_path.exists():
            cache_path.unlink()
            log.info("LLM 缓存已清理")

    # 输出文件
    out_srt = output_dir / f"{stem}{suffix}.srt"
    out_log = output_dir / f"{stem}{suffix}_changelog.md"

    write_srt(entries, out_srt)
    write_changelog(dict_changelog, llm_fixes, out_log)

    total_fixes = len(dict_changelog) + len([f for f in llm_fixes
                                              if f.get("fixed")])
    log.info(f"校订完成: 共 {total_fixes} 处修正")

    # 输出 LLM 用量
    if not args.dict_only and llm_client:
        llm_client.report_usage()


if __name__ == "__main__":
    main()
