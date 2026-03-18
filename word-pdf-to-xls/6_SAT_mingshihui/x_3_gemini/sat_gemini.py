import pdfplumber
import pandas as pd
import re

def extract_sat_vocabulary(pdf_path, output_excel):
    all_data = []
    current_list = "unknown"
    
    # 词性匹配正则，用于清洗中文释义（目前暂未使用，保留以便后续扩展）
    pos_pattern = re.compile(r'(n\.|v\.|adj\.|adv\.|phrase\.|prep\.|conj\.)')

    with pdfplumber.open(pdf_path) as pdf:
        # 第7页到154页 (索引为 6 到 153)
        for i in range(6, 154):
            page = pdf.pages[i]
            text = page.extract_text() or ""
            
            # 1. 识别当前单元编号 (List-XX)
            list_match = re.search(r'list\s*(\d+)', text, re.IGNORECASE)
            if list_match:
                current_list = f"list-{list_match.group(1).zfill(2)}"
            
            # 2. 提取表格（使用默认设置；在本 PDF 上识别率更高）
            table = page.extract_table()
            if not table:
                continue

            for row in table:
                if not row:
                    continue

                # 转成字符串并去掉首尾空格
                cells = [str(c or "").strip() for c in row]

                # 跳过表头（通常会包含“单词”等字样）
                if any("单词" in c for c in cells):
                    continue
                # 跳过整行空白
                if all(not c for c in cells):
                    continue

                # 在一整行里智能寻找“英文单词”和“中文释义”所在的单元格
                word_candidates = []
                def_candidates = []
                for c in cells:
                    # 含有英文字母的单元格，作为单词候选
                    if re.search(r"[A-Za-z]", c):
                        word_candidates.append(c)
                    # 含有中文或词性缩写的单元格，作为释义候选
                    if re.search(r"[\u4e00-\u9fff]", c) or pos_pattern.search(c):
                        def_candidates.append(c)

                if not word_candidates or not def_candidates:
                    continue

                word_raw = word_candidates[0]
                definition_raw = def_candidates[0]
                
                # 3. 数据清洗：过滤掉 PDF 里的“标记”字符 (M1234567 等)
                word_cleaned_cell = re.sub(r'Ⓜ|[①-⑦]|M|\d+', '', word_raw).strip()
                def_cleaned_cell = re.sub(r'Ⓜ|[①-⑦]|M', '', definition_raw).strip()

                # 只取每个单元格的第一行，避免因为换行导致的错位和多余噪声（如“师”“名”等）
                clean_word = word_cleaned_cell.split('\n')[0].strip()
                clean_def = def_cleaned_cell.split('\n')[0].strip()
                
                # 如果单词和定义都不为空，则加入列表（每一行对应一条记录）
                if clean_word and clean_def:
                    all_data.append({
                        "英文单词": clean_word,
                        "中文释义（含词性及释义）": clean_def,
                        "章节单元编号": current_list
                    })

    # 4. 导出为 Excel
    df = pd.DataFrame(all_data)
    df.drop_duplicates(inplace=True) # 去重
    df.to_excel(output_excel, index=False)

    # 打印前 10 行，方便检查提取结果是否正确
    print(f"提取完成！共计 {len(df)} 个词条已保存至 {output_excel}")
    print("前 10 行示例：")
    print(df.head(10).to_string(index=False))

# 使用方法：
# 1. 安装库: pip install pdfplumber pandas openpyxl
# 2. 运行脚本:
# extract_sat_vocabulary("4 .SAT初级词汇-水印.pdf", "SAT_Vocabulary_Full.xlsx")

if __name__ == "__main__":
    extract_sat_vocabulary("4 .SAT初级词汇-水印.pdf", "SAT_Vocabulary_Full.xlsx")
