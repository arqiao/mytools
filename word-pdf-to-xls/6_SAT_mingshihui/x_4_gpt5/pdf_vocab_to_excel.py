import argparse
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pdfplumber
from openpyxl import Workbook


LIST_RE = re.compile(r"\blist\s*(\d+)\b", re.IGNORECASE)
MARK_RE = re.compile(r"[Ⓜ①②③④⑤⑥⑦]|M\s*\d")  # 兼容 OCR/抽取把Ⓜ变成 M123 的情况
PHONETIC_INLINE_RE = re.compile(r"/[^/]{1,80}/")  # 行内音标片段
PURE_PAGE_NO_RE = re.compile(r"^\d{1,4}$")

WATERMARK_NOISE = {"名师汇", "汇", "师", "名"}  # 你的PDF里水印经常被抽出来

@dataclass
class Entry:
    word: str
    meaning: str
    list_id: str


def normalize_list_id(n: int) -> str:
    return f"list-{n:02d}"


def clean_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_noise_token(t: str) -> bool:
    if not t:
        return True
    if t in WATERMARK_NOISE:
        return True
    if PURE_PAGE_NO_RE.match(t):
        return True
    if t in {"单词", "音标", "词义", "标记"}:
        return True
    return False


def group_words_to_lines(words: List[dict], y_tol: float = 3.0) -> List[List[dict]]:
    """
    把 extract_words() 的结果按 y(top) 聚成“行”。
    """
    if not words:
        return []
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: List[List[dict]] = []
    current: List[dict] = []
    current_y = None

    for w in words:
        if current_y is None:
            current_y = w["top"]
            current = [w]
            continue
        if abs(w["top"] - current_y) <= y_tol:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda ww: ww["x0"]))
            current_y = w["top"]
            current = [w]
    if current:
        lines.append(sorted(current, key=lambda ww: ww["x0"]))
    return lines


def find_column_boundaries_from_header(lines: List[List[dict]]) -> Optional[Tuple[float, float, float]]:
    """
    根据表头“单词/音标/词义/标记”的 x 坐标推断列分隔线：
      返回 (b1, b2, b3) ，分别是：
        x < b1 => 单词列
        b1<=x<b2 => 音标列
        b2<=x<b3 => 词义列
        x>=b3 => 标记列
    """
    header_x: Dict[str, float] = {}
    for line in lines[:8]:  # 通常表头在最上面几行
        for w in line:
            txt = clean_text(w["text"])
            if txt in {"单词", "音标", "词义", "标记"}:
                header_x[txt] = (w["x0"] + w["x1"]) / 2

    if not all(k in header_x for k in ("单词", "音标", "词义", "标记")):
        return None

    xs = [header_x["单词"], header_x["音标"], header_x["词义"], header_x["标记"]]
    xs_sorted = sorted(xs)
    # 用相邻表头中心点的中点作为边界
    b1 = (xs_sorted[0] + xs_sorted[1]) / 2
    b2 = (xs_sorted[1] + xs_sorted[2]) / 2
    b3 = (xs_sorted[2] + xs_sorted[3]) / 2
    return (b1, b2, b3)


def assign_line_to_columns(line: List[dict], boundaries: Tuple[float, float, float]) -> Dict[str, str]:
    b1, b2, b3 = boundaries
    cols = {"word": [], "phon": [], "meaning": [], "mark": []}

    for w in line:
        txt = clean_text(w["text"])
        if is_noise_token(txt):
            continue

        x = (w["x0"] + w["x1"]) / 2
        if x < b1:
            cols["word"].append(txt)
        elif x < b2:
            cols["phon"].append(txt)
        elif x < b3:
            cols["meaning"].append(txt)
        else:
            cols["mark"].append(txt)

    return {k: clean_text(" ".join(v)) for k, v in cols.items()}


def extract_list_id_from_page(page) -> Optional[str]:
    """
    在页面上方区域找 "list N"
    """
    # 只看顶部 25% 区域更稳（避免正文干扰）
    top_h = page.height * 0.25
    try:
        cropped = page.crop((0, 0, page.width, top_h))
        txt = cropped.extract_text() or ""
    except Exception:
        txt = page.extract_text() or ""

    m = LIST_RE.search(txt)
    if not m:
        return None
    return normalize_list_id(int(m.group(1)))


def is_mark_cell(s: str) -> bool:
    return bool(s and MARK_RE.search(s))


def strip_phonetic_inline(s: str) -> str:
    s = PHONETIC_INLINE_RE.sub("", s)
    return clean_text(s)


def parse_pdf(pdf_path: str, start_page: int, end_page: int) -> List[Entry]:
    entries: List[Entry] = []
    current_list_id: Optional[str] = None
    last_boundaries: Optional[Tuple[float, float, float]] = None

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        s = max(1, start_page)
        e = min(end_page, total)
        if s > e:
            raise ValueError(f"无效页码范围：start={start_page}, end={end_page}, total={total}")

        pending_word: Optional[str] = None
        pending_meaning_parts: List[str] = []
        pending_list: Optional[str] = None

        for pno in range(s, e + 1):
            page = pdf.pages[pno - 1]

            lid = extract_list_id_from_page(page)
            if lid:
                current_list_id = lid

            # 拿到“词级别”坐标信息；过滤非upright（很多水印是斜的/非upright）
            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=2,
                keep_blank_chars=False,
                extra_attrs=["upright", "size"]
            )
            words = [w for w in words if w.get("upright", True)]

            lines = group_words_to_lines(words, y_tol=3.0)

            boundaries = find_column_boundaries_from_header(lines) or last_boundaries
            if boundaries:
                last_boundaries = boundaries
            else:
                # 实在找不到边界就跳过（避免乱写）
                continue

            for line in lines:
                cols = assign_line_to_columns(line, boundaries)
                wcol = cols["word"]
                mcol = cols["meaning"]
                mark = cols["mark"]

                # 跳过表头行/空行
                if wcol in {"单词", ""} and mcol in {"词义", ""} and (mark in {"标记", ""}):
                    continue

                # 行内清理：有时 meaning/word 里会混入音标片段
                wcol = strip_phonetic_inline(wcol)
                mcol = strip_phonetic_inline(mcol)

                # 若这一行是“仅释义续行”（单词列空但释义列有），拼到 pending
                if (not wcol) and mcol and pending_word:
                    pending_meaning_parts.append(mcol)

                # 如果有新单词开头（word列非空），开始/覆盖 pending
                if wcol:
                    pending_word = wcol
                    pending_list = current_list_id
                    pending_meaning_parts = []
                    if mcol:
                        pending_meaning_parts.append(mcol)

                # 遇到标记列 => 一条结束
                if is_mark_cell(mark) and pending_word and pending_list:
                    meaning = clean_text(" ".join(pending_meaning_parts))
                    # 基本有效性过滤：释义必须含中文或常见词性缩写
                    if meaning and re.search(r"[\u4e00-\u9fff]|adj\.|n\.|v\.|adv\.|phrase\.", meaning, re.IGNORECASE):
                        entries.append(Entry(pending_word, meaning, pending_list))

                    pending_word = None
                    pending_list = None
                    pending_meaning_parts = []

        # 文件结束若还有 pending（少数页最后一条不带标记），尝试补写
        if pending_word and pending_list:
            meaning = clean_text(" ".join(pending_meaning_parts))
            if meaning:
                entries.append(Entry(pending_word, meaning, pending_list))

    # 去重（保序）
    seen = set()
    uniq: List[Entry] = []
    for e in entries:
        key = (e.list_id, e.word, e.meaning)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    return uniq


def write_xlsx(entries: List[Entry], out_path: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "vocab"
    ws.append(["英文单词", "中文释义（含词性及释义）", "章节及单元编号"])

    for e in entries:
        ws.append([e.word, e.meaning, e.list_id])

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 14

    wb.save(out_path)


def main():
    ap = argparse.ArgumentParser(description="Extract vocab table (word + CN meaning + list-id) from PDF to Excel.")
    ap.add_argument("pdf", help="输入PDF路径")
    ap.add_argument("-o", "--out", default="vocab.xlsx", help="输出Excel路径（默认 vocab.xlsx）")
    ap.add_argument("--start", type=int, default=7, help="词汇表起始页（默认7，按PDF页码从1开始）")
    ap.add_argument("--end", type=int, default=154, help="词汇表结束页（默认154）")
    args = ap.parse_args()

    entries = parse_pdf(args.pdf, args.start, args.end)
    if not entries:
        raise SystemExit("未提取到词条：该PDF可能是扫描件/或表头未被识别，需改用OCR方案。")

    write_xlsx(entries, args.out)
    print(f"完成：提取 {len(entries)} 条 -> {args.out}")


if __name__ == "__main__":
    main()
