# 一堂课程资料导出工具

## 1. 背景与目标

"一堂"(yitang.top) 是一个在线课程平台，课程文档以飞书格式存储但通过自有前端展示，无法直接导出；课程视频以 m3u8/mp3 形式在线播放，不提供下载入口。

目标：提供一组 Python 小工具，将一堂课程的文稿、视频、音频、讨论区内容导出到本地或飞书知识库，方便归档和二次利用。

工具列表：
- **yitang_wiki.py** — 文档复制：一堂文稿/飞书文档 → 飞书 wiki 文档
- **yitang_video.py** — 视频/音频/讨论区下载
- **subtitle_from_mp3.py** — 音频转字幕（多引擎，Whisper 支持 CUDA 加速）
- **model_downloader.py** — Whisper 模型下载管理（HuggingFace 镜像加速）
- **yitang_srt_fix.py** — 字幕校订（LLM + 逐字稿参考 + 词典替换）
- **filter_changelog.py** — 字幕校订后处理过滤器
- **url2md.py** — 飞书/一堂文档 URL → 本地 Markdown 导出
- **go-newlesson.py** — 新课一键处理（串联 wiki→video→subtitle→NAS）

## 2. 工具一：文档复制（yitang_wiki.py）

### 2.0 功能概述

将一堂课程文稿自动复制到飞书 wiki 文档，尽可能保留原始排版和格式。

### 2.1 内容提取

- 从一堂 API 获取文档的 block 结构（AES-256-CBC 加密传输）
- 也支持飞书原始文档作为数据源（`feishu.cn/docx/...`），通过飞书 docx API 读取 blocks 并转换为统一格式
- 按章节范围过滤：从"开始上课"到"作业与Candy"章节（含），前后内容不复制
- 标题匹配 `full_copy_titles` 关键词时，复制全文不进行章节过滤
- 不复制源文档总标题（Page 根节点）

### 2.2 支持的 Block 类型

| 类型 | 说明 | 处理方式 |
|------|------|----------|
| text (2) | 正文段落 | 保留文字内容及样式（加粗/斜体/删除线/下划线/行内代码/链接/颜色） |
| heading1~9 (3-11) | 各级标题 | 保留标题级别和文字样式 |
| ordered_list (12) | 有序列表 | 保留列表层级（支持嵌套缩进） |
| bullet_list (13) | 无序列表 | 同上 |
| code (14) | 代码块 | 保留语言类型和代码内容 |
| callout (19) | 高亮块 | 保留容器及子节点 |
| grid (24) | 多列布局 | 保留列数、列宽比例、各列内容；超过 5 列自动拆分 |
| image (27) | 图片 | 从 CDN 下载后上传到目标飞书文档 |
| table (31) | 表格 | 保留行列结构、单元格内容、合并单元格 |
| quote_container (34) | 引用容器 | 保留容器及子节点 |
| divider (22) | 分割线 | 直接复制 |

### 2.3 不支持的 Block 类型

| 类型 | 说明 |
|------|------|
| file/视频 (23) | 转为"📎 文件名"文字段落（飞书 API 不支持创建 file block） |
| synced_block/同步块 (33) | 跳过容器，展平子节点逐个处理 |
| sheet/嵌入表格 (30) | 记录到跳过日志 |
| add_ons/倒计时 (40) | 记录到跳过日志 |

- file block：无法通过飞书 API 创建，改为显示"📎 文件名"的文字段落作为占位
- 同步块：飞书 API 不支持创建同步块容器，跳过容器后将子节点展平处理。同步块内的文件附件 token 无法通过 drive API 下载（应用缺少 drive:drive:readonly 权限）
- 其他不支持的类型：记录其类型、原因、上下文位置到日志文件，不中断执行

### 2.4 写入目标文档

- 内容追加到目标飞书文档末尾（不覆盖已有内容）
- 每批最多 20 个 block，批间延迟 0.3s
- 图片三步写入：创建空 image block → 上传文件 → replace_image 绑定
- 容器类型（callout/quote_container/grid/table）创建后自动删除自带的空 paragraph，避免空行
- 容器内可嵌套任意特殊类型（图片、grid、table、嵌套列表等），由 _write_column_content 统一处理
- 表格合并单元格通过 batch_update + merge_table_cells 实现
- Grid 列宽通过 batch_update + update_grid_column_width_ratio 设置
- 有序列表编号保留 style.sequence 字段（"1"=首项, "auto"=续编）

### 2.5 标题自动编号

- 在 config-wiki.yaml 的每个 mapping 中可配置 `heading_number`
- `start_heading`：从哪个标题开始编号（精确匹配标题文本）
- `end_heading`：到哪个标题结束编号（留空则到末尾）
- 支持多级编号（如 4.1.2.1），自动根据标题层级递增/重置
- 飞书原始文档和一堂文档均支持

### 2.6 容错与恢复

- 飞书 API 429 限流：自动重试最多 3 次（递增等待 3s/6s/9s）
- 批量写入失败时降级为逐个 block 写入
- 断点恢复：通过 `--resume` 参数从上次中断位置继续
- 飞书 user_access_token 过期自动刷新
- `start_heading` 找不到时自动 fallback 全文复制
- mapping 支持 `full_copy: true` 跳过章节过滤

### 2.7 本地 Markdown 导出

- 写入飞书的同时，自动以文档标题导出本地 md 文件到 `localscript/` 目录
- 文件名取文档标题（去除特殊字符），如 `AI落地Live_069.md`

## 3. 工具二：视频/音频/讨论区下载（yitang_video.py）

### 3.1 功能

从一堂课程页面提取视频回放、音频、讨论区聊天记录，下载到本地 `ailive/` 目录。

### 3.2 数据来源

- 视频：`replay.url`（m3u8 格式，需 yt-dlp 转 mp4）
- 音频：`replay.audioUrl`（mp3 直链）
- 讨论区：`chats` 数组（含时间 offset、发言人、消息内容、icons 标签）
- 字幕：一堂没有单独的字幕 API，需通过音频转字幕工具生成

### 3.3 运行模式

| 参数 | 说明 |
|------|------|
| （无参数） | 下载视频（m3u8 → mp4） |
| `--mp3` | 仅下载音频 |
| `--chat` | 仅下载讨论区文字 |
| `--ts` | 仅下载时间戳 |

### 3.4 依赖

- yt-dlp（视频下载需要，需提前安装到 PATH）

## 4. 工具三：音频转字幕（subtitle_from_mp3.py）

### 4.1 功能

将 mp3 音频文件转写为带时间戳的字幕文本，支持多种转写引擎。

### 4.2 支持的引擎

| 引擎 | 参数 | 说明 |
|------|------|------|
| Whisper | `--whisper` | 本地模型，默认 medium，可选 tiny/small/large；自动检测 CUDA 加速 |
| Whisper CPU | `--cpu` | 强制 CPU 模式（GPU 显存不足时使用） |
| 讯飞 | `--xunfei` | 讯飞开放平台 API |
| 阿里云 | `--aliyun` | 阿里云智能语音 API |
| 豆包 | `--doubao` | 火山引擎 API |
| 飞书 | `--feishu` | 飞书语音转写（不支持时间戳） |
| 全部 | `--all` | 同时使用所有引擎 |

### 4.3 Whisper 本地转写

- 模型管理：`model_downloader.py` 负责下载和缓存检查，支持 HuggingFace 镜像加速
- CUDA 加速：自动检测 GPU，需安装 `nvidia-cublas-cu12`（DLL 路径自动注入 PATH）
- 本地加载：优先使用缓存路径加载模型，避免每次联网检查版本
- 长音频分段：超过 10 分钟的音频自动分段处理（每段 3 分钟，5 秒重叠）
- 繁体转简体：使用 OpenCC 自动转换

### 4.4 依赖

- faster-whisper + ctranslate2（Whisper 引擎）
- nvidia-cublas-cu12（CUDA 加速，可选）
- opencc-python-reimplemented（繁简转换）
- websocket-client（讯飞引擎）
- oss2（阿里云引擎）
- 各引擎的 API 凭证配置在 credentials.yaml 中

## 5. 工具四：字幕校订（yitang_srt_fix.py）

### 5.1 功能

基于 LLM + 逐字稿参考 + 自定义词典，纠正 Whisper 字幕中的语音识别错误。

### 5.2 处理流程

```
原始 SRT ──→ 解析字幕条目
                │
          apply_dict_fixes()
          └─ 第一轮：自定义词典字符串替换（srtfix-dict.yaml）
                │
          run_llm_fix()
          ├─ 加载逐字稿（无/本地文件/飞书URL 三种模式）
          ├─ 提取专有名词（英文词、引号术语）
          ├─ 分段（每段 80 条）送 LLM 纠正
          ├─ 实时缓存分段结果（断点续传）
          └─ 第二轮：LLM 返回 JSON 修正列表
                │
          apply_llm_fixes()
          └─ 将修正应用到字幕条目
                │
          输出 _fix.srt + _fix_changelog.md
```

### 5.3 LLM 校订策略

- 提示词区分有参照（`prompt-srtfix-ref.md`）和无参照（`prompt-srtfix-noref.md`）两种模式
- 有参照模式：对照逐字稿，只纠正"听错的字"（同音字、专有名词、乱码），不做口语优化
- 无参照模式：仅纠正明显的专有名词和同音字错误
- 课程领域知识内嵌提示词（龙虾=OpenClaw、一堂=平台名等）

### 5.4 后处理过滤（filter_changelog.py）

LLM 即使在提示词中明确禁止，仍可能过度纠正（删语气词、口语书面化等）。`filter_changelog.py` 按关键词列表过滤这类条目，输出 `_filtered.md` 供人工复核。

### 5.5 配置

| 文件 | 说明 |
|------|------|
| `config-srtfix.yaml` | 输入文件、LLM provider/model、输出目录、chunk_size |
| `prompt-srtfix-ref.md` | 有逐字稿参照的提示词 |
| `prompt-srtfix-noref.md` | 无逐字稿参照的提示词 |
| `srtfix-dict.yaml` | 自定义纠正词典（错误: 正确） |

## 6. 工具五：文档导出（url2md.py）

### 6.1 功能

将飞书/一堂文档 URL 导出为本地 Markdown 文件。复用 `YitangCopier` 的 block 解析和 `_block_to_md` 转换能力。

### 6.2 支持的 URL 格式

| 格式 | 示例 |
|------|------|
| 飞书 docx | `https://xxx.feishu.cn/docx/{doc_token}` |
| 飞书 wiki | `https://xxx.feishu.cn/wiki/{wiki_token}` |
| 一堂 fs-doc | `https://yitang.top/fs-doc/{acl}/{doc_token}` |

### 6.3 运行参数

| 参数 | 说明 |
|------|------|
| `url`（位置参数） | 飞书/一堂文档 URL |
| `-o, --output` | 输出文件路径（默认保存到 `localscript/`） |

## 7. 工具六：新课一键处理（go-newlesson.py）

### 7.1 功能

串联多个工具完成新课的完整处理流程。修改脚本顶部 CONFIG 区后直接运行。

### 7.2 处理步骤

1. 更新 `config-wiki.yaml` → 运行 `yitang_wiki.py`（写入 wiki + 生成本地 md）
2. 更新 `config-video.yaml` → 运行 `yitang_video.py`（下载视频/音频/讨论区）
3. 重命名 md 文件与视频文件名一致
4. 运行 `subtitle_from_mp3.py` 生成字幕
5. 移动 ts/mp3 到 NAS 目录

### 7.3 失败通知

任一步骤失败时，通过飞书 IM API 发送文本消息到指定群聊，包含失败步骤和回放 URL。

### 7.4 CONFIG 配置项

| 字段 | 说明 |
|------|------|
| `transcript_url` | 课程逐字稿 URL（飞书/一堂） |
| `replay_url` | 课程回放 URL |
| `target_wiki_url` | 飞书 wiki 目标写入地址 |
| `start_heading` / `end_heading` | 内容过滤起止标题（留空则全文复制） |
| `heading_number_start` | 标题自动编号起始（留空则不编号） |
| `nas_dir` | NAS 目标目录 |

## 8. 技术架构

### 8.1 文档复制数据流

```
一堂 API ──(AES加密)──→ 解密 ──→ Block 树
飞书 docx API ──────────────────→ Block 树（自动转换格式）
                                    │
                              _flatten_blocks()
                              ├─ 章节过滤（start/end heading）
                              ├─ 容器保留（callout/quote_container/grid/table）
                              ├─ 同步块展平（跳过容器，子节点提升）
                              └─ 嵌套列表收集
                                    │
                              _auto_heading_numbers()（可选）
                              └─ 按配置范围为标题添加多级编号
                                    │
                              convert_block()
                              ├─ 普通 block → 飞书写入格式
                              ├─ 容器 → 特殊标记字典（_callout/_quote_container/_grid/_table）
                              └─ _flatten_and_convert_children() 递归处理容器子节点
                                    │
                              append_to_feishu()
                              ├─ 普通 block → 批量写入（每批≤20）
                              ├─ 特殊类型 → 专用写入方法
                              └─ _write_column_content() 统一处理容器内嵌套
                                    │
                              飞书 Open API ──→ 目标文档
```

### 8.2 认证

**一堂侧：**
- Cookie 认证
- x-token-1：AES 加密的认证令牌（含 TOKEN + 时间戳）
- x-token-2：HmacSHA1 请求签名

**飞书侧：**
- OAuth 2.0 user_access_token（2 小时有效期，自动刷新）

### 8.3 文件结构

```
yitang/
├── src/
│   ├── yitang_wiki.py       # 文档复制主程序
│   ├── yitang_video.py      # 视频/音频/讨论区下载
│   ├── yitang_addon.py      # 讨论区精华提取（LLM）
│   ├── subtitle_from_mp3.py # 音频转字幕（多引擎）
│   ├── model_downloader.py  # Whisper 模型下载管理
│   ├── yitang_srt_fix.py    # 字幕校订（LLM + 词典）
│   ├── filter_changelog.py  # 校订后处理过滤器
│   ├── url2md.py            # 文档 URL → Markdown 导出
│   ├── go-newlesson.py      # 新课一键处理
│   └── auth.py              # 飞书 OAuth 授权辅助
├── cfg/
│   ├── config-wiki.yaml     # 文档复制配置（源→目标映射、章节范围）
│   ├── config-video.yaml    # 视频下载配置
│   ├── config-addon.yaml    # 讨论区精华提取配置
│   ├── config-srtfix.yaml   # 字幕校订配置
│   ├── prompt-srtfix-ref.md # 校订提示词（有逐字稿参照）
│   ├── prompt-srtfix-noref.md # 校订提示词（无逐字稿参照）
│   ├── srtfix-dict.yaml     # 字幕校订自定义词典
│   └── credentials.yaml     # 敏感凭证（不入库）
├── docs/
│   ├── PRD.md               # 本文档
│   └── 需求草稿-复制Live.txt
├── README.md                # 操作说明
├── log-err/                 # 运行日志 + 跳过记录
│   ├── yitang_wiki.log      # 文档复制日志
│   ├── subtitle.log         # 字幕转写日志
│   ├── srtfix.log           # 字幕校订日志
│   ├── url2md.log           # 文档导出日志
│   ├── model_download.log   # 模型下载日志（仅独立运行时）
│   └── skipped_*.log        # 跳过内容记录
├── ailive/                  # 视频/音频/字幕下载目录
├── localscript/             # 本地导出文件目录（md、校订后 srt）
├── temp_images/             # 图片下载临时目录
└── .progress_*.json         # 断点恢复进度文件（运行中生成，完成后自动删除）
```

### 8.4 配置文件格式

**config-wiki.yaml**（文档复制配置）：
```yaml
mappings:
  - source_url: "https://yitang.top/fs-doc/{acl}/{doc_token}"
    target_url: "https://xxx.feishu.cn/wiki/{wiki_token}"
    heading_number:              # 可选：标题自动编号
      start_heading: "章节标题"  # 精确匹配，从此标题开始
      end_heading: ""            # 留空则到末尾
  # 也支持飞书原始文档
  - source_url: "https://xxx.feishu.cn/docx/{doc_token}"
    target_url: "https://xxx.feishu.cn/wiki/{wiki_token}"

content_range:
  start_heading: "开始上课"
  end_heading: "作业与Candy"

full_copy_titles:
  - "AI落地Live"
```

**credentials.yaml**（敏感凭证，gitignore）：
```yaml
yitang:
  cookie: "..."
  token: "oe9x1xEC0Z1qmM3H"
  request_id: "..."

feishu:
  app_id: "cli_xxx"
  app_secret: "xxx"
  user_access_token: "u-xxx"
  user_refresh_token: "ur-xxx"
  user_token_expire_time: 1772914991
```

## 9. 已知限制

- 一堂 Cookie/Token 有效期未知，过期需手动从浏览器重新获取
- 飞书 Grid 最多 5 列，超过自动拆分为多个 Grid（布局可能与原文略有差异）
- 同步块（synced_block）无法通过飞书 API 创建，当前跳过容器展平子节点
- 同步块内的文件附件 token 无法通过 drive API 下载（应用缺少 drive:drive:readonly 权限）
- file block 无法创建，以"📎 文件名"文字段落替代
- 嵌入表格(sheet)、倒计时(add_ons) 无法复制，记录到跳过日志
- 图片从 CDN 下载后重新上传，不保留原始飞书 token 引用
- 飞书 refresh_token 有效期约 30 天，过期需重新 OAuth 授权

## 10. 依赖

- Python 3.10+
- requests, cryptography, pyyaml, opencc-python-reimplemented（核心）
- yt-dlp（视频下载，外部工具）
- faster-whisper, ctranslate2（Whisper 本地转写）
- nvidia-cublas-cu12（CUDA 加速，可选）
- websocket-client（讯飞转写）
- oss2（阿里云转写）
