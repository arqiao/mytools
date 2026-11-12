> 历史资料 / 草稿：保留当时方案，内容可能已被后续实现替代。2026-11-12 已核对的当前说明见 [文档索引](INDEX.md)；本文不作为现行操作规范。

# Mac 系统离线语音识别方案对比与选型

本文用于规划 `src/s3_subtitle.py` 在 Mac 系统下的本地离线语音识别方案。

当前测试机器：

- 机型：Mac mini `Mac16,10`
- 芯片：Apple M4
- CPU：10 核，4 个性能核心 + 6 个能效核心
- 内存：24 GB 统一内存
- 系统：macOS 26.4.1
- 架构：arm64 / Apple Silicon

## 结论

建议第一阶段把 `mlx-whisper` 作为 Mac Apple Silicon 默认字幕引擎，默认模型选：

```text
mlx-community/whisper-large-v3-turbo
```

原因：

- 适配 Apple Silicon，可利用 MLX 和统一内存。
- 对 M4 / 24GB 机器负载合理。
- 中文课程、直播、访谈类音频的准确性和速度比较均衡。
- 适合作为 `s3_subtitle.py` 自动流水线的默认本地离线方案。

多人识别不要放进第一阶段默认路径。需要区分说话人时，再增加可选方案：

```text
whispermlx 或 WhisperX + pyannote 本地模型
```

## 方案对比

| 方案 | 定位 | 准确性 | 多人识别 | 时间戳 | Mac Apple Silicon 适配 | 脚本集成 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `mlx-whisper` | Apple Silicon 上的 Whisper 推理 | 取决于模型，`large-v3-turbo` 较均衡 | 不自带 | 段落级，适合 SRT | 好 | 好 | 推荐默认方案 |
| `WhisperX` | Whisper + 对齐 + 说话人分离 | ASR 准确性取决于 Whisper，时间轴更准 | 支持，依赖 pyannote | 词级时间戳 | 一般，生态偏 CUDA | 好 | 适合需要词级时间戳和 diarization 的场景 |
| `whispermlx` | WhisperX 思路的 MLX/Apple Silicon 实现 | 取决于 MLX Whisper 模型 | 支持，依赖 pyannote | 词级时间戳 | 好 | 中等 | 值得作为多人识别实验路线 |
| `MacWhisper` | Mac 桌面应用 | 取决于 App 内模型 | Pro 支持 | 支持 SRT/VTT 等导出 | 好 | 中等偏弱 | 依赖 GUI App 和授权，不适合作为项目主引擎 |
| 当前 `faster-whisper` | 现有跨平台实现 | 稳定，`large-v3` 准确性高 | 不自带 | 段落级 | Mac 上主要走 CPU，速度一般 | 已集成 | 继续保留为 fallback |

## 准确性

语音识别文本准确性主要由 Whisper 模型大小和模型版本决定，而不是单纯由框架决定。

推荐优先级：

1. `large-v3-turbo`：速度和准确性均衡，适合默认批量字幕。
2. `large-v3`：准确性更稳，但速度和资源占用更高。
3. `medium`：可作为低资源或快速验证备选。
4. `small/tiny`：只适合链路测试，不建议用于正式字幕。

对当前 M4 / 24GB Mac mini，`large-v3-turbo` 是最合理的默认模型。

## 多人识别

Whisper 本身不负责识别“谁在说话”。多人识别通常需要 speaker diarization，也就是把音频按说话人分段。

已采用第二阶段第一版实现：

```bash
HF_TOKEN=hf_xxx python src/s3_speaker.py audio.mp3 audio.mlx_turbo.srt
```

该方案不重新转写音频，而是读取已有 SRT 和原始音频，使用 pyannote 做 speaker diarization，再按时间重叠把说话人标签合并回字幕。

输出：

```text
audio.mlx_turbo.speaker.srt
```

字幕正文示例：

```srt
[SPEAKER_00] 这一段字幕文本
```

后续可选增强：

- `WhisperX + pyannote`
- `mlx-whisper + pyannote` 集成到 `s3_subtitle.py`

需要注意：

- 说话人分离不是 100% 准确。
- 多人同时说话、背景噪声、远场录音会显著降低效果。
- 说话人标签通常是 `SPEAKER_00`、`SPEAKER_01` 这种匿名标签，不会自动知道真实姓名。
- pyannote 需要 Hugging Face token，并需要先接受对应模型的授权条款。

当前不默认开启多人识别；它是独立后处理步骤，避免影响已有 s3 转写流程。

## 时间戳能力

普通字幕场景只需要段落级时间戳：

```srt
00:01:12,300 --> 00:01:16,800
这一段字幕文本
```

`mlx-whisper` 和当前 `faster-whisper` 都适合生成这种 SRT。

如果需要逐词时间戳，例如给卡拉 OK、高亮逐词播放、精细剪辑对齐，则优先考虑：

- `WhisperX`
- `whispermlx`

这类方案会多一步 forced alignment，因此整体链路更复杂。

## 成本和授权

### 普通离线字幕

使用 `mlx-whisper` 本地推理不需要额外 API 费用。

需要成本：

- 首次下载模型的网络流量。
- 本机磁盘空间。
- 本机计算时间。

### Hugging Face token

Hugging Face token 通常不是付费 API key。它主要用于：

- 下载 gated model。
- 访问需要登录或接受条款的模型。

如果只使用公开 Whisper/MLX Whisper 模型，通常不需要 token。

### pyannote 授权

本地使用 pyannote 开源模型时，通常流程是：

1. 注册 Hugging Face 账号。
2. 创建 access token。
3. 到 pyannote 模型页面接受使用条款。
4. 本机下载模型并本地推理。

这种方式通常不按音频分钟收费。

如果使用 pyannoteAI 云 API，则属于商业服务，需要按其定价付费。当前项目不建议优先接入云 API。

## 不推荐把 MacWhisper 作为主引擎

MacWhisper 适合人工桌面工作流：

- 拖入音频。
- 手动选择模型。
- 导出 SRT/VTT/文本。
- Pro 功能可支持更多模型和说话人识别。

但它不适合作为 `s3_subtitle.py` 的主引擎：

- 依赖已安装的 macOS App。
- 可能依赖 Pro 授权。
- 自动化和可复现性不如 Python 库。
- 服务器或非 GUI 环境不可用。

可以把它作为人工校验工具，而不是项目核心依赖。

## 已实现的脚本改造

第一阶段目标：支持 Mac Apple Silicon 离线字幕。当前已在 `src/s3_subtitle.py` 中实现：

```bash
python src/s3_subtitle.py audio.mp3 --whisper
python src/s3_subtitle.py video.mp4 --whisper
```

在 Apple Silicon Mac 上，上面的默认命令等价于：

```bash
python src/s3_subtitle.py audio.mp3 --whisper --backend mlx --model large-v3-turbo
python src/s3_subtitle.py video.mp4 --whisper --backend mlx --model large-v3-turbo
```

视频输入会先提取音频轨道并转为本机临时 16kHz WAV，字幕输出仍使用原视频文件名前缀。

已新增参数：

```bash
--backend auto|mlx|faster-whisper
--model large-v3-turbo|large-v3|medium|large|small|tiny
```

当前行为：

- `--backend auto`：
  - Apple Silicon Mac：优先使用 `mlx`。
  - 其他系统：继续使用当前 `faster-whisper`。
- Mac 默认模型：`large-v3-turbo`，对应 `mlx-community/whisper-large-v3-turbo`。
- 非 Mac 默认模型：`medium`。
- `--cpu` 会让 `auto` 选择 `faster-whisper` 并强制 CPU。

MLX 完整 `large-v3` 可显式指定：

```bash
python src/s3_subtitle.py "output/03_学术OpenClaw 第一天02.mp3" --whisper --backend mlx --model large-v3
python src/s3_subtitle.py "output/demo.mp4" --whisper --backend mlx --model large-v3
```

输出：

```text
output/03_学术OpenClaw 第一天02.mlx_l3.srt
```

Mac 上需要安装：

```bash
pip install mlx-whisper
python src/tools/model_downloader.py --backend mlx large-v3-turbo
python src/tools/model_downloader.py --backend mlx large-v3
```

脚本默认设置 `HF_ENDPOINT=https://hf-mirror.com` 以降低 Hugging Face 连接失败概率。需要改用官方源时，可在命令前覆盖：

```bash
HF_ENDPOINT=https://huggingface.co python src/tools/model_downloader.py --backend mlx large-v3
```

注意：`mlx-whisper` 需要当前进程能访问 macOS Metal 设备。如果在 headless、沙箱、虚拟化或某些远程执行环境中运行，可能出现：

```text
No Metal device available
```

这种情况下 `--backend auto` 会回退到 `faster-whisper/medium`；如果要验证 MLX 性能，应在普通 macOS 终端会话中运行脚本。

输出后缀已在 `cfg/config.yaml` 中配置：

```yaml
mlx_whisper_large_v3_turbo: ".mlx_turbo"
mlx_whisper_large_v3: ".mlx_l3"
mlx_whisper_medium: ".mlx_m"
mlx_whisper_small: ".mlx_s"
mlx_whisper_tiny: ".mlx_t"
```

第二阶段目标：支持多人识别。

建议新增参数：

```bash
--diarize
--hf-token-env HF_TOKEN
```

推荐行为：

- 未传 `--diarize`：只做普通字幕。
- 传 `--diarize`：检查 pyannote 依赖、Hugging Face token、模型授权。
- diarization 结果单独保存，避免破坏普通 SRT 输出。

## 当前推荐落地顺序

1. 已完成：为 `s3_subtitle.py` 增加 `mlx` backend。
2. 已完成：在 Apple Silicon Mac 上让 `auto` 默认选择 `mlx + large-v3-turbo`。
3. 已完成：保留当前 `faster-whisper` 作为 fallback。
4. 已完成：安装 `mlx-whisper`。
5. 受限：当前 Codex/沙箱会话无法访问 Metal，真实 MLX 转写需在普通 macOS 终端中执行。
6. 待执行：再用 30 分钟以上长音频测试分段、内存和断点续转。
7. 后续可选：如果确实需要多人识别，再评估 `whispermlx + pyannote`。
