# 一堂课程工具集

一堂(yitang.top)课程内容提取工具，包含文档复制、视频下载、音频转字幕、字幕校订、文档导出、新课一键处理六个功能。

## 环境准备

```bash
pip install requests cryptography pyyaml opencc-python-reimplemented
# 音频转字幕额外依赖（按需安装）
pip install faster-whisper websocket-client oss2
# CUDA 加速（可选，需 NVIDIA 显卡 + 驱动）
pip install nvidia-cublas-cu12
# 字幕校订额外依赖（LLM 调用，已包含在 requests 中）
# 无额外依赖
```

## 配置文件

配置文件位于 `cfg/` 目录：

- `credentials.yaml` — 敏感凭证（不入库）
- `config-wiki.yaml` — 文档复制配置
- `config-video.yaml` — 视频下载配置
- `config-addon.yaml` — 讨论区精华提取配置
- `config-srtfix.yaml` — 字幕校订配置
- `prompt-srtfix-ref.md` / `prompt-srtfix-noref.md` — 校订提示词
- `srtfix-dict.yaml` — 字幕校订自定义词典

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
自动以文档标题导出本地 md 文件到 `localscript/` 目录。

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

**容错**：`start_heading` 找不到时自动 fallback 全文复制；mapping 可配置 `full_copy: true` 跳过章节过滤

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

# 强制 CPU 模式（GPU 显存不足时使用）
python src/subtitle_from_mp3.py ailive/xxx.mp3 --cpu
```

**输出**：字幕文件保存到音频同目录，格式为 `.srt`，日志记录到 `log-err/subtitle.log`。

---

## 功能四：字幕校订

基于 LLM + 逐字稿参考 + 自定义词典，纠正 Whisper 字幕中的同音字、专有名词、乱码错误。

```bash
# 正常运行（按 config-srtfix.yaml 配置）
python src/yitang_srt_fix.py

# 试运行（只解析不调用 LLM）
python src/yitang_srt_fix.py --dry-run

# 仅词典替换，不调用 LLM
python src/yitang_srt_fix.py --dict-only

# 覆盖配置中的文件路径
python src/yitang_srt_fix.py --subtitle xxx.srt --transcript xxx.md

# 不使用断点缓存，强制重新调用 LLM
python src/yitang_srt_fix.py --no-cache
```

**处理流程**：
1. 词典替换（`srtfix-dict.yaml`）— 修正已知的固定错误
2. LLM 逐段纠正 — 每次送 80 条字幕，对照逐字稿参考纠正语音识别错误
3. 输出 `_fix.srt` 校订版字幕 + `_fix_changelog.md` 修正日志

**后处理过滤**（可选）：LLM 有时会过度纠正（删语气词、口语书面化等），用 `filter_changelog.py` 过滤：

```bash
python src/filter_changelog.py localscript/xxx_fix_changelog.md
```

**配置文件**：
- `cfg/config-srtfix.yaml` — 输入文件、LLM provider、输出目录
- `cfg/prompt-srtfix-ref.md` — 有逐字稿参照时的提示词
- `cfg/prompt-srtfix-noref.md` — 无逐字稿参照时的提示词
- `cfg/srtfix-dict.yaml` — 自定义纠正词典（错误写法: 正确写法）

---

## 功能五：文档导出

将飞书/一堂文档 URL 导出为本地 Markdown 文件。

```bash
# 导出飞书文档
python src/url2md.py "https://xxx.feishu.cn/docx/xxx"

# 导出一堂文档
python src/url2md.py "https://yitang.top/fs-doc/xxx/xxx"

# 指定输出路径
python src/url2md.py "https://xxx.feishu.cn/wiki/xxx" -o output.md
```

支持的 URL 格式：飞书 docx、飞书 wiki、一堂 fs-doc。默认保存到 `localscript/` 目录。

---

## 功能六：新课一键处理

串联多个工具完成新课的完整处理流程，任一步骤失败自动发送飞书群通知。

修改 `src/go-newlesson.py` 顶部的 CONFIG 区后直接运行：

```bash
python src/go-newlesson.py
```

**处理步骤**：
1. 更新 config-wiki.yaml → 运行 yitang_wiki.py（写入 wiki + 生成本地 md）
2. 更新 config-video.yaml → 运行 yitang_video.py（下载视频/音频/讨论区）
3. 重命名 md 文件与视频文件名一致
4. 运行 subtitle_from_mp3.py 生成字幕
5. 移动 ts/mp3 到 NAS 目录

**CONFIG 配置项**：`transcript_url`（逐字稿 URL）、`replay_url`（回放 URL）、`target_wiki_url`（wiki 目标）、`start_heading`/`end_heading`（内容过滤）、`nas_dir`（NAS 目录）

---

## 注意事项

1. 目标飞书文档需提前创建好（空文档即可），内容追加到末尾不覆盖
2. `--resume` 仅在 block 总数未变时生效
3. 一堂 Cookie 过期后需从浏览器重新获取并更新 credentials.yaml
4. 飞书 refresh_token 约 30 天有效，过期后重新运行 `python src/auth.py`
5. 同步块内的文件附件无法下载（飞书应用缺少 drive:drive:readonly 权限）
