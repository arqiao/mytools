# 快速使用指南

## 前提

```bash
pip install -r doc/requirements.txt
```

## 配置

编辑 `doc/config.yaml`，设置词汇子目录和 PDF 文件名。

## 步骤

### 1. 提取词汇

```bash
python extract_vocab.py
```

在 `<vocab_dir>/` 下生成 `result_phase12.xls`

### 2. 手工去重

用 Excel 打开"3去重后"Sheet，合并重复词汇，保存。

颜色含义：
- 黄色 — 重复词汇首次出现
- 橙色 — 重复词汇后续出现
- 蓝色 — 中文释义超过50字符（过长）

### 3. 分类导出

```bash
python split_upload.py
```

在 `<vocab_dir>/` 下生成 `result_phase4.xls`，含"5拟上传"和"5不上传"两个 Sheet。

## 常见问题

- Q: 提示"duplicate worksheet name"？
  - A: 删除旧的 result_phase12.xls 重新执行 Step 1

- Q: 读取格式失败？
  - A: 确认 xlrd 版本为 1.2.0：`pip install xlrd==1.2.0`

- Q: 切换词汇书？
  - A: 修改 `doc/config.yaml` 中的 `vocab_dir` 和 `pdf_filename`
