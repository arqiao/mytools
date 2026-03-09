# 一堂课程工具集

一堂(yitang.top)课程内容提取工具，包含文档复制、视频下载、音频转字幕三个功能。

## 环境准备

```bash
pip install requests cryptography pyyaml opencc-python-reimplemented
# 音频转字幕额外依赖（按需安装）
pip install faster-whisper websocket-client oss2
# CUDA 加速（可选，需 NVIDIA 显卡 + 驱动）
pip install nvidia-cublas-cu12
```

## 配置文件

配置文件位于 `cfg/` 目录：

- `credentials.yaml` — 敏感凭证（不入库）
- `config-wiki.yaml` — 文档复制配置
- `config-video.yaml` — 视频下载配置

### credentials.yaml

首次使用需获取以下凭证：

**一堂凭证**（从浏览器开发者工具获取）：
1. 登录 yitang.top，打开任意课程文档
2. F12 → Network → 找到 `get-doc-blocks` 请求
3. 从请求头中复制 Cookie 值
4. token 和 request_id 从页面 JS 或请求参数中提取

**飞书凭证**：
1. 在飞书开放平台创建应用，获取 app_id 和 app_secret
2. 运行 `python src/auth.py` 完成 OAuth 授权
3. token 过期后程序自动用 refresh_token 刷新

### config-wiki.yaml

```yaml
mappings:
  - source_url: "https://yitang.top/fs-doc/{acl}/{doc_token}"
    target_url: "https://xxx.feishu.cn/wiki/{wiki_token}"
    # 标题自动编号（可选）
    heading_number:
      start_heading: "章节标题"  # 从哪个标题开始编号（精确匹配）
      end_heading: ""            # 留空则编号到末尾

# 也支持飞书原始文档作为数据源
  - source_url: "https://xxx.feishu.cn/docx/{doc_token}"
    target_url: "https://xxx.feishu.cn/wiki/{wiki_token}"

content_range:
  start_heading: "开始上课"
  end_heading: "作业与Candy"

# 标题匹配此关键词时，复制全文不进行章节过滤
full_copy_titles:
  - "AI落地Live"
```

---

## 功能一：复制文档到飞书

将一堂课程文稿复制到飞书 wiki 文档，保留排版和格式。
也支持飞书原始文档（`feishu.cn/docx/...`）作为数据源。

```bash
# 正常运行
python src/yitang_wiki.py

# 试运行（只解析不写入，验证源数据）
python src/yitang_wiki.py --dry-run

# 断点恢复（从上次中断位置继续）
python src/yitang_wiki.py --resume
```

**支持的内容类型**：文字段落、标题（heading1~9，支持自动编号）、有序/无序列表、代码块、图片、表格（含合并单元格）、多列布局(Grid)、高亮块(Callout)、引用容器、分割线

**不支持的类型**：同步块（展平子节点处理）、file 块（转为"📎 文件名"占位）、嵌入表格、倒计时

**输出**：
- 控制台实时进度
- `log-err/yitang_wiki.log` — 完整运行日志
- `log-err/skipped_*.log` — 跳过内容记录
- `temp_images/` — 图片缓存（可清理）

---

## 功能二：下载视频/音频/讨论区

```bash
# 下载视频（m3u8 → mp4，需要 yt-dlp）
python src/yitang_video.py

# 仅下载讨论区文字
python src/yitang_video.py --chat

# 仅下载时间戳
python src/yitang_video.py --ts

# 仅下载音频(mp3)
python src/yitang_video.py --mp3
```

下载文件保存到 `ailive/` 目录。

---

## 功能三：音频转字幕

支持多种转写引擎，可按需选择。Whisper 支持 CUDA 加速（自动检测 GPU）。

```bash
# Whisper 模型管理
python src/model_downloader.py --list          # 查看模型状态
python src/model_downloader.py medium          # 下载 medium 模型

# 默认使用 Whisper medium 模型（自动检测 CUDA）
python src/subtitle_from_mp3.py ailive/AI落地Live_069.mp3

# 指定引擎
python src/subtitle_from_mp3.py ailive/xxx.mp3 --xunfei    # 讯飞
python src/subtitle_from_mp3.py ailive/xxx.mp3 --aliyun    # 阿里云
python src/subtitle_from_mp3.py ailive/xxx.mp3 --doubao    # 豆包(火山引擎)
python src/subtitle_from_mp3.py ailive/xxx.mp3 --feishu    # 飞书（不支持时间戳）
python src/subtitle_from_mp3.py ailive/xxx.mp3 --whisper   # Whisper
python src/subtitle_from_mp3.py ailive/xxx.mp3 --all       # 全部引擎

# Whisper 模型大小
python src/subtitle_from_mp3.py ailive/xxx.mp3 --model small
```

**输出**：字幕文件保存到音频同目录，格式为 `.srt`，日志记录到 `log-err/subtitle.log`。

---

## 注意事项

1. 目标飞书文档需提前创建好（空文档即可），内容追加到末尾不覆盖
2. `--resume` 仅在 block 总数未变时生效
3. 一堂 Cookie 过期后需从浏览器重新获取并更新 credentials.yaml
4. 飞书 refresh_token 约 30 天有效，过期后重新运行 `python src/auth.py`
5. 同步块内的文件附件无法下载（飞书应用缺少 drive:drive:readonly 权限）
