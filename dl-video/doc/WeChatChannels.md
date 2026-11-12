# 微信视频号：独立下载与完整流水线

## 流程

Step1 使用 `src/s1w_wechatchannels.py`，复用腾讯元宝网页登录态：

`getuserinfo → get_parse_result → data.wx_export_id → findergetobjecturl → videoUrl → MP4 → M4A`

没有独立 Wiki 文稿，流水线会跳过 Step2。Step3/4/5 继续调用原程序：

- Qwen3-ASR（默认）、Whisper 和 doubao-seed-asr 均执行 `S1 → S3 → S4 → S5`。
- S4 以 `--no-transcript` 做无参照校订。
- S5 以 `--no-transcript --subtitle-only` 从字幕直接提取要点和摘要，不强制 Wiki，不读取配置中的其他文稿或讨论区。

全流程不操作微信客户端，也不配置代理、根证书或进程注入。不需要第三方解析 API Key；S4/S5 的 LLM 凭据仍由原有集中加载器提供。

## 快速开始

需要 Python 的 `requests`、`pyyaml`、`playwright`，Playwright Chromium，以及 PATH 中的 `ffmpeg` / `ffprobe`。安装操作按本机环境需要执行，不必重复安装已有依赖。

先查看计划（不会访问元宝、运行下载/模型或发送通知）：

```bash
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --print-plan
```

首次登录与运行：

```bash
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --no-wechat-headless --no-notify
```

在打开的 Chromium 中手工登录腾讯元宝。登录态保存在 `.browser_data_wxchannels`；请勿提交或公开该目录。程序检查真实账号状态，匿名 HTTP 200 不算登录成功，也不再要求 `wxIsBind` 必须为真。

已有登录态后，可无窗口运行：

```bash
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --wechat-headless --no-notify
```

单条 URL 直接启动（`--wechat-url` 是 `--live-url` 的别名，依据 sph URL 自动识别）：

```bash
python src/run_pipeline.py --wechat-url 'https://weixin.qq.com/sph/ALRGH1Htvi' --wechat-headless --no-notify
```

默认会执行字幕校订；需要改用 Whisper 时：

```bash
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --subtitle-engine whisper --wechat-headless --no-notify
```

只下载视频和音频：

```bash
python src/s1w_wechatchannels.py 'https://weixin.qq.com/sph/ALRGH1Htvi' --headless
python src/s1w_wechatchannels.py --config cfg/input.wechat.example.yaml --headless
# 或将完整流水线截止到 S1
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --to-step s1 --wechat-headless --no-notify
# 只下载并生成 ASR 字幕，不调用 S4/S5 的 LLM
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --to-step s3 --wechat-headless --no-notify
```

## 配置与启动参数

`cfg/config.yaml` 的 `wechat_channels` 提供默认配置。`--config` 选择的输入 YAML 覆盖同名顶层配置；CLI 参数优先。任务示例见 `cfg/input.wechat.example.yaml`，原 `cfg/input.yaml` 的现有任务未替换。

```yaml
path_wechat_dir: "out-wechat/"
wechat_channels:
  browser_profile: ".browser_data_wxchannels"
  headless: false
  login_timeout: 300
  request_timeout: 30
  verify_decode: true
tasks:
  - source_type: "wechat_channels"
    source_huifang_url: "https://weixin.qq.com/sph/ALRGH1Htvi"
    source_wiki_url: ""
    target_wiki_url: ""
```

| 用途 | 流水线参数 | 独立 S1 参数 |
| --- | --- | --- |
| 视频地址 | `--wechat-url URL` / `--live-url URL` | 位置参数，可传多个 URL |
| 输入配置 | `--config PATH` | `--config PATH` |
| 无窗口 / 可见浏览器 | `--wechat-headless` / `--no-wechat-headless` | `--headless` / `--no-headless` |
| 登录态目录 | `--wechat-browser-profile PATH` | `--browser-profile PATH` |
| 登录等待秒数 | `--wechat-login-timeout N` | `--login-timeout N` |
| 输出目录 | 配置 `path_wechat_dir`（默认 `out-wechat/`） | `--output-dir PATH` 或配置 `path_wechat_dir` |
| 字幕引擎 | `--subtitle-engine qwen3-asr/whisper/doubao-seed-asr` | 不适用 |
| 执行范围 | `--from-step` / `--to-step` | 只执行 S1 |
| 仅看计划 | `--print-plan` | 不适用 |
| 关闭飞书通知 | `--no-notify` | 独立 S1 不发送通知 |

相对配置路径、输出目录和浏览器 profile 以项目根目录解析。请勿并发使用同一浏览器 profile；遇到占用错误，应先结束使用该 profile 的浏览器进程，再重试。

视频号从 URL 开始的新任务统一输出到项目根目录的 `out-wechat/`，不使用其他平台的 `path_output_dir`。S3/S4/S5 产物跟随输入媒体或字幕所在目录；显式传入其他目录的已有文件续跑时，仍保存在该文件旁。旧视频号专用配置中的 `path_output_dir` 请改为 `path_wechat_dir`。

## 输出与续跑

文件按分享 ID 命名，避免不同视频标题重复而串任务：

```text
wx_ALRGH1Htvi.mp4
wx_ALRGH1Htvi.m4a
wx_ALRGH1Htvi.download.json
wx_ALRGH1Htvi.qwen3_asr.srt          # 或对应其他引擎后缀
wx_ALRGH1Htvi.qwen3_asr_fix.srt      # 默认 Qwen3-ASR 的 S4 校订结果
wx_ALRGH1Htvi.mlx_l3_fix.srt        # Whisper 校订后的示例
wx_ALRGH1Htvi_信息拓展.md
wx_ALRGH1Htvi_精华摘要.md
```

下载清单保存标题、来源分享链接、本地文件路径、时长与完成时间；不保存 Cookie、wx_export_id 或带签名的媒体 URL。只有视频和音频均校验成功才写入成功清单。流水线核对本轮清单后把确切的 M4A 交给 S3，不再扫描其他视频的最新音频。

重复执行 S1 会校验并复用已有 MP4/M4A；损坏的既有文件会报错，不自动覆盖。失败下载留下唯一名称的 `.part.mp4`，它不会被当作正式产物；当前不支持续传这些分片。默认执行 MP4 全片解码检查；`verify_decode: false` 可关闭完整解码，但仍检查文件头、下载字节数、媒体流、时长和音视频时长一致性。

从 S3 接续时保留视频号 URL / 配置，以继续采用无 Wiki 模式：

```bash
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --from-step s3 --no-notify
python src/run_pipeline.py --wechat-url 'https://weixin.qq.com/sph/ALRGH1Htvi' \
  --fix-subtitle 'out-wechat/wx_ALRGH1Htvi.qwen3_asr_fix.srt' --no-notify
```

单独处理已有字幕：

```bash
python src/s4_srt_fix.py --subtitle 'out-wechat/wx_ALRGH1Htvi.mlx_l3.srt' --no-transcript
python src/s5_addon.py --subtitle 'out-wechat/wx_ALRGH1Htvi.qwen3_asr.srt' \
  --no-transcript --subtitle-only --no-prefer-fix
```

## 失败与验证范围

- 元宝返回 `success` 但 `wx_export_id` 为空：仍判失败，不推进后续步骤。不能由此直接判断视频已删除或作者禁止解析。
- 登录过期：用可见浏览器重新登录；无窗口模式立即报错。
- 签名地址失效：重新运行 S1 重新解析。程序保留完整媒体 URL，不擅自删除查询参数，也不声称一定取得上传原片/最高画质。
- S1 失败、音频提取失败或交接清单不匹配：以非零状态退出/停止流水线。

2026-11-06 实测：7 个用户样本中的 5 个完成全片下载、完整解码校验、音频提取和本地 Qwen3-ASR；累计约 7 小时 33 分钟。`ALsTTe4Kdm`、`A2qpYmRFsy` 返回空解析，未下载或转写。该记录不保证链接或登录态长期有效，也不代表文件当前仍保存在输出目录。

## 已整理批次与自动流程的区别

此前已交付的批次按用户要求整理为 `标题.mp4`、`标题.m4a` 和单个 `标题.asr.srt`。保留的字幕已去除汉字间多余空格、处理零时长条目，并按原音轨播放时间戳校正累计偏差；原始 ASR 备份已移入废纸篓。额外的 `标题.asr.md` 只是便于阅读的转写稿，不是原作者提供的 Wiki 文档，也不是播放必需文件。

这些标题重命名、格式整理、时间轴映射与单字幕收尾操作针对该批次执行，尚未内置为上述通用 CLI 的默认行为。自动流程仍按分享 ID 和引擎后缀输出；不能把已经校正的字幕再次当作原始字幕重复映射时间轴。

文件发布时间：实测元宝解析/取流接口没有提供发布时间；原页面详情接口对该批次只返回前往微信查看的提示，未提供 `createtime`。因此未改写视频文件时间，不能用下载时间、签名过期时间或推测日期代替发布时间。

实现参考：[元宝两段解析的公开源码](https://github.com/yanlingLabs/video-extract-mcp/blob/main/src/resolve/wechat.ts)。本项目保留自己的配置、下载校验和流水线交接逻辑。

离线回归入口：`python -m unittest discover -s src -p 'test_wechat_channels.py'`。测试使用响应替身和本地生成的短 MP4，覆盖取流协议、空解析、脱敏、音频提取、重复运行、失败中断、两种字幕流程和 S5 无文稿输出；不调用真实 ASR/LLM 或飞书。
