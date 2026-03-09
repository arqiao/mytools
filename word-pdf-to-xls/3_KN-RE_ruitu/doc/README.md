# 睿途词汇书提取工具

## 项目概述

从睿途系列词汇书 PDF 提取词汇信息，输出为 Excel 格式。
支持 RE 系列和 KN 系列词汇书，通过 `doc/config.yaml` 配置输入输出。

## 文件说明

```
3_RE_ruitu/
├── doc/
│   ├── config.yaml        # 配置文件（词汇目录、PDF文件名、输出文件名）
│   ├── README.md          # 本文件
│   ├── requirements.txt   # Python 依赖
│   └── 原始需求.txt
├── extract_vocab.py       # 主程序（Phase1+2：PDF提取→去重→输出）
├── split_upload.py        # Phase4：按规则分流到拟上传/不上传
├── QUICKSTART.md          # 快速使用指南
└── <vocab_dir>/           # 词汇子目录（由config.yaml指定）
    ├── *.pdf              # 源PDF
    ├── result_phase12.xls # Phase1+2输出
    └── result_phase4.xls  # Phase4输出
```

## 配置说明

编辑 `doc/config.yaml`：

```yaml
vocab_dir: "2603-K3下"                              # 词汇子目录
pdf_filename: "6. 睿途国际词汇书排版3.0-K3下.pdf"    # PDF文件名
output_phase12: "result_phase12.xls"                 # Phase1+2输出
output_phase4: "result_phase4.xls"                   # Phase4输出
```

## 输出格式

### result_phase12.xls（3个Sheet）

| Sheet名称 | 说明 |
|---------|------|
| 1原表 | 全部词汇，含颜色标记 |
| 2去重及字符过长 | 去重后，含超长标记 |
| 3去重后 | 手工去重后的最终版本（待用户填写） |

### 列序

英文词汇 | 中文词性及释义 | 归属章节 | 音标 | 英文释义 | 例句

### 颜色标记

- 浅黄色：重复词汇首次出现
- 浅橙色：重复词汇后续出现
- 浅蓝色（2去重及字符过长）：中文释义超过50字符

## 运行步骤

### Step 1: 提取词汇

```bash
python extract_vocab.py
```

输出：`<vocab_dir>/result_phase12.xls`

### Step 2: 手工去重

打开 result_phase12.xls 的"3去重后"Sheet，手工合并重复词汇。

### Step 3: 分类导出

```bash
python split_upload.py
```

读取"3去重后"内容，按规则分流输出 `<vocab_dir>/result_phase4.xls`：

- 5拟上传：符合上传条件的词汇
- 5不上传：含特殊字符或需要审核的词汇

## 分类规则（split_upload.py）

1. 只放"5不上传"：英文词汇含 `'` 或 `...`
2. 同时放两边：背景色为蓝色的条目（中文释义过长）
3. 只放"5拟上传"：其余正常词汇

## 技术细节

- 自动检测章节标题（"Unit X · List Y"），无需硬编码章节布局
- 默认表格检测失败时，使用 PDF 线条元素重建表格结构（explicit lines retry）
- 使用 `pdfplumber` 提取 PDF 表格
- 使用 `xlwt` / `xlrd==1.2.0` / `xlutils` 读写带格式的 Excel
- 使用 `pyyaml` 读取配置

## 依赖

```bash
pip install -r doc/requirements.txt
```
