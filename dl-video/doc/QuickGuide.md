# dl-video 快速使用指南

按 2026-11-12 代码核对。完整参数见 [ReadMe.md](ReadMe.md)，历史与文档导航见 [INDEX.md](INDEX.md)。下列命令在项目根目录运行，示例 URL 和文件名需换成实际输入。

## 准备

```bash
cp -n cfg/input.example.yaml cfg/input.yaml
python src/run_pipeline.py --config cfg/input.example.yaml --print-plan
```

需 ffmpeg/ffprobe；一堂视频另需 yt-dlp。按使用的平台/引擎准备 Python 包、Playwright Chromium 和模型，见完整参考。项目复用本机 `secrets-loader`、`feishu-access` 和 `media-download`，它们不随仓库分发。凭据由集中加载器提供，不新建 cfg/credentials.yaml。

## 全流程与续跑

```bash
# 配置中的全部任务顺序执行；先只看计划
python src/run_pipeline.py --print-plan
python src/run_pipeline.py --no-notify

# 直接给回放、源文稿和目标 Wiki
python src/run_pipeline.py --live-url '回放URL' --wiki-url '源文稿URL' --target-wiki-url '目标WikiURL' --print-plan

# 已有音视频：从 S3 开始
python src/run_pipeline.py --video-file 'output/demo.mp4' --no-notify
python src/run_pipeline.py --audio-file 'output/demo.m4a' --no-notify

# 已有字幕：从 S4 / S5 开始
python src/run_pipeline.py --subtitle 'output/demo.qwen3_asr.srt' --no-notify
python src/run_pipeline.py --fix-subtitle 'output/demo.qwen3_asr_fix.srt' --no-notify

# 选择引擎或限制步骤
python src/run_pipeline.py --audio-file 'output/demo.m4a' --subtitle-engine whisper --whisper-backend mlx --whisper-model large-v3 --to-step s3 --no-notify
python src/run_pipeline.py --from-step s2 --to-step s2 --print-plan
```

流水线默认 Qwen3-ASR，之后执行 S4、S5；独立 S3 默认 Whisper。流水线的 Whisper 默认 MLX + large-v3，独立 S3 在 Apple Silicon 默认 MLX + large-v3-turbo。`--s3-engine` 是 `--subtitle-engine` 的兼容别名。

S1/S2 按各自 URL 分别选择一堂或通用脚本。配置批量任务失败时停止后续任务；一般输出为 `output/`、一堂 `out-yitang/`、视频号 `out-wechat/`，已有文件续跑跟随文件目录。存在原始字幕/校订字幕/报告不会自动跳过对应步骤；用起止步骤或显式文件控制范围。

## 视频号

```bash
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --print-plan
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --no-wechat-headless --no-notify
python src/run_pipeline.py --wechat-url 'https://weixin.qq.com/sph/分享ID' --wechat-headless --no-notify
# 只下载和 ASR
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --to-step s3 --wechat-headless --no-notify
# 独立 S1
python src/s1w_wechatchannels.py 'https://weixin.qq.com/sph/分享ID' --headless
```

当前 S1 是公共 media-download 的适配层，复用元宝 profile，不操作微信客户端。视频号跳过 S2，S4/S5 自动无文稿模式。默认 `wx_<分享ID>.mp4/.m4a`，后处理跟随同前缀。登录、恢复及公共接口见 [视频号指南](WeChatChannels.md) 与 [迁移交接](公共下载工具迁移交接.md)。

## 只下载媒体

```bash
python src/s1_huifang.py '回放URL'
python src/s1_huifang.py 'URL1' 'URL2'
python src/s1w_yitang_video.py '一堂回放URL' --video --audio --chat
python src/s1w_cda.py 'https://edu.cda.cn/my/course/课程ID'
```

| 平台 | source_type | 认证来源 | 当前主要产物 |
|---|---|---|---|
| 一堂 | yitang | yitang.token/user_id | TS、M4A、讨论区 XLSX |
| 飞书妙记 | feishu_minutes | feishu Cookie/用户授权 | TS、M4A、原始 SRT/MD |
| 腾讯会议 | tencent_meeting | tencent_meeting.cookie | MP4、M4A、原始字幕/纪要 |
| 知乎训练营 | zhihu | zhihu.browser_cookie | MP4、M4A |
| 小鹅通 | xiaoe | xiaoe.browser_cookie；有效直链可直接处理 | MP4、M4A |
| 熊猫学院 | panda | panda.token / PANDA_TOKEN | MP4 或 TS、M4A |
| 淘宝回放 | taobao | bearer_token/access_token 等 | MP4、M4A |
| CDA | cda | cda.browser_cookie | MP4、M4A |
| 视频号 | wechat_channels | 元宝 profile | MP4、M4A、交接清单 |

除一堂、CDA、视频号等明确支持 URL 的独立入口外，其他平台优先经 s1_huifang 调度。知乎独立脚本末尾仍是内置示例，不作为常用入口。Panda DRM 直链可能缺少网页签名所需材料，不能保证任意 m3u8 都可下载。

## 文稿与附件

```bash
# 普通飞书文稿本地导出
python src/s2_wiki.py '源WikiURL' --title '文稿标题'
# 一堂只导出原文，不写目标 Wiki
python src/s2w_yitang_wiki.py --export-local-md '一堂文稿URL' --no-attachments
# 一堂全文复制，或按标题范围复制
python src/s2w_yitang_wiki.py '源文稿URL' '目标WikiURL' --full-copy
python src/s2w_yitang_wiki.py '源文稿URL' '目标WikiURL' --exclude-before-heading '头部章节' --exclude-after-heading '尾部章节'
# 只下载附件（包括内嵌妙记）
python src/s2_wiki.py '源WikiURL' --attachments-only
```

目标非空时须符合 `arqiaoknow.feishu.cn/wiki/` 规则。复制采用追加写入，重跑可能重复；`--resume` 仅按块位置续跑。附件默认到 `out-yitang/<文稿标题>/`，可用 `--no-attachments` 关闭。`--dry-run` 仍可联网读取、下载附件及导出本地 MD，不等于离线预览。

## 字幕与后处理

```bash
python src/s3_subtitle.py 'output/demo.mp4' --qwen3-asr
python src/s3_subtitle.py 'output/demo.mp4' --qwen3-asr --qwen3-context '术语 OpenAI'
python src/s3_subtitle.py 'output/demo.mp4' --qwen3-asr --qwen3-language Chinese
python src/s3_subtitle.py 'output/demo.mp4' --qwen3-asr --qwen3-diarize
python src/tools/model_downloader.py --backend mlx large-v3
python src/s3_subtitle.py 'output/demo.m4a' --whisper --backend mlx --model large-v3
python src/s3_subtitle.py 'output/demo.mp4' --doubao-seed-asr
python src/s3_speaker.py 'output/demo.m4a' 'output/demo.mlx_l3.srt'
python src/s4_srt_fix.py --subtitle 'output/demo.qwen3_asr.srt' --transcript 'output/demo.md'
python src/s5_addon.py --subtitle 'output/demo.qwen3_asr_fix.srt' --transcript 'output/demo.md' --discussion 'output/demo.xlsx'
# 没有参考文稿的独立后处理
python src/s4_srt_fix.py --subtitle 'output/demo.qwen3_asr.srt' --no-transcript
python src/s5_addon.py --subtitle 'output/demo.qwen3_asr_fix.srt' --no-transcript --subtitle-only
```

Qwen 参数目前不能由流水线透传，需先单独 S3。说话人功能依赖 pyannote 和模型授权，标注身份编号，不识别真人姓名。Token 可存入 `~/.config/secrets/gtokens.yaml` 的 `huggingface.token`，两个说话人入口会在没有显式参数/环境变量时读取，格式与优先级见操作说明。模型完整缓存按 14 天检查更新；缺失可下载，更新失败有完整旧缓存时继续。

MLX Whisper 超过 30 分钟自动按 20 分钟、2 秒重叠分段并尝试续转；Qwen 当前没有同样的项目级断点机制。Qwen 按整句生成字幕，长句可能超过 20 秒。Seed-ASR 用 Agent Plan WebSocket，读取 volcengine-agentplan.api_key，接近实时发送 PCM，无需 endpoint_id。

S4 当前默认 Seed 2.1 Lite，S5 默认 Pro。输出为 `*_fix.srt`、`*_fix_changelog.md`、`*_信息拓展.md`、`*_精华摘要.md`。S5 有文稿模式只接受本地 MD；两步均不会因已有输出而自动跳过。只解析可用 `--dry-run`，S4 只做词典替换用 `--dict-only`。输入或模型变化后用 S4 `--no-cache` 防止重用旧段缓存。

## 维护

飞书重新授权入口为 `python src/modules/feishu_auth.py`。只更新集中凭据，不写项目凭据文件。离线检查、已知不足、Git 同步方式见 [维护交接](维护交接.md)。
