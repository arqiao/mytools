import pandas as pd
import re

# 读取文件内容
with open('睿途托福核心词汇2.0 正文.pdf', 'rb') as file
    content = file.read().decode('utf-8')

# 提取单词、词性、释义、List编号和页码号的正则表达式
pattern = re.compile(r'b(d+)b.s+(w+)s+([ws.,()]+)s+(w+)s+[(d+)]')

# 查找所有匹配项
matches = pattern.findall(content)

# 组织数据
data = []
for match in matches
    index, word, definition, pos, page = match
    data.append([int(index), pos, word, definition.strip(), int(page)])

# 创建DataFrame
df = pd.DataFrame(data, columns=['序号', '词性', '英文单词', '中文释义', 'List编号', '页码号'])

# 保存为Excel文件
df.to_excel('listall.xlsx', index=False)
