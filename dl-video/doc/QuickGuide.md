# dl-video 快速使用指南

## 前置条件

```bash
pip install requests pyyaml cryptography playwright
playwright install chromium
```

确保 ffmpeg / ffprobe 在 PATH 中；凭据使用现有集中加载器，不新建 `cfg/credentials.yaml`。视频号使用元宝网页登录态。

## 各程序速查

### run_pipeline.py — 全流水线

```bash
python src/run_pipeline.py
```

按输入执行 S1–S5，失败中断；`--no-notify` 关闭飞书通知。Qwen3-ASR 与 Whisper 都执行 S4；视频号另外跳过 S2。
输入：`cfg/input.yaml`（tasks 列表）+ `cfg/config.yaml`（步骤参数）
输出：`output/`、`out-yitang/`（一堂）或 `out-wechat/`（视频号）；已有文件续跑时跟随输入目录。

已有原始视频时，可直接从 S3 开始，不再生成同名的持久化音频文件：

```bash
python src/run_pipeline.py --video-file out-yitang/demo.mp4
```

S3 仍会在系统临时目录中提取转写所需的 WAV，并在处理结束后清理。

### 微信视频号完整流程

视频、音频、字幕及后处理文件默认输出到项目根目录 `out-wechat/`，使用独立配置 `path_wechat_dir`。

```bash
# 查看计划
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --print-plan
# 首次运行，在 Chromium 中手工登录元宝
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --no-wechat-headless --no-notify
# 已有登录态；默认 S1 → S3 → S4 → S5
python src/run_pipeline.py --wechat-url 'https://weixin.qq.com/sph/ALRGH1Htvi' --wechat-headless --no-notify
# 使用 Whisper 执行 S3 → S4 → S5
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --subtitle-engine whisper --wechat-headless --no-notify
# 独立下载 MP4/M4A
python src/s1w_wechatchannels.py 'https://weixin.qq.com/sph/ALRGH1Htvi' --headless
# 只下载并生成 ASR 字幕
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --to-step s3 --wechat-headless --no-notify
```

独立 S1 使用元宝 `wx_export_id → findergetobjecturl` 获取媒体，不操作微信客户端。无 Wiki 的 S4/S5 使用字幕模式；输出按 `wx_<分享ID>` 命名。
完整参数、配置、登录、续跑及失败处理见 [视频号指南](WeChatChannels.md)。

---

### s1_huifang.py — 视频下载调度器

```bash
python src/s1_huifang.py
```

读取 input.yaml 中所有 task，按 `source_type` 分发到对应平台模块。支持 URL 自动识别：未指定 `source_type` 时，根据 URL 模式自动推断平台类型。
输入：`cfg/input.yaml` + 集中凭据加载器（视频号使用浏览器 profile）
输出：`output/{title}.ts` 或 `.mp4` + `.mp3`（飞书妙记额外产出 `_ori.srt`、`_ori.md`）

---

### s1w_yitang_video.py — 一堂

```bash
python src/s1w_yitang_video.py --video --audio --chat
```

由 s1_huifang.py 调度（`source_type: "yitang"`），也可单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "yitang"
    source_huifang_url: "https://air.yitang.top/live/xxx"
    source_wiki_url: "https://yitang.top/fs-doc/xxx"
    target_wiki_url: ""
    title: ""
    output_name: ""
```

凭证：`yitang.token` + `yitang.user_id`
输出：`out-yitang/{output_name}.ts` + `.mp3` + `.xlsx`（讨论区）

---

### s2w_yitang_wiki.py — 一堂文稿复制

```bash
python src/s2w_yitang_wiki.py [--dry-run]
```

由 s2_wiki.py 调度（`source_type: "yitang"`），也可单独运行。
将一堂文档 blocks 抓取、过滤、转换后写入飞书 wiki，同时本地导出 Markdown。
按文章生成独立日志（`wiki_{标题}_{时间戳}.log`）和警告错误日志（`err_{标题}_{时间戳}.log`）。

凭证：`yitang.token` + `feishu.user_access_token`
输出：`out-yitang/{title}.md` + 飞书 wiki 写入

---

### s1w_feishumiaoji.py — 飞书妙记

```bash
python src/s1w_feishumiaoji.py
```

由 s1_huifang.py 调度（`source_type: "feishu_minutes"`），也可单独运行。
自动提取创建日期作为文件名前缀（如 `260312-标题`）。

input.yaml 示例：
```yaml
tasks:
  - source_type: "feishu_minutes"
    source_huifang_url: "https://waytoagi.feishu.cn/minutes/obcnxxx"
    source_wiki_url: "https://waytoagi.feishu.cn/wiki/xxx"
    title: ""
```

凭证：`feishu.browser_cookie`（必需）+ `feishu.user_access_token`（自动刷新）
输出：`output/{date_prefix}{title}.ts` + `.mp3` + `_ori.srt` + `_ori.md`

---

### s1w_tencentmeeting.py — 腾讯会议

```bash
python src/s1w_tencentmeeting.py
```

由 s1_huifang.py 调度（`source_type: "tencent_meeting"`），也可单独运行。需要 Playwright。

input.yaml 示例：
```yaml
tasks:
  - source_type: "tencent_meeting"
    source_huifang_url: "https://meeting.tencent.com/cw/lJayL90E87"
    source_wiki_url: ""
    title: ""
```

凭证：`tencent_meeting.cookie`（浏览器 cookie，可跨会议复用）
输出：`output/{date_prefix}{title}.mp4` + `.mp3` + `_ori.srt` + `_abs.md`（纪要含层次格式 + 时间轴，优先 API 获取）

---

### s1w_zhihu.py — 知乎训练营

```bash
python src/s1w_zhihu.py
```

由 s1_huifang.py 调度（`source_type: "zhihu"`），也可单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "zhihu"
    source_huifang_url: "https://www.zhihu.com/training-video/1234567890/9876543210"
    source_wiki_url: ""
    title: ""
```

凭证：`zhihu.browser_cookie`（浏览器 cookie）
输出：`output/{date_prefix}{title}.mp4` + `.mp3`

---

### s1w_xiaoe.py — 小鹅通

```bash
python src/s1w_xiaoe.py
```

由 s1_huifang.py 调度（`source_type: "xiaoe"`），也可单独运行。需要 Playwright。

input.yaml 示例：
```yaml
tasks:
  - source_type: "xiaoe"
    source_huifang_url: "https://appxxx.h5.xet.citv.cn/v4/course/alive/l_xxx"
    source_wiki_url: ""
    title: ""
```

凭证：`xiaoe.browser_cookie`（从浏览器 Cookies 面板复制完整字符串）
输出：`output/{title}.mp4` + `.mp3`

---

### s1w_panda.py — 熊猫学院

```bash
python src/s1w_panda.py
```

由 s1_huifang.py 调度（`source_type: "panda"`），也可单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "panda"
    source_huifang_url: "https://fclive.pandacollege.cn/p/9nubG3"
    source_wiki_url: ""
    title: ""
```

凭证：`panda.token`（Bearer JWT，从浏览器 Network 面板获取）
输出：`output/{title}.ts` + `.mp3`

---

### s1w_taobao.py — 淘宝直播

```bash
python src/s1w_taobao.py
```

由 s1_huifang.py 调度（`source_type: "taobao"`），也可单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "taobao"
    source_huifang_url: "https://81025.tbkflow.cn/pcLive/980e81dbbe100..."
    source_wiki_url: ""
    title: ""
```

凭证：`taobao.bearer_token` + `taobao.access_token` + `taobao.company_id`
输出：`output/{title}.mp4` + `.mp3`

---

### s2_wiki.py — 教学文档下载

```bash
python src/s2_wiki.py
python src/s2_wiki.py <source_wiki_url> --attachments-only
```

下载 task 中 `source_wiki_url` 指定的飞书 wiki 为 markdown；yitang 类型任务分发到 s2w_yitang_wiki.py；如配置了 `target_wiki_url` 则将处理结果写回飞书。
`--attachments-only` 模式只下载附件（视频、音频、PDF 等），不下载全文。对于飞书页面中嵌入的 iframe 妙记视频，会调用 `s1w_feishumiaoji` 的下载链路获取视频文件。
输入：input.yaml 中的 `source_wiki_url`、`target_wiki_url`
输出：`output/{title}_wiki.md`（普通任务）或 `out-yitang/{title}.md`（一堂任务）；附件模式输出到 `out-yitang/{文档标题}/`

---

### s3_subtitle.py — 字幕生成

```bash
python src/s3_subtitle.py output/03_学术OpenClaw\ 第一天02.mp3 --whisper
python src/s3_subtitle.py output/demo.mp4 --whisper --backend mlx --model large-v3
python src/s3_subtitle.py output/demo.mp4 --doubao-seed-asr
```

当 s1 未产出 `_ori.srt` 时，从音频或视频生成字幕。流水线默认使用本机 Qwen3-ASR，也可选择 Whisper 或豆包 Seed-ASR 2.0；独立调用 S3 未指定引擎时默认使用 Whisper。流水线中已有 `_ori.srt` 则跳过。

```bash
python src/s3_subtitle.py output/demo.mp4 --qwen3-asr
```

Qwen3-ASR 需 Apple Silicon Mac 和 `mlx-qwen3-asr==0.4.4`；安装与模型缓存说明见 [完整文档](ReadMe.md#s3-qwen3-asr-依赖)。
支持长音频断点续转：中断后重新运行时自动从上次完成的段落继续。
转写时会输出进度提示：MLX 超过 30 分钟时自动分段并支持续转；其他情况每 10 秒刷新估算百分比和剩余时间。faster-whisper 按字幕时间或分段输出百分比。
输入：`output/{title}.m4a`、`output/{title}.mp3` 或常见视频文件如 `output/{title}.mp4`
输出：Mac 默认 `output/{title}.mlx_turbo.srt`；旧 faster-whisper medium 为 `output/{title}.wm.srt`
配置：`config.yaml` 中 `s3_engine_suffix`

常用命令：

```bash
# 首次使用先下载 Mac MLX 默认模型
python src/tools/model_downloader.py --backend mlx large-v3-turbo

# Apple Silicon Mac 默认：mlx-whisper + large-v3-turbo
python src/s3_subtitle.py output/03_学术OpenClaw\ 第一天02.mp3 --whisper

# 下载并使用 MLX 完整 large-v3，输出 .mlx_l3.srt
python src/tools/model_downloader.py --backend mlx large-v3

# MLX 完整 large-v3，输出 .mlx_l3.srt
python src/s3_subtitle.py output/03_学术OpenClaw\ 第一天02.mp3 --whisper --backend mlx --model large-v3

# 视频输入会先提取音频轨道，再输出同名前缀字幕
python src/s3_subtitle.py output/demo.mp4 --whisper --backend mlx --model large-v3

# 使用旧 faster-whisper 路径
python src/s3_subtitle.py output/03_学术OpenClaw\ 第一天02.mp3 --whisper --backend faster-whisper --model medium

# 使用 Agent Plan 豆包 Seed-ASR 2.0 直接识别视频
# 凭证读取 gkeys.yaml 的 volcengine-agentplan.api_key
python src/s3_subtitle.py output/demo.mp4 --doubao-seed-asr
```

`--doubao-seed-asr` 使用准确率优先的 Agent Plan WebSocket 单流接口，以 200ms PCM 音频包实时发送；无需 `endpoint_id`，也不会把整个媒体文件放入 Base64 请求体。流水线可用 `--s3-engine doubao-seed-asr` 选择云端识别。

关键参数：`--doubao-seed-asr`、`--backend auto|mlx|faster-whisper`、`--model large-v3-turbo|medium|large`、`--cpu`。
完整参数说明见 `doc/ReadMe.md` 的 `src/s3_subtitle.py — s3 媒体转字幕`。

---

### s3_speaker.py — 说话人标注

```bash
HF_TOKEN=hf_xxx python src/s3_speaker.py output/03_学术OpenClaw\ 第一天02.mp3 output/03_学术OpenClaw\ 第一天02.mlx_turbo.srt
```

读取已有 SRT 和音频，使用 pyannote 做多人识别，并输出带说话人标签的字幕。
输入：`output/{title}.mp3` + `output/{title}.mlx_turbo.srt`
输出：`output/{title}.mlx_turbo.speaker.srt`
标签形式：保留 pyannote 原始标签，例如 `[SPEAKER_00]`

首次使用需安装依赖，并在 Hugging Face 接受 `pyannote/speaker-diarization-community-1` 模型授权：

```bash
pip install pyannote.audio
```

完整参数说明见 `doc/ReadMe.md` 的 `src/s3_speaker.py — s3 字幕说话人标注`。

---

### s4_srt_fix.py — LLM 字幕修订

```bash
python src/s4_srt_fix.py
```

用 LLM 对比教学文档修订字幕中的 ASR 错误和专业术语。已有 `_fix.srt` 则跳过。
输入：`config.yaml` 中 `input.s4.subtitle`（优先）或任一 s3 字幕（如 `.mlx_turbo.srt`、`.wm.srt`）+ `input.s4.transcript`（可选）
输出：`{stem}_fix.srt` + `{stem}_fix_changelog.md`（输出到输入文件同目录）
配置：`config.yaml` 中 `s4_fix`（prompt、chunk_size）、`llm_plan`

---

### s5_addon.py — 补充内容提取

```bash
python src/s5_addon.py
```

LLM 对比字幕与教学文档，提取"字幕中有但文档中没有"的补充信息。已有 `_addon.md` 则跳过。
输入：`config.yaml` 中 `input.s5`（subtitle、transcript、discussion）
输出：`{prefix}_信息拓展.md` + `{prefix}_精华摘要.md`（输出到输入文件同目录）
配置：`config.yaml` 中 `s5_analysis`（prompt、chunk_size）、`llm_plan`

---

### feishu_auth.py — 飞书 OAuth 授权

```bash
python src/modules/feishu_auth.py
```

需要飞书用户授权时运行。该入口转调本机 `feishu-access` 公共模块，由公共模块完成 OAuth、刷新及集中凭据回写；不写入项目内的 `cfg/credentials.yaml`。
