"""
从 RE词汇书1级别（下）.pdf 提取词汇信息，输出到 result.xls
基于表格结构提取，每个词条占一行，3列：单词+音标、例句、中文释义+英文释义
"""
import pdfplumber
import re
import xlwt
import os
import yaml

# 读取配置文件（放在 doc 目录下）
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'doc', 'config.yaml')
with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# 脚本所在目录（配置文件也在此目录）
SCRIPT_DIR = os.path.dirname(__file__)
# 词汇文件子目录（相对于脚本所在目录）
VOCAB_DIR = os.path.join(SCRIPT_DIR, config['vocab_dir'])

PDF_PATH = os.path.join(VOCAB_DIR, config['pdf_filename'])
OUTPUT_PATH = os.path.join(VOCAB_DIR, config['output_phase12'])


def detect_chapters(text):
    """从页面文本中自动识别章节信息，返回去重后的章节名列表"""
    matches = re.findall(r'Unit\s*(\d+)\s*[·.]\s*List\s*(\d+)', text)
    if not matches:
        return []
    seen = set()
    chapters = []
    for unit, lst in matches:
        key = (unit, lst)
        if key not in seen:
            seen.add(key)
            chapters.append(f'Unit {unit} List {lst}')
    return chapters


def find_vocab_tables(page):
    """从页面中找到词汇表格，按 x 坐标排序返回 (side, table) 列表

    先用默认检测，若发现缺失行则用 explicit 策略重试。
    """
    tables = page.find_tables()
    big_tables = [t for t in tables if (t.bbox[2] - t.bbox[0]) > 100]
    if not big_tables:
        return []

    # 检查是否有缺失行（col0 为空但 col1 有内容）
    has_missing = False
    for t in big_tables:
        for row in t.extract():
            if not row[0] and row[1]:
                has_missing = True
                break
        if has_missing:
            break

    if has_missing:
        retried = _retry_with_explicit_lines(page)
        if retried:
            big_tables = retried

    mid = page.width / 2
    big_tables.sort(key=lambda t: t.bbox[0])
    result = []
    for t in big_tables:
        side = 'left' if t.bbox[0] < mid else 'right'
        result.append((side, t))
    return result


def _snap_values(values, tolerance):
    """将接近的数值合并"""
    if not values:
        return []
    values = sorted(values)
    result = [values[0]]
    for v in values[1:]:
        if v - result[-1] > tolerance:
            result.append(v)
    return result


def _retry_with_explicit_lines(page):
    """用 PDF 中的线条元素重建表格结构"""
    all_lines = page.lines
    v_lines = [l for l in all_lines
               if abs(l['x0'] - l['x1']) < 2 and l['x0'] > 10]
    if not v_lines:
        return None
    v_xs = _snap_values([l['x0'] for l in v_lines], tolerance=40)
    if len(v_xs) < 2:
        return None

    v_top = min(l['top'] for l in v_lines)
    v_bottom = max(l['bottom'] for l in v_lines)

    # 收集水平线，加上垂直线的起止作为表格上下边界
    h_lines = [l for l in all_lines
               if abs(l['top'] - l['bottom']) < 2
               and (l['x1'] - l['x0']) > 100]
    h_ys = _snap_values([l['top'] for l in h_lines], tolerance=5)
    h_ys = sorted(set(
        [round(v_top)] + [round(y) for y in h_ys] + [round(v_bottom)]
    ))

    # 从水平线推断表格左右边界
    if h_lines:
        tbl_left = round(min(l['x0'] for l in h_lines))
        tbl_right = round(max(l['x1'] for l in h_lines))
    else:
        tbl_left = round(v_xs[0] - 90)
        tbl_right = round(v_xs[-1] + 200)

    # 所有垂直线作为同一个表格的内部分隔线
    explicit_vs = sorted(set(
        [tbl_left] + [round(x) for x in v_xs] + [tbl_right]
    ))
    if len(explicit_vs) < 2:
        return None

    settings = {
        'vertical_strategy': 'explicit',
        'horizontal_strategy': 'explicit',
        'explicit_vertical_lines': explicit_vs,
        'explicit_horizontal_lines': h_ys,
    }
    tables = page.find_tables(table_settings=settings)
    big = [t for t in tables if (t.bbox[2] - t.bbox[0]) > 100]
    return big if big else None


def chapter_to_list_id(chapter_str):
    m = re.match(r'Unit (\d+) List (\d+)', chapter_str)
    if not m:
        return None
    unit, lst = int(m.group(1)), int(m.group(2))
    return f'u{unit:02d}-{lst}'


def extract_marker_content(text, marker):
    """从文本中提取指定标记后的内容"""
    idx = text.find(marker)
    if idx < 0:
        return ''
    after = text[idx + len(marker):]
    m = re.search(r'【[中释例]】', after)
    if m:
        after = after[:m.start()]
    return after.strip()


def clean_multiline(text):
    """将多行文本合并为单行"""
    if not text:
        return ''
    return re.sub(r'\s+', ' ', text).strip()


def parse_table_row(row):
    """解析表格的一行，提取词条信息"""
    if len(row) < 3:
        return None
    col0 = row[0] or ''  # 单词 + 音标
    col1 = row[1] or ''  # 【例】例句
    col2 = row[2] or ''  # 【中】中文 + 【释】英文

    # 提取单词名和音标
    # 先合并所有行，用正则提取音标 /..../
    col0_flat = col0.replace('\n', ' ').strip()
    phonetic = ''
    ph_match = re.search(r'/[^/]+/', col0_flat)
    if ph_match:
        phonetic = ph_match.group(0)
        # 单词名 = 音标之前的文本
        word = col0_flat[:ph_match.start()].strip()
    else:
        word = col0_flat

    # 提取中文释义
    chinese = extract_marker_content(col2, '【中】')
    # 提取英文释义
    english_def = extract_marker_content(col2, '【释】')
    # 提取例句
    example = extract_marker_content(col1, '【例】')

    # 清理多行
    word = clean_multiline(word)
    phonetic = clean_multiline(phonetic)
    chinese = clean_multiline(chinese)
    english_def = clean_multiline(english_def)
    example = clean_multiline(example)

    if not word and not phonetic:
        return None

    return {
        'word': word,
        'phonetic': phonetic,
        'chinese': chinese,
        'english_def': english_def,
        'example': example,
    }


def process_table(table, list_id):
    """解析一个表格，返回词条列表"""
    data = table.extract()
    # 处理合并单元格：例句为None时继承上一行的例句
    last_example_cell = None
    for row in data:
        if row[1] is not None:
            last_example_cell = row[1]
        else:
            row[1] = last_example_cell
    entries = []
    for row in data:
        entry = parse_table_row(row)
        if entry:
            entry['chapter'] = list_id
            entries.append(entry)
    return entries


def process_pdf():
    pdf = pdfplumber.open(PDF_PATH)
    all_entries = []
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ''
        chapters = detect_chapters(text)
        if not chapters:
            continue
        vocab_tables = find_vocab_tables(page)
        if not vocab_tables:
            continue

        # 匹配章节和表格
        pairs = []
        if len(vocab_tables) == 1:
            pairs = [(chapters[0], vocab_tables[0])]
        elif len(chapters) >= 2:
            # 多表格多章节：按位置一一对应
            pairs = list(zip(chapters, vocab_tables))
        else:
            # 多表格同一章节
            pairs = [(chapters[0], vt) for vt in vocab_tables]

        for chapter, (side, table) in pairs:
            list_id = chapter_to_list_id(chapter)
            if not list_id:
                continue
            entries = process_table(table, list_id)
            all_entries.extend(entries)
            print(f'  Page {i+1:2d} {side:5s} ({list_id}): '
                  f'{len(entries)} entries')
    pdf.close()
    return all_entries


def write_row(ws, row, entry, style=None):
    """写入一行数据"""
    fields = [entry.get('word', ''), entry.get('chinese', ''),
              entry.get('chapter', ''), entry.get('phonetic', ''),
              entry.get('english_def', ''), entry.get('example', '')]
    for c, val in enumerate(fields):
        if style:
            ws.write(row, c, val, style)
        else:
            ws.write(row, c, val)


def write_excel(entries):
    wb = xlwt.Workbook(encoding='utf-8')
    headers = ['英文词汇', '中文词性及释义', '归属章节',
               '音标', '英文释义', '例句']

    # 样式
    style_yellow = xlwt.easyxf('pattern: pattern solid, fore_colour light_yellow;')
    style_orange = xlwt.easyxf('pattern: pattern solid, fore_colour light_orange;')
    style_blue = xlwt.easyxf('pattern: pattern solid, fore_colour light_blue;')

    # 找出重复词汇：word -> [index list]
    word_indices = {}
    for i, e in enumerate(entries):
        w = e.get('word', '')
        word_indices.setdefault(w, []).append(i)
    dup_words = {w: idxs for w, idxs in word_indices.items() if len(idxs) > 1}

    # === 1原表 ===
    ws1 = wb.add_sheet('1原表')
    for c, h in enumerate(headers):
        ws1.write(0, c, h)
    for r, e in enumerate(entries, 1):
        w = e.get('word', '')
        if w in dup_words:
            first_idx = dup_words[w][0]
            style = style_yellow if (r - 1) == first_idx else style_orange
            write_row(ws1, r, e, style)
        else:
            write_row(ws1, r, e)

    # === 2去重 ===
    ws2 = wb.add_sheet('2去重及字符过长')
    for c, h in enumerate(headers):
        ws2.write(0, c, h)
    # 构建去重表：保持原序，重复条目挪到首次出现的紧邻下方
    used = set()  # 已写入的原始索引
    out_rows = []
    for i, e in enumerate(entries):
        if i in used:
            continue
        w = e.get('word', '')
        if w in dup_words:
            # 首次出现 + 所有重复项按原序排列
            for idx in dup_words[w]:
                out_rows.append((idx, entries[idx]))
                used.add(idx)
        else:
            out_rows.append((i, e))
            used.add(i)
    for r, (orig_idx, e) in enumerate(out_rows, 1):
        chinese_len = len(e.get('chinese', ''))
        if chinese_len > 50:
            write_row(ws2, r, e, style_blue)
        elif e.get('word', '') in dup_words:
            first_idx = dup_words[e['word']][0]
            style = style_yellow if orig_idx == first_idx else style_orange
            write_row(ws2, r, e, style)
        else:
            write_row(ws2, r, e)

    wb.save(OUTPUT_PATH)
    print(f'\nSaved {len(entries)} entries to {OUTPUT_PATH}')
    print(f'Duplicated words: {len(dup_words)}')


if __name__ == '__main__':
    print('Extracting vocabulary from PDF...')
    entries = process_pdf()
    write_excel(entries)
    print('Done!')
