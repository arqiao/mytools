"""
第四阶段：读取 result_phase12.xls 中的"3去重后"表格，
按规则分流到"5拟上传"和"5不上传"两个新 sheet。

规则：
1) 英文词汇含 ' 或 ... → 只放"5不上传"
2) 背景色为蓝色 → 同时放"5拟上传"和"5不上传"
3) 其余 → 只放"5拟上传"
"""
import xlrd
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

XLS_PATH = os.path.join(VOCAB_DIR, config['output_phase12'])
OUTPUT_PATH = os.path.join(VOCAB_DIR, config['output_phase4'])


def get_blue_color_indices(workbook):
    """获取蓝色系的颜色索引集合"""
    blue_indices = set()
    colour_map = workbook.colour_map
    for idx, rgb in colour_map.items():
        if rgb is None:
            continue
        r, g, b = rgb
        if b > 200 and b > r and b > g:
            blue_indices.add(idx)
        if idx == 24:
            blue_indices.add(idx)
    return blue_indices


def is_blue_bg(workbook, sheet, row_idx, blue_indices):
    """判断某行是否有蓝色背景"""
    xf_idx = sheet.cell_xf_index(row_idx, 0)
    xf = workbook.xf_list[xf_idx]
    bg_colour = xf.background.pattern_colour_index
    return bg_colour in blue_indices


def read_source_sheet(workbook):
    """读取索引2的sheet，返回 (headers, rows_data)
    rows_data: list of (values, bg_xf_idx)
    """
    sheet = workbook.sheet_by_index(2)
    blue_indices = get_blue_color_indices(workbook)
    headers = [sheet.cell_value(0, c) for c in range(sheet.ncols)]
    rows_data = []
    for r in range(1, sheet.nrows):
        values = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
        bg_xf_idx = sheet.cell_xf_index(r, 0)
        rows_data.append((values, bg_xf_idx))
    return headers, rows_data


def get_xlwt_style(bg_xf_idx, workbook):
    """根据原 xf 索引创建 xlwt 样式"""
    if bg_xf_idx is None:
        return None
    xf = workbook.xf_list[bg_xf_idx]
    bg = xf.background
    colour_idx = bg.pattern_colour_index

    # 跳过无填充 (64) 和 None
    if colour_idx is None or colour_idx == 64:
        return None

    # xlrd 颜色索引转 xlwt 颜色名
    colour_name_map = {
        22: 'gray25', 23: 'gray50', 24: 'periwinkle', 26: 'ivory',
        43: 'light_yellow', 44: 'pale_blue', 45: 'rose', 46: 'lavender',
        47: 'tan', 48: 'light_blue', 49: 'aqua', 50: 'lime',
        51: 'gold', 52: 'light_orange',
    }

    colour_name = colour_name_map.get(colour_idx)
    if colour_name:
        return xlwt.easyxf(f'pattern: pattern solid, fore_colour {colour_name};')
    return None


def classify_row(values, bg_xf_idx, workbook, blue_indices):
    """分类一行数据"""
    word = str(values[0]) if values else ''
    # 规则1
    if "'" in word or '...' in word:
        return 'no_upload'
    # 规则2：检查是否为蓝色背景
    if bg_xf_idx is not None:
        xf = workbook.xf_list[bg_xf_idx]
        bg_colour = xf.background.pattern_colour_index
        if bg_colour in blue_indices:
            return 'both'
    # 规则3
    return 'upload'


def main():
    rb = xlrd.open_workbook(XLS_PATH, formatting_info=True)
    headers, rows_data = read_source_sheet(rb)
    blue_indices = get_blue_color_indices(rb)

    wb = xlwt.Workbook(encoding='utf-8')
    ws_upload = wb.add_sheet('5拟上传')
    ws_no_upload = wb.add_sheet('5不上传')

    # 写表头
    for c, h in enumerate(headers):
        ws_upload.write(0, c, h)
        ws_no_upload.write(0, c, h)

    r_up, r_no = 1, 1
    stats = {'upload': 0, 'no_upload': 0, 'both': 0}

    for values, bg_xf_idx in rows_data:
        cat = classify_row(values, bg_xf_idx, rb, blue_indices)
        style = get_xlwt_style(bg_xf_idx, rb)
        stats[cat] += 1

        if cat == 'upload':
            for c, v in enumerate(values):
                if style:
                    ws_upload.write(r_up, c, v, style)
                else:
                    ws_upload.write(r_up, c, v)
            r_up += 1
        elif cat == 'no_upload':
            for c, v in enumerate(values):
                if style:
                    ws_no_upload.write(r_no, c, v, style)
                else:
                    ws_no_upload.write(r_no, c, v)
            r_no += 1
        else:  # both
            for c, v in enumerate(values):
                if style:
                    ws_upload.write(r_up, c, v, style)
                    ws_no_upload.write(r_no, c, v, style)
                else:
                    ws_upload.write(r_up, c, v)
                    ws_no_upload.write(r_no, c, v)
            r_up += 1
            r_no += 1

    wb.save(OUTPUT_PATH)
    total = sum(stats.values())
    print(f'从"3去重后"读取 {total} 条')
    print(f'  只放拟上传: {stats["upload"]}')
    print(f'  只放不上传: {stats["no_upload"]}')
    print(f'  两边都放:   {stats["both"]}')
    print(f'5拟上传: {r_up - 1} 条, 5不上传: {r_no - 1} 条')
    print(f'Saved to {OUTPUT_PATH}')


if __name__ == '__main__':
    main()
