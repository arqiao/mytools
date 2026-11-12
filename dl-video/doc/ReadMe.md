# dl-video 操作说明

本文按 2026-11-12 本地代码核对。快速上手见 [QuickGuide.md](QuickGuide.md)，架构与实际限制见 [DESIGN.md](DESIGN.md)，历史推进见 [项目演进记录](项目演进记录.md)，文档入口与核对范围见 [文档索引](INDEX.md)。Mac-Offline-ASR-Plan.md 是历史选型资料。

微信视频号的完整流程、参数和恢复运行说明见 [WeChatChannels.md](WeChatChannels.md)。凭据通过 `load_config()` 复用本机集中加载器，不在项目内新建凭据文件。

## 目录

1. 工作流概览
2. 环境准备
3. 配置文件
4. 程序参数完整参考
5. 平台配置说明
6. 输出文件
7. 常见问题

## 工作流概览

项目按五步流水线处理课程、回放、文稿和字幕：

| 步骤 | 程序 | 作用 |
| --- | --- | --- |
| s1 | `src/s1_huifang.py` | 下载视频、提取音频，部分平台同步获取原始字幕/讨论区 |
| s2 | `src/s2_wiki.py` | 下载或复制教学文稿 |
| s3 | `src/s3_subtitle.py` | 从音频或视频生成字幕；不自动因存在原始字幕而跳过 |
| s4 | `src/s4_srt_fix.py` | 用 LLM 和词典修订字幕 |
| s5 | `src/s5_addon.py` | 基于字幕、文稿、讨论区生成信息拓展和摘要 |

全流程运行：

```bash
python src/run_pipeline.py
```

配置入口：

- 任务列表：`cfg/input.yaml`
- 步骤参数：`cfg/config.yaml`
- 平台凭证：本机集中加载器提供的 `gkeys`、`gfeishu`、`gtokens` 配置组

## 环境准备

### 基础依赖

```bash
pip install requests pyyaml cryptography playwright openpyxl opencc-python-reimplemented
playwright install chromium
```

| 包 | 用途 | 主要模块 |
| --- | --- | --- |
| `requests` | HTTP 请求、API 调用、文件下载 | 多数模块 |
| `pyyaml` | 读取 YAML 配置 | 多数模块 |
| `cryptography` | AES/RSA 解密 | `s1w_panda.py`, `s1w_xiaoe.py` |
| `playwright` | 页面渲染、登录、m3u8 拦截 | `s1w_xiaoe.py`, `s1w_zhihu.py`, `s1w_tencentmeeting.py` |
| `openpyxl` | 讨论区 xlsx 读写 | `s1w_yitang_video.py`, `s5_addon.py` |
| `opencc-python-reimplemented` | 繁简转换 | `s3_subtitle.py` |

### ffmpeg

视频下载、音频提取、TS 合并和音频预处理均依赖 ffmpeg。

`modules/ffmpeg_utils.py` 查找顺序：

1. 存在的 `FFMPEG_PATH`
2. 系统 `PATH` 中的 `ffmpeg`
3. 当前 Python 环境 `sys.prefix/bin/ffmpeg`
4. `/opt/homebrew/bin/ffmpeg`、`/usr/local/bin/ffmpeg`
5. `D:\tools\ffmpeg\bin\ffmpeg.exe`、`C:\tools\ffmpeg\bin\ffmpeg.exe`
6. 最后尝试裸命令 `ffmpeg`。`ffprobe` 也应在 PATH 中；`pip install ffmpeg` 不能替代系统二进制。

macOS 可用 Homebrew 安装：

```bash
brew install ffmpeg
```

一堂 HLS 视频下载另需 `yt-dlp` 可执行命令；当前没有缺失时自动用 ffmpeg 下载视频的回退。基础包并不是全部可选后端的锁定依赖清单；按使用的引擎补装。项目没有统一 requirements/pyproject 锁文件。

### s3 Qwen3-ASR 依赖

流水线默认使用本机 Qwen3-ASR，需要 Apple Silicon Mac。项目当前使用 `mlx-qwen3-asr==0.4.4`：

```bash
pip install mlx-qwen3-asr==0.4.4
python src/s3_subtitle.py output/demo.mp4 --qwen3-asr
```

转写模型为 `moona3k/mlx-qwen3-asr-1.7b-8bit`，时间轴对齐模型为 `mlx-community/Qwen3-ForcedAligner-0.6B-8bit`。首次运行需取得模型；随后使用本地缓存，每 14 天检查更新，更新检查失败且已有完整缓存时继续使用缓存。输出为 `.qwen3_asr.srt`；流水线随后执行 S4，得到 `.qwen3_asr_fix.srt`。

### s3 Whisper 依赖

Apple Silicon Mac 推荐 MLX：

```bash
pip install mlx-whisper
python src/tools/model_downloader.py --backend mlx large-v3-turbo
python src/tools/model_downloader.py --backend mlx large-v3
```

脚本默认设置 `HF_ENDPOINT=https://hf-mirror.com` 以减少 Hugging Face 连接失败。需要改用官方源时，可在命令前覆盖：

```bash
HF_ENDPOINT=https://huggingface.co python src/tools/model_downloader.py --backend mlx large-v3
```

旧 `faster-whisper` 路径：

```bash
pip install faster-whisper huggingface_hub
python src/tools/model_downloader.py medium
```

多人识别/说话人分离使用 pyannote：

```bash
pip install pyannote.audio
```

pyannote 模型需要 Hugging Face token，并需要在 Hugging Face 页面接受 `pyannote/speaker-diarization-community-1` 的模型授权条款。脚本可通过 `--token`、`HF_TOKEN` 或 `HUGGINGFACE_TOKEN` 读取 token。

### s4/s5 LLM 依赖

s4/s5 通过 OpenAI 兼容接口调用 LLM。对应 provider 的 API key 由集中加载器提供，模型与接口地址在 `cfg/config.yaml` 中选择。

## 配置文件

### `cfg/input.yaml`

`input.yaml` 存放本机任务列表及可选覆盖项，不进入 Git。全局路径和一堂规则默认维护在 `config.yaml`，输入配置可以覆盖同名顶层键（浅合并，不是嵌套深合并）。首次使用可复制 `cfg/input.example.yaml`；视频号可直接通过 `--config cfg/input.wechat.example.yaml` 运行。

```yaml
path_output_dir: "output/"
path_log_dir: "log-err/"
path_yitang_dir: "out-yitang/"
path_wechat_dir: "out-wechat/"
titleShougong: ""

tasks:
  - source_type: "yitang"
    source_huifang_url: "https://air.yitang.top/live/xxx"
    source_wiki_url: "https://yitang.top/fs-doc/xxx"
    target_wiki_url: ""
    title: ""
    output_name: ""
    # 一堂文稿范围（均为可选）
    full_copy: false
    exclude_before_heading: ""
    exclude_after_heading: ""
    include_start_heading: ""
    include_end_heading: ""
```

任务字段：

| 字段 | 说明 |
| --- | --- |
| `source_type` | 平台类型：`yitang`、`feishu_minutes`、`tencent_meeting`、`panda`、`xiaoe`、`taobao`、`zhihu`、`cda`、`wechat_channels` |
| `source_huifang_url` | 视频、直播或回放页面 URL |
| `source_wiki_url` | 教学文稿 URL；s2 使用 |
| `target_wiki_url` | 目标 wiki URL；留空不指定写入目标，但一堂复制入口可能拒绝空目标，纯本地请用 --export-local-md。填写时必须包含 `arqiaoknow.feishu.cn/wiki/`，且不得包含 `yitang.top/homework/` |
| `title` | 手工标题；留空则尽量自动获取 |
| `output_name` | 手工输出文件名，不含扩展名；主要用于一堂 |
| `full_copy` | 一堂文稿：`true` 时忽略全部标题边界并全文复制 |
| `exclude_before_heading` | 一堂文稿：排除匹配标题的完整章节及其之前内容 |
| `exclude_after_heading` | 一堂文稿：排除匹配标题的完整章节及其之后内容 |
| `include_start_heading` | 一堂文稿：从匹配标题开始复制 |
| `include_end_heading` | 一堂文稿：复制匹配标题的完整章节后停止 |

### `cfg/config.yaml`

`config.yaml` 存放全局路径、一堂规则、步骤参数、输出后缀及 LLM provider 配置。当前 `current_s4=agentplan_seed21_lite`、`current_s5=agentplan_seed21_pro`，模型分别为 `doubao-seed-2.1-lite/pro`；关闭 thinking，启用结构化输出，额度分别 16384/32768，S4 每段 80 条、S5 每段 12000 字符。提示词实际从 `prompt/` 读取，`cfg/prompt-*.md` 是旧副本。

关键字段（节选）：

```yaml
s3_engine_suffix:
  qwen3_asr: ".qwen3_asr"
  doubao_seed_asr: ".seed_asr"
  whisper_large: ".wl"
  whisper_medium: ".wm"
  whisper_small: ".ws"
  whisper_tiny: ".wt"
  mlx_whisper_large_v3_turbo: ".mlx_turbo"
  mlx_whisper_large_v3: ".mlx_l3"
  mlx_whisper_medium: ".mlx_m"
  mlx_whisper_small: ".mlx_s"
  mlx_whisper_tiny: ".mlx_t"

s4_fix:
  chunk_size: 80
  prompt: "srtfix-ref.md"
  prompt_noref: "srtfix-noref.md"
  custom_dict: "srtfix-dict.yaml"
```

### 集中认证配置

`src/modules/config_utils.py` 的 `load_config()` 读取业务 YAML，并通过本机共享 `secrets_loader.load("gkeys", "gfeishu", "gtokens")` 加载凭据。共享模块安装位置为 `~/.local/share/arqiao-tools/secrets-loader/`；飞书适配另依赖 `~/.local/share/arqiao-tools/feishu-access/`。视频号下载另依赖 `~/.local/share/arqiao-tools/media-download/`；当前流水线顶层也导入该适配层。它们均是本机独立工具，未随本项目分发，新机器需先准备对应模块及自身凭据。具体迁移接口见 [公共下载工具迁移交接](公共下载工具迁移交接.md)。

配置组分工：`gkeys` 提供模型服务凭据，`gfeishu` 提供飞书应用、浏览器 Cookie 和用户授权，`gtokens` 提供其他平台账号凭据。各平台实际读取的字段以对应模块为准；不要将真实值复制到示例配置、代码或日志。

飞书授权、Token 刷新与回写统一转调 `feishu-access` 公共模块，项目保留 `auth_dl-video` 调用入口，由共享模块决定与统一用户授权的映射关系。应用凭据、浏览器 Cookie 和用户 Token 用途不同，不能互相替代。不创建或维护项目内的 `cfg/credentials.yaml`。

#### 获取 `feishu.browser_cookie`

一堂文稿复制遇到源飞书文档的图片时，会访问内部 Drive 预览接口；该接口不接受 Open API 的 `user_access_token`，需要源租户的浏览器登录 Cookie。出现图片下载 `401 Unauthorized` 时，按以下方式更新 `~/.config/secrets/gfeishu.yaml` 的 `feishu.browser_cookie`：

1. 在 Chrome 登录有权查看源文档图片的账号，并打开源飞书页面。
2. 打开开发者工具的 Network，勾选 Preserve log 后刷新页面。
3. 在过滤框搜索 `preview/`；若没有结果，点击任意图片打开预览后再搜索。
4. 选择 `internal-api-drive-stream.feishu.cn` 的请求，在 Request Headers 中复制 `Cookie` 的完整值。
5. 将该值写在 `feishu.browser_cookie:` 后，保持单行；不要提交或分享该值。

Cookie 需对应能访问源页面的身份。图片还会尝试本机 Chrome/飞书客户端登录态，并使用源域名 Referer；读取本机登录态可能依赖 Keychain 权限。附件优先级见下文。正文 Token 刷新不能替代图片 Cookie。

## 程序参数完整参考

本节列出项目中面向日常运行和维护的入口脚本。`tmp/`、测试脚本和 `__pycache__` 不在此范围内。

### `src/run_pipeline.py` — 五步流水线

用法：

```bash
python src/run_pipeline.py
python src/run_pipeline.py -s2
python src/run_pipeline.py output/demo.mp3
python src/run_pipeline.py --video-file output/demo.mp4
python src/run_pipeline.py output/demo.srt
python src/run_pipeline.py output/demo_fix.srt
```

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| 无参数 | 默认 | 读取任务从 S1 开始；Qwen3-ASR 与 Whisper 都执行 S4，视频号跳过 S2 |
| `--wechat-url URL` / `--live-url URL` | 选项 | 回放 URL；一堂走专用 S1，视频号 sph 走视频号 S1，其他走通用调度 |
| `--wiki-url URL` / `--target-wiki-url URL` | 选项 | 源文稿/目标 Wiki；S2 根据源 URL 独立路由 |
| `--full-copy` / `--no-attachments` | 开关 | S2 全文复制 / 关闭附件下载 |
| `--exclude-before-heading TEXT` / `--exclude-after-heading TEXT` | 选项 | S2 排除边界章节及其外侧内容 |
| `--include-start-heading TEXT` / `--include-end-heading TEXT` | 选项 | S2 包含起止章节 |
| `--subtitle PATH` / `--fix-subtitle PATH` | 选项 | 分别从 S4 / S5 接续 |
| `--whisper-backend BACKEND` | 选项 | `auto`、`mlx`（流水线默认）、`faster-whisper` |
| `--whisper-model MODEL` | 选项 | 流水线 Whisper 默认 `large-v3` |
| `--config PATH` | 选项 | 输入配置；视频号示例为 `cfg/input.wechat.example.yaml` |
| `--wechat-headless` / `--no-wechat-headless` | 开关 | 覆盖元宝浏览器无窗口设置 |
| `--wechat-browser-profile PATH` | 选项 | 元宝登录态目录 |
| `--wechat-login-timeout N` | 选项 | 元宝登录等待秒数 |
| `--subtitle-engine ENGINE` / `--s3-engine ENGINE` | 选项 | `qwen3-asr`（默认）、`whisper`、`doubao-seed-asr` |
| `--from-step STEP` / `--to-step STEP` | 选项 | 指定 S1–S5 中的起止步骤（小写 s1…s5） |
| `--print-plan` | 开关 | 只打印计划，不下载、不调用模型或发送通知 |
| `--no-notify` | 开关 | 不发送飞书通知 |
| `-s2` | 开关 | 从 s2 开始，输入取配置文件 |
| `<audio_path>` | 位置参数 | 旧式用法：从 s3 开始，用该音频生成字幕 |
| `--audio-file <media_path>` | 选项 | 从 s3 开始，用已有音频生成字幕；仍兼容视频路径 |
| `--video-file <media_path>` | 选项 | 从 s3 开始直接读取已有原始视频，不生成同名的持久化 M4A/MP3；Qwen/Whisper 使用临时音频，Seed-ASR 使用 PCM 流 |
| `<srt_path>` | 位置参数 | 从 s4 开始，用该 SRT 做字幕修订 |
| `<fix_srt_path>` | 位置参数 | 文件名包含 `_fix.srt` 时，从 s5 开始生成拓展和摘要 |

说明：

- 该脚本使用 argparse，完整参数可用 `python src/run_pipeline.py --help` 查看。
- 输入文件类型由扩展名和 `_fix.srt` 后缀判断；位置参数同样支持常见音频、视频扩展名。
- 配置模式顺序执行所有 tasks，失败停止后续任务；直接 URL/中间文件模式只执行一项。路径与 URL 应加引号。
- `--print-plan` 不执行步骤，但仍需可导入的本机共享模块。其他脚本的 `--dry-run` 不等于不联网（例如文稿解析）。
- Qwen 的 language/context/diarize 当前仅在独立 S3 暴露；需要时先运行 S3，再用 `--subtitle` 接续。
- 当前不会仅因存在 `_ori.srt`、`_fix.srt` 或报告而跳过 S3/S4/S5。一般任务的产物发现仍含目录扫描；已有素材接续优先使用显式文件参数。

### `src/s1w_wechatchannels.py` — 视频号独立 S1

```bash
python src/s1w_wechatchannels.py 'https://weixin.qq.com/sph/ALRGH1Htvi' --headless
python src/s1w_wechatchannels.py --config cfg/input.wechat.example.yaml --no-headless
```

支持多个 URL；不传 URL 时读取 `source_type: wechat_channels` 的任务。默认输出到 `out-wechat/`，由 `path_wechat_dir` 配置；`--output-dir` 可覆盖。另支持 `--browser-profile`、`--login-timeout`。成功生成 MP4、M4A 和不含媒体签名的下载交接清单；失败返回非零状态。详见 [视频号指南](WeChatChannels.md)。

### `src/s1_huifang.py` — s1 下载调度器

用法：

```bash
python src/s1_huifang.py
```

参数：`urls ...`（可选，可传多个）及 `-h/--help`。

```bash
python src/s1_huifang.py 'https://example.com/replay'
```

说明：

- 无 URL 时读取 `cfg/input.yaml` 的 `tasks`；有 URL 时自动推断平台并跳过任务列表。
- 按 `source_type` 分发到对应 `s1w_*` 模块。
- `source_type` 留空时会根据 URL 尝试自动识别平台。

### `src/s1w_yitang_video.py` — 一堂视频与讨论区

用法：

```bash
python src/s1w_yitang_video.py
python src/s1w_yitang_video.py --video
python src/s1w_yitang_video.py --audio
python src/s1w_yitang_video.py --chat
python src/s1w_yitang_video.py --video --audio
```

参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `-h`, `--help` | 开关 | 关闭 | 显示帮助并退出 |
| `url_yitanglive` | 位置参数 | 可省略 | 指定单条一堂回放，忽略任务列表 |
| `--video` | 开关 | 关闭 | 选择视频下载（可与另两个开关组合） |
| `--audio` | 开关 | 关闭 | 只下载音频或提取音频 |
| `--chat` | 开关 | 关闭 | 只导出讨论区 |

说明：

- 三个开关都不传时，默认全部执行：视频、音频、讨论区。
- 不传 URL 时读取 `cfg/input.yaml` 中一堂任务；传 URL 时只处理该 URL。
- 由 `s1_huifang.py` 调度时会执行完整一堂下载流程。

### `src/s1w_feishumiaoji.py` — 飞书妙记

用法：

```bash
python src/s1w_feishumiaoji.py
```

参数：无。

说明：

- 只处理 `cfg/input.yaml` 中 `source_type: feishu_minutes` 的任务。
- 输出视频、音频、原始字幕和文字记录。

### `src/s1w_tencentmeeting.py` — 腾讯会议

用法：

```bash
python src/s1w_tencentmeeting.py
```

参数：无。

说明：

- 只处理 `source_type: tencent_meeting` 的任务。
- 需要 `tencent_meeting.cookie`。

### `src/s1w_zhihu.py` — 知乎训练营

用法：

```bash
python src/s1_huifang.py
```

参数：无。

说明：

- 推荐通过 `s1_huifang.py` 调度 `source_type: zhihu` 的任务。
- 当前直接运行 `s1w_zhihu.py` 使用脚本内置示例 URL，不适合作为日常入口。

### `src/s1w_xiaoe.py` — 小鹅通

用法：

```bash
python src/s1w_xiaoe.py
```

参数：无。

说明：只处理 `source_type: xiaoe` 的任务。

### `src/s1w_panda.py` — 熊猫学院

用法：

```bash
python src/s1w_panda.py
```

参数：无。

说明：只处理 `source_type: panda` 的任务。

### `src/s1w_taobao.py` — 淘宝直播

用法：

```bash
python src/s1w_taobao.py
```

参数：无。

说明：只处理 `source_type: taobao` 的任务。

### `src/s1w_cda.py` — CDA 视频

用法：

```bash
python src/s1w_cda.py
python src/s1w_cda.py "https://example.com/video-page"
python src/s1w_cda.py URL1 URL2
```

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| 无参数 | 默认 | 处理 `cfg/input.yaml` 中 `source_type: cda` 的任务；没有任务时使用脚本内置默认 URL |
| `URL ...` | 位置参数 | 一个或多个 CDA 课时/课程目录 URL，或媒体直链；忽略任务列表 |

课程目录按已发布、未锁定的视频/回放任务顺序处理。CDA 入口使用手写 `sys.argv`，不要把 `--help` 当成安全预览（会被视为 URL）。

### `src/s2_wiki.py` — s2 文稿下载与写入

用法：

```bash
python src/s2_wiki.py
python src/s2_wiki.py <source_wiki_url> [target_wiki_url] --attachments-only
```

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `source_wiki_url` | 位置参数（可选） | 源 wiki/文稿 URL |
| `target_wiki_url` | 位置参数（可选） | 目标飞书 wiki URL |
| `--title TITLE` | 选项 | 直接参数模式下的输出标题 |
| `--no-attachments` | 开关 | 一堂文稿不下载附件 |
| `--attachments-only` | 开关 | 只下载附件（视频、音频、PDF 等），不下载全文 |

说明：

- 读取 `cfg/input.yaml` 的 `tasks`。
- 一堂任务分发到 `s2w_yitang_wiki.py`。
- 普通飞书文档通过 `source_wiki_url` 下载为 Markdown。
- 配置了 `target_wiki_url` 时，会尝试把处理结果写回飞书 wiki。
- `--attachments-only` 模式下，会解析页面中的普通附件（type=23）和 iframe 嵌入的飞书妙记视频（type=26），调用 `s1w_feishumiaoji` 的下载链路获取视频。`--attachments-only` 与 `--no-attachments` 互斥。

### `src/s2w_yitang_wiki.py` — 一堂文稿复制

用法：

```bash
python src/s2w_yitang_wiki.py
python src/s2w_yitang_wiki.py --dry-run
python src/s2w_yitang_wiki.py --resume
python src/s2w_yitang_wiki.py <source_url> <target_url> --full-copy
python src/s2w_yitang_wiki.py <source_url> <target_url> --exclude-before-heading "头部最后章节" --exclude-after-heading "尾部第一章节"
python src/s2w_yitang_wiki.py --export-local-md <source_url>
python src/s2w_yitang_wiki.py --export-local-md <source_url_1> --export-local-md <source_url_2>
```

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `--dry-run` | 开关 | 试运行，不写入目标 wiki |
| `--resume` | 开关 | 读取 `.progress_<doc_id>.json` 的块位置继续；不是内容去重 |
| `url_yitangwiki` / `url_feishuwiki` | 位置参数 | 复制模式需成对提供 |
| `--no-attachments` | 开关 | 关闭正文与 Sheet 附件下载 |
| `-h` / `--help` | 开关 | 显示帮助 |
| `--full-copy` | 开关 | 忽略标题边界并全文复制 |
| `--export-local-md URL` | 可重复 | 将原一堂文稿全文导出至 `out-yitang/{文档标题}.md`，不写入目标 wiki |
| `--exclude-before-heading` | 文本 | 排除匹配标题完整章节及之前内容 |
| `--exclude-after-heading` | 文本 | 排除匹配标题完整章节及之后内容 |
| `--include-start-heading` | 文本 | 从匹配标题开始复制 |
| `--include-end-heading` | 文本 | 复制匹配标题完整章节后停止 |

说明：

- 该脚本使用命令行参数解析；直接复制模式仍须同时提供源一堂 URL 与目标 wiki URL。
- `--export-local-md` 可一次传入多个一堂文稿 URL；该模式固定导出原文全文，不继承默认标题范围，也不需要目标 wiki URL。
- 由 `s2_wiki.py` 调度时会处理当前 task 的一堂文稿。
- 原生飞书源直接全文复制；一堂源按下列范围规则处理。
- 标题采用包含匹配；同侧同时配置 `exclude_*` 和 `include_*` 时，`exclude_*` 优先。未在任务中配置某一侧时，使用 `config.yaml` 的 `default_*` 设置。

复制会追加目标正文；重跑同一目标可能重复，不自动清空或去重。目标 URL 校验只检查字符串，不能证明文档为空。`--dry-run` 仍读取源文档、可下载附件和写本地 MD，只禁止目标正文写入。

附件默认写入 `path_attachment_dir/<文稿标题>/`（当前 `out-yitang/`），正文文件块、Sheet 内文件和妙记 iframe 均在范围内。一堂源优先媒体代理，再回退飞书应用/本机 Cookie；同一轮按附件标识去重，跨轮同名使用 `-copy1` 等后缀。单个附件失败仅记警告，不代表全文操作一定失败，须检查文章 err 日志。大表按最多 18 格拆连续行，保留表头与单元格链接。

### `src/s3_subtitle.py` — s3 媒体转字幕

用法：

```bash
python src/s3_subtitle.py output/demo.mp3 --whisper
python src/s3_subtitle.py output/demo.mp4 --whisper --backend mlx --model large-v3
python src/s3_subtitle.py output/demo.mp3 --whisper --backend mlx --model large-v3-turbo
python src/s3_subtitle.py output/demo.mp3 --whisper --backend mlx --model large-v3
python src/s3_subtitle.py output/demo.mp3 --whisper --backend faster-whisper --model medium
python src/s3_subtitle.py output/demo.mp3 --xunfei
python src/s3_subtitle.py output/demo.mp4 --doubao-seed-asr
python src/s3_subtitle.py output/demo.mp3 --all
```

完整参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `audio_file` | 位置参数 | 必填 | 输入媒体路径，支持常见音频和视频格式；音频如 `.m4a`、`.mp3`、`.wav`，视频如 `.mp4`、`.mov`、`.mkv`、`.webm`、`.ts` |
| `-h`, `--help` | 开关 | 关闭 | 显示帮助并退出 |
| `--whisper` | 开关 | 关闭 | 使用本地 Whisper；未指定任何引擎时也会默认启用 Whisper |
| `--qwen3-asr` | 开关 | 关闭 | 使用本机 MLX Qwen3-ASR 和强制对齐模型；流水线默认选择此引擎 |
| `--qwen3-language LANGUAGE` | 选项 | 自动检测 | 指定识别语言，如 `Chinese` 或 `English` |
| `--qwen3-context TEXT` | 选项 | 空 | 专业词汇提示，词语用空格分隔 |
| `--qwen3-diarize` | 开关 | 关闭 | 标注说话人；需额外安装 `mlx-qwen3-asr[diarize]==0.4.4` |
| `--xunfei` | 开关 | 关闭 | 使用讯飞语音转写 API |
| `--aliyun` | 开关 | 关闭 | 使用阿里云录音文件识别 API |
| `--doubao` | 开关 | 关闭 | 使用豆包/火山引擎语音转写 |
| `--doubao-seed-asr` | 开关 | 关闭 | 使用 Agent Plan 豆包 Seed-ASR 2.0 WebSocket 单流识别；输出 `.seed_asr.srt` |
| `--feishu` | 开关 | 关闭 | 使用飞书语音识别 API |
| `--all` | 开关 | 关闭 | 同时运行 Whisper、Qwen3-ASR、讯飞、阿里云、两种豆包、飞书 |
| `--backend auto` | 选项 | `auto` | Whisper 后端自动选择；Apple Silicon Mac 优先 `mlx` |
| `--backend mlx` | 选项 | 无 | 强制使用 `mlx-whisper` |
| `--backend faster-whisper` | 选项 | 无 | 强制使用 `faster-whisper` |
| `--model MODEL` | 选项 | Mac 为 `large-v3-turbo`，其他系统为 `medium` | Whisper 模型名：`large-v3-turbo`、`large-v3`、`large`、`medium`、`small`、`tiny` |
| `--cpu` | 开关 | 关闭 | 强制使用 `faster-whisper` CPU 模式；`auto` 不再选择 MLX |

组合规则：

- 独立调用 S3 不传引擎开关时等价于 `--whisper`；`run_pipeline.py` 则默认显式传入 `--qwen3-asr`。
- `--backend`、`--model`、`--cpu` 只影响本地 Whisper。
- `--all` 会输出多个 `.srt` 文件。
- 显式 `--backend mlx` 时，Metal 不可用会失败；`--backend auto` 才会尝试回退到 `faster-whisper/medium`。
- Whisper 接收视频文件时会先将音频轨道转换为本机临时目录中的 16kHz WAV；Seed-ASR 则通过管道实时解码，不生成整段临时音频。字幕文件始终输出到原视频所在目录，文件名前缀使用原视频文件名。
- Seed-ASR 2.0 使用 `gkeys.yaml` 中的 `volcengine-agentplan.api_key`；无需 `endpoint_id`。为兼容旧配置，缺少该项时才回退读取 `doubao-seed-asr.api_key`。
- Seed-ASR 会通过 `ffmpeg` 将音频或视频实时解码为 16kHz 单声道 PCM，并按官方建议以 200ms 分包发送到 `bigmodel_nostream`。它不会生成整段临时 WAV/M4A，也不会将整个文件 Base64 编码到一次 HTTP 请求中。

进度提示：

- MLX 短音频会每 10 秒刷新一次估算进度；超过 30 分钟的音频自动按 20 分钟分段、重叠 2 秒转写，每段完成后立即写入 SRT，并支持从已有字幕续转。
- MLX 估算进度首次使用模型默认速度；完整转写成功后会把本机实测速度写入 `cfg/whisper_speed.json`，后续同模型使用历史速度平滑估算。
- faster-whisper 短音频会按识别到的字幕时间约每 1 分钟输出一次百分比。
- faster-whisper 长音频分段处理时，会输出当前段号、段内心跳和整体音频进度。

MLX 模型示例：

```bash
# 默认高性价比模型：large-v3-turbo
python src/s3_subtitle.py "output/03_学术OpenClaw 第一天02.mp3" --whisper --backend mlx --model large-v3-turbo

# 完整 large-v3 模型：准确性更稳，速度更慢
python src/s3_subtitle.py "output/03_学术OpenClaw 第一天02.mp3" --whisper --backend mlx --model large-v3

# 直接从视频生成字幕
python src/s3_subtitle.py "output/demo.mp4" --whisper --backend mlx --model large-v3
```

对应输出：

```text
output/03_学术OpenClaw 第一天02.mlx_turbo.srt
output/03_学术OpenClaw 第一天02.mlx_l3.srt
```

常见输出：

| 引擎 | 输出示例 |
| --- | --- |
| Qwen3-ASR | `output/demo.qwen3_asr.srt` |
| Agent Plan Seed-ASR | `output/demo.seed_asr.srt` |
| MLX `large-v3-turbo` | `output/demo.mlx_turbo.srt` |
| MLX `large-v3` | `output/demo.mlx_l3.srt` |
| faster-whisper `medium` | `output/demo.wm.srt` |
| faster-whisper `large` | `output/demo.wl.srt` |
| 讯飞 | `output/demo.xunfei.srt` |
| 阿里云 | `output/demo.aliyun.srt` |
| 豆包 | `output/demo.doubao.srt` |
| 飞书 | `output/demo.feishu.srt` |

Qwen3-ASR 按识别块恢复标点，必要时近似回填；按句末标点、说话人和超过 5 秒停顿切分，不再强制 6 秒/固定字数上限，因此长句字幕可能超过 20 秒。当前不保存完整原始识别 JSON，也没有项目级分段续转。`--qwen3-diarize` 还需要模型授权与库接受的 Token（例如 `PYANNOTE_AUTH_TOKEN`），历史未完成真实说话人分离验收。

`.ts` 输入若有同名 `.mp3`，独立 S3 仍优先使用该 MP3；这是保留的兼容行为。Whisper 长音频支持按 SRT 尝试续转，不能推广到所有引擎。`--all` 会调用各已选后端，可能联网和计费；当前任一引擎有结果即可成功，不保证全部成功。Seed-ASR 使用 Agent Plan WebSocket 近实时发送 PCM，不整文件上传。

### `src/s3_speaker.py` — s3 字幕说话人标注

用法：

```bash
HF_TOKEN=hf_xxx python src/s3_speaker.py output/demo.mp3 output/demo.mlx_turbo.srt
python src/s3_speaker.py output/demo.mp3 output/demo.mlx_turbo.srt --token hf_xxx
python src/s3_speaker.py output/demo.mp3 output/demo.mlx_turbo.srt --output output/demo.speaker.srt
```

说明：

- 这是独立后处理脚本，不重新转写音频。
- 输入已有 SRT 和原始音频，使用 pyannote 做 speaker diarization。
- 对每条字幕按时间重叠最多的 pyannote speaker 分段打标签。
- 保留 pyannote 原始 speaker 标签，输出正文形式为 `[SPEAKER_00] 字幕内容`。
- 默认输出：`demo.mlx_turbo.speaker.srt`。

完整参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `audio_file` | 位置参数 | 必填 | 输入音频路径，例如 `output/demo.mp3` |
| `srt_file` | 位置参数 | 必填 | 输入 SRT 路径，例如 `output/demo.mlx_turbo.srt` |
| `-h`, `--help` | 开关 | 关闭 | 显示帮助并退出 |
| `--backend pyannote` | 选项 | `pyannote` | 多人识别后端；当前仅支持 pyannote |
| `--model MODEL` | 选项 | `pyannote/speaker-diarization-community-1` | pyannote diarization 模型 |
| `--token TOKEN` | 选项 | `None` | Hugging Face token；也可用 `HF_TOKEN` 或 `HUGGINGFACE_TOKEN` |
| `--output OUTPUT` | 选项 | `None` | 输出 SRT 路径；默认在原 SRT stem 后追加 `.speaker` |
| `--unknown LABEL` | 选项 | `SPEAKER_??` | 无匹配 speaker 时使用的标签 |

### `src/s4_srt_fix.py` — s4 字幕修订

视频号无 Wiki 时传 `--no-transcript`，忽略自动关联文稿及配置里的文稿来源。

用法：

```bash
python src/s4_srt_fix.py
python src/s4_srt_fix.py --subtitle output/demo.mlx_turbo.srt --transcript output/demo_wiki.md
python src/s4_srt_fix.py --dry-run
python src/s4_srt_fix.py --dict-only
```

完整参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `-h`, `--help` | 开关 | 关闭 | 显示帮助并退出 |
| `--dry-run` | 开关 | 关闭 | 只解析文件，不调用 LLM |
| `--dict-only` | 开关 | 关闭 | 只做词典替换，不调用 LLM |
| `--subtitle SUBTITLE` | 选项 | `""` | 覆盖配置中的字幕文件路径 |
| `--transcript TRANSCRIPT` | 选项 | `""` | 覆盖配置中的逐字稿路径或 URL |
| `--no-cache` | 开关 | 关闭 | 不使用断点缓存，强制重新调用 LLM |
| `--no-transcript` | 开关 | 关闭 | 禁用配置与自动关联文稿；与 --transcript 互斥 |

S4 要求字幕文件名具有配置中的引擎后缀或兼容旧后缀。实际文稿选择顺序为：`--no-transcript` → 同名 `_wiki.md` → 显式/配置 transcript → 无参照；已有同名文稿会优先于手工 transcript，这是当前实现限制。缓存为 `<字幕stem>_llm_cache.json`，按段号保存，完成后删除；输入、模型或提示词变更后应使用 `--no-cache`，缓存没有内容哈希校验。

### `src/s5_addon.py` — s5 信息拓展与摘要

视频号没有 Wiki 时使用 `--no-transcript --subtitle-only`，从字幕直接提取要点和摘要；流水线默认把 S4 生成的校订字幕交给 S5。

用法：

```bash
python src/s5_addon.py
python src/s5_addon.py --subtitle output/demo_fix.srt --transcript output/demo_wiki.md
python src/s5_addon.py --discussion output/demo.xlsx
python src/s5_addon.py --subtitle-only
python src/s5_addon.py --discussion-only
python src/s5_addon.py --no-digest
```

完整参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `-h`, `--help` | 开关 | 关闭 | 显示帮助并退出 |
| `--dry-run` | 开关 | 关闭 | 只解析文件，不调用 LLM |
| `--provider PROVIDER` | 选项 | `None` | 覆盖 `config.yaml` 中的 s5 LLM provider |
| `--subtitle SUBTITLE` | 选项 | `""` | 字幕文件路径 |
| `--transcript TRANSCRIPT` | 选项 | `""` | 本地 Markdown 文稿路径；S5 当前不直接读取 URL |
| `--discussion DISCUSSION` | 选项 | `""` | 讨论区 xlsx 路径 |
| `--subtitle-only` | 开关 | 关闭 | 仅分析字幕，跳过讨论区 |
| `--no-transcript` | 开关 | 关闭 | 忽略 Wiki 文稿与其配置，使用无文稿提示词分析字幕 |
| `--no-prefer-fix` | 开关 | 关闭 | 直接使用指定字幕，不自动切换到同名校订版 |
| `--discussion-only` | 开关 | 关闭 | 仅分析讨论区，跳过字幕 |
| `--no-digest` | 开关 | 关闭 | 跳过精华摘要生成 |

S5 有文稿模式需要本地 MD；无文稿模式必须有有效字幕。默认优先同名 `_fix.srt`，`--no-prefer-fix` 可关闭。空响应、截断与非法 JSON 会重试并最终失败；合法空 items 会生成明确报告。没有分段持久化缓存/自动二分；重新运行会重新调用模型并写结果。

### `src/modules/feishu_auth.py` — 飞书 OAuth 授权

用法：

```bash
python src/modules/feishu_auth.py
```

参数：无。

说明：

- 此入口转调本机 `feishu-access` 公共授权模块。
- OAuth 登录、已有 Token 刷新和集中凭据回写均由公共模块负责。
- 项目调用名为 `auth_dl-video`，不改写其他项目的授权块；不写入项目内凭据文件。

### `src/tools/model_downloader.py` — Whisper 模型下载

用法：

```bash
python src/tools/model_downloader.py --list
python src/tools/model_downloader.py medium
python src/tools/model_downloader.py --backend mlx large-v3
python src/tools/model_downloader.py --backend mlx large-v3-turbo
python src/tools/model_downloader.py --backend all --list
python src/tools/model_downloader.py --all
python src/tools/model_downloader.py large --mirror https://huggingface.co
```

完整参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `model` | 位置参数 | 可省略 | 模型大小：`tiny`、`small`、`medium`、`large`、`large-v3`、`large-v3-turbo` |
| `-h`, `--help` | 开关 | 关闭 | 显示帮助并退出 |
| `--backend faster-whisper` | 选项 | `faster-whisper` | 下载/检查 faster-whisper 模型 |
| `--backend mlx` | 选项 | 无 | 下载/检查 MLX Whisper 模型 |
| `--backend all` | 选项 | 无 | 同时处理 faster-whisper 和 MLX 模型 |
| `--list` | 开关 | 关闭 | 列出所有模型下载状态 |
| `--all` | 开关 | 关闭 | 下载所有模型 |
| `--mirror MIRROR` | 选项 | `None` | 指定 Hugging Face 镜像 URL |

说明：

- Whisper/Qwen/对齐模型共用 14 天检查机制；无记录或到期会核对远端，有变化才更新；缺失或不完整会下载，更新失败且旧缓存完整则继续旧版。MLX 已不再是“只能预下载”的旧行为。
- 仍建议在长任务前显式下载。检查记录位于 `~/.cache/dl-video/model-checks/`，Hub 缓存或旧 `~/.cache/dl-video/models/` 均可识别。
- 下载器和部分老脚本在解析参数前读取 input.yaml，因此新机器要先复制任务示例，不能假定所有 --help 都脱离配置。

### `src/model_downloader.py` — 旧路径模型下载器

用法和参数同 `src/tools/model_downloader.py`。

说明：

- 当前 `s3_subtitle.py` 使用的是 `src/tools/model_downloader.py`。
- `src/model_downloader.py` 保留为旧路径兼容，不建议新增调用使用它。

### `src/url2md.py` — 飞书文档转 Markdown

用法：

```bash
python src/url2md.py "https://xxx.feishu.cn/wiki/xxx" -o output/doc.md
```

完整参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `url` | 位置参数 | 必填 | 飞书文档 URL |
| `-h`, `--help` | 开关 | 关闭 | 显示帮助并退出 |
| `-o`, `--output OUTPUT` | 选项 | 必填 | 输出 Markdown 文件路径 |

### `src/tools/filter_changelog.py` — 过滤字幕修订日志

用法：

```bash
python src/tools/filter_changelog.py output/demo_fix_changelog.md
```

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `changelog.md` | 位置参数 | s4 生成的 changelog 文件 |

说明：输出 `{stem}_filtered.md`。

### `src/test_taobao_replay.py` — 淘宝回放调试脚本

用法：

```bash
python src/test_taobao_replay.py
python src/test_taobao_replay.py --url "https://81025.tbkflow.cn/pcLive/xxx"
python src/test_taobao_replay.py --url "https://81025.tbkflow.cn/pcLive/xxx" --download
python src/test_taobao_replay.py --download --output-name demo
```

完整参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `-h`, `--help` | 开关 | 关闭 | 显示帮助并退出 |
| `--url URL` | 选项 | `""` | 淘宝回放 URL；不传则读取 `cfg/input.yaml` |
| `--download` | 开关 | 关闭 | 下载选中的测试视频 |
| `--output-name OUTPUT_NAME` | 选项 | `""` | 手动指定输出文件名 |

说明：这是调试脚本，不属于主流水线；日常下载淘宝直播优先使用 `s1_huifang.py` 或 `s1w_taobao.py`。

## 平台配置说明

### 一堂

URL 格式：`https://air.yitang.top/live/{liveId}`

凭证：

- `yitang.token`
- `yitang.user_id`

获取方式：浏览器打开回放页面，F12 → Network，查看一堂 API 请求 headers。

输出：

- `out-yitang/{output_name}.ts`
- `out-yitang/{output_name}.m4a`
- `out-yitang/{output_name}.xlsx`
- `out-yitang/{title}.md`

### 飞书妙记

URL 格式：`https://{tenant}.feishu.cn/minutes/{token}`

凭证：

- OAuth：运行 `python src/modules/feishu_auth.py`
- Cookie：`feishu.browser_cookie`

输出：

- `output/{date_prefix}{title}.ts`
- `output/{date_prefix}{title}.m4a`
- `output/{date_prefix}{title}_ori.srt`
- `output/{date_prefix}{title}_ori.md`

### 腾讯会议

URL 格式：

- `https://meeting.tencent.com/cw/{sharing_id}`
- `https://meeting.tencent.com/crm/{sharing_id}`

凭证：`tencent_meeting.cookie`

输出：

- `output/{date_prefix}{title}.mp4`
- `output/{date_prefix}{title}.m4a`
- `output/{date_prefix}{title}_ori.srt`
- `output/{date_prefix}{title}_abs.md`

### 知乎训练营

URL 格式：`https://www.zhihu.com/training-video/{course_id}/{video_id}`

凭证：`zhihu.browser_cookie`

输出：

- `output/{date_prefix}{title}.mp4`
- `output/{date_prefix}{title}.m4a`

### 小鹅通

URL 格式：`https://{app_id}.h5.xet.citv.cn/v4/course/alive/l_{course_id}`

凭证：`xiaoe.browser_cookie`

输出：

- `output/{title}.mp4`
- `output/{title}.m4a`

### 熊猫学院

URL 格式：

- `https://fclive.pandacollege.cn/p/{shortLink}`
- `https://fclive.pandacollege.cn/playback/{shortLink}`

凭证：`panda.token`

输出：

- `output/{title}.ts` 或 `output/{title}.mp4`（取决于 DRM/直链路径）
- `output/{title}.m4a`

### 淘宝直播

URL 格式：`https://{company_id}.tbkflow.cn/pcLive/{link_code}?f={xxx}`

凭证：

- `taobao.bearer_token`
- `taobao.access_token`
- `taobao.company_id`
- `taobao.api_base`

输出：

- `output/{title}.mp4`
- `output/{title}.m4a`

### CDA

URL：支持 `edu.cda.cn/course/<id>/task/<id>/show`、`/my/course/<id>`、`/course/<id>` 和媒体直链；认证使用集中凭据的 `cda.browser_cookie`。

输出：

- `output/{title}.mp4`
- `output/{title}.m4a`

## 输出文件

常见输出：

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `{title}.ts` / `{title}.mp4` | s1 | 视频 |
| `{title}.m4a` | s1 | 当前默认音频；旧 MP3 仍可输入 |
| `{title}.xlsx` | s1 | 一堂讨论区 |
| `{title}_ori.srt` | s1 | 平台原始字幕 |
| `{title}_ori.md` | s1 | 平台文字记录 |
| `{title}_wiki.md` / `{title}.md` | s2 | 教学文稿 |
| `{title}.qwen3_asr.srt` / `{title}.seed_asr.srt` | s3 | Qwen / Seed-ASR 字幕 |
| `{title}.mlx_turbo.srt` | s3 | MLX Whisper 字幕 |
| `{title}.mlx_l3.srt` | s3 | MLX Whisper large-v3 字幕 |
| `{title}.wm.srt` | s3 | faster-whisper medium 字幕 |
| `{subtitle_stem}.speaker.srt` | s3_speaker | 带 pyannote 说话人标签的字幕 |
| `{subtitle_stem}_fix.srt` | s4 | 修订后字幕 |
| `{subtitle_stem}_fix_changelog.md` | s4 | 修订变更日志 |
| `{title}_信息拓展.md` | s5 | 信息拓展 |
| `{title}_精华摘要.md` | s5 | 精华摘要 |

日志默认写入 `log-err/`。

## 常见问题

### ffmpeg 找不到

执行：

```bash
which ffmpeg
ffmpeg -version
```

macOS 安装：

```bash
brew install ffmpeg
```

### MLX 报 `No Metal device available`

`mlx-whisper` 需要当前进程能访问 macOS Metal 设备。headless、沙箱、虚拟化或部分远程会话可能不可用。

处理方式：

- 在普通 macOS 终端中运行。
- 或使用 `--backend faster-whisper --model medium`。
- 或使用 `--cpu`。

### MLX 模型下载 SSL 失败

现象类似：

```text
ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]
An error happened while trying to locate the files on the Hub
```

原因通常是 Hugging Face 连接中断，且本地还没有对应模型缓存。脚本默认使用 `https://hf-mirror.com`，可直接重试下载命令：

```bash
python src/tools/model_downloader.py --backend mlx large-v3
```

如果镜像不可用，改用官方源：

```bash
HF_ENDPOINT=https://huggingface.co python src/tools/model_downloader.py --backend mlx large-v3
```

### 飞书 token 过期

重新运行：

```bash
python src/modules/feishu_auth.py
```

如果是跨租户妙记，重新从浏览器复制 `feishu.browser_cookie`。

### 平台 cookie/token 过期

先按平台区分：飞书用户 Token 由公共模块刷新；视频号复用元宝浏览器 profile，过期后以可见模式重新登录。其他平台如需更新浏览器 Cookie/Token，按对应模块要求更新集中凭据组，不写入项目目录。

### s4/s5 不想调用 LLM

使用：

```bash
python src/s4_srt_fix.py --dry-run
python src/s4_srt_fix.py --dict-only
python src/s5_addon.py --dry-run
```

### 查看脚本实时帮助

支持 argparse 的脚本可直接运行：

```bash
python src/s3_subtitle.py --help
python src/s4_srt_fix.py --help
python src/s5_addon.py --help
python src/tools/model_downloader.py --help
python src/url2md.py --help
```

## 批量辅助脚本与维护边界

`batch_srtfix.py` 是针对固定本机目录的批处理，支持 `--fix-only`、`--mtime-only`。默认扫描源码里的 SCAN_DIRS，可能调用 LLM，并把字幕 mtime 调整为对应媒体时间加 30 分钟。它不是通用流水线入口；其媒体匹配仍只列 MP4/TS/MP3，后缀表尚未覆盖全部新引擎，使用前必须核对范围。

回归测试入口、已知限制、同步基线见 [维护交接](维护交接.md)。
