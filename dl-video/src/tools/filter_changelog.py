#!/usr/bin/env python3
"""
后处理过滤器 — 从 changelog 中过滤掉过度修正的条目
"""
import re
import sys
from pathlib import Path

# 过滤关键词：reason 中包含这些词的条目会被过滤掉
FILTER_KEYWORDS = [
    # 新版提示词（AI落地069）的措辞
    "应保留原样",
    "应删除重复",
    "应删除",
    "应补全",
    "应按逐字稿修改",
    "多了语气词",
    "删除语气词",
    "删除重复",
    "口语表达",
    "书面化",
    "补全字幕",
    "逐字稿删除了",
    "逐字稿删了",
    "编辑删除了",
    "编辑删了",
    "漏掉了",
    "字幕多了",
    "字幕中多了",
    "字幕缺少",
    "缺少逗号",
    "补全标点",
    # 旧版提示词（Live245）的措辞
    "补充缺失的",
    "修正标点错误",
    "修正标点",
    "删除多余的",
    "补充语气词",
    "修正句子结构",
    "补充缺失",
    "添加标点",
    "修正逗号",
    "修正句号",
    "应补充",
    "应补齐",
    "缺少句号",
    "缺少冒号",
    "添加了逗号",
    "添加了句号",
    "漏掉了逗号",
    "缺少标点",
    "书面语习惯",
    "以符合书面",
    "识别遗漏",
    "多余语气词",
]

def parse_changelog(changelog_path: Path) -> tuple[list, list]:
    """解析 changelog，返回 (dict_corrections, llm_corrections)"""
    content = changelog_path.read_text(encoding='utf-8')

    dict_corrections = []
    llm_corrections = []

    # 解析字典修正（词典替换）
    dict_match = re.search(r'## 词典替换.*?\n(.*?)(?=\n## |$)', content, re.DOTALL)
    if dict_match:
        entries = re.findall(
            r'- \[(\d+)\] (.+?)\n  → (.+?) \(词典替换\)',
            dict_match.group(1),
            re.DOTALL
        )
        for seq, orig, fixed in entries:
            dict_corrections.append(f"- [{seq}] {orig.strip()}\n  → {fixed.strip()} (词典替换)")

    # 解析 LLM 修正（LLM 纠正）
    llm_match = re.search(r'## LLM 纠正.*?\n(.*?)$', content, re.DOTALL)
    if llm_match:
        entries = re.findall(
            r'- \[(\d+)\] (.+?)\n  → (.+?) \((.+?)\)',
            llm_match.group(1),
            re.DOTALL
        )
        for seq, orig, fixed, reason in entries:
            llm_corrections.append({
                'seq': int(seq),
                'original': orig.strip(),
                'fixed': fixed.strip(),
                'reason': reason.strip()
            })

    return dict_corrections, llm_corrections

def should_filter(reason: str) -> tuple[bool, str]:
    """判断是否应该过滤，返回 (是否过滤, 匹配的关键词)"""
    for keyword in FILTER_KEYWORDS:
        if keyword in reason:
            return True, keyword
    return False, ""

def filter_corrections(llm_corrections: list) -> tuple[list, list]:
    """过滤 LLM 修正，返回 (保留的, 过滤掉的)"""
    kept = []
    filtered = []

    for corr in llm_corrections:
        should_remove, keyword = should_filter(corr['reason'])
        if should_remove:
            filtered.append((corr, keyword))
        else:
            kept.append(corr)

    return kept, filtered

def generate_filtered_changelog(
    changelog_path: Path,
    dict_corrections: list,
    kept_corrections: list,
    filtered_corrections: list
) -> str:
    """生成过滤后的 changelog"""
    lines = [
        f"# 字幕修正记录（过滤后）",
        f"",
        f"原始文件：{changelog_path.stem.replace('_fix_changelog', '.srt')}",
        f"",
        f"## 统计",
        f"- 字典修正：{len(dict_corrections)} 条",
        f"- LLM 修正（过滤前）：{len(kept_corrections) + len(filtered_corrections)} 条",
        f"- LLM 修正（过滤后）：{len(kept_corrections)} 条",
        f"- 过滤掉：{len(filtered_corrections)} 条（{len(filtered_corrections) / (len(kept_corrections) + len(filtered_corrections)) * 100:.1f}%）",
        f"",
    ]

    if dict_corrections:
        lines.append("## 字典修正")
        lines.append("")
        lines.extend(dict_corrections)
        lines.append("")

    if kept_corrections:
        lines.append("## LLM 修正（保留）")
        lines.append("")
        for corr in kept_corrections:
            lines.append(f"- [{corr['seq']}] `{corr['original']}` → `{corr['fixed']}`")
            lines.append(f"  - 原因：{corr['reason']}")
        lines.append("")

    if filtered_corrections:
        lines.append("## 过滤掉的修正")
        lines.append("")
        for corr, keyword in filtered_corrections:
            lines.append(f"- [{corr['seq']}] `{corr['original']}` → `{corr['fixed']}`")
            lines.append(f"  - 原因：{corr['reason']}")
            lines.append(f"  - 过滤关键词：{keyword}")
        lines.append("")

    return '\n'.join(lines)

def main():
    if len(sys.argv) < 2:
        print("用法: python filter_changelog.py <changelog.md>")
        sys.exit(1)

    changelog_path = Path(sys.argv[1])
    if not changelog_path.exists():
        print(f"错误：文件不存在 {changelog_path}")
        sys.exit(1)

    print(f"解析 {changelog_path.name}...")
    dict_corrections, llm_corrections = parse_changelog(changelog_path)

    print(f"过滤 LLM 修正...")
    kept, filtered = filter_corrections(llm_corrections)

    print(f"\n统计：")
    print(f"  字典修正：{len(dict_corrections)} 条")
    print(f"  LLM 修正（过滤前）：{len(llm_corrections)} 条")
    print(f"  LLM 修正（过滤后）：{len(kept)} 条")
    if len(llm_corrections) > 0:
        print(f"  过滤掉：{len(filtered)} 条（{len(filtered) / len(llm_corrections) * 100:.1f}%）")
    else:
        print(f"  过滤掉：0 条")

    output_path = changelog_path.parent / f"{changelog_path.stem}_filtered.md"
    content = generate_filtered_changelog(changelog_path, dict_corrections, kept, filtered)
    output_path.write_text(content, encoding='utf-8')

    print(f"\n已生成过滤后的 changelog：{output_path}")

if __name__ == '__main__':
    main()
