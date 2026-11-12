# dl-video 更新历史

按时间倒序排列。2026-05 之后由本机 Codex 对话与当前源码补核；更早条目保留原文档记载的月份和提交号，本次没有重新查询 Git。完整正序过程及逐轮来源见 [项目演进记录](项目演进记录.md) 和 [对话核对索引](对话核对索引.md)。

历史条目描述当时状态，旧路径、凭据方案和默认值不能直接当作当前操作说明；当前行为见 [ReadMe.md](ReadMe.md)。

## 2026-11

### 2026-11-12 fix: 说话人分离读取集中 Hugging Face Token

- Qwen `--qwen3-diarize` 与独立 `s3_speaker.py` 接入 gtokens.yaml 的 huggingface.token，保留显式参数和环境变量优先级。
- Qwen 仅在需要说话人分离时临时注入 Token，成功/失败后恢复环境，不修改凭据文件或全局 shell。
- 集中认证 3 项与 Qwen 21 项离线回归通过；本机仅确认字段非空，未在线验证模型权限或运行真实说话人分离。

### 2026-11-12 build/docs: 同步公共下载依赖与迁移文档

- 同仓库根目录增加 `media-download/` 1.0.0 版本副本，包含公共源码、39 项离线测试、依赖、安装说明和许可证；本机唯一维护位置不变。
- 同步 dl-video 薄兼容层、11 项兼容回归、完整文档整理与迁移验收说明；补充新机器安装和 GitHub 路径。
- 此次更新由用户明确授权，凭据、profile、媒体、个人任务与运行证据留本机；不扩大真实下载验收额度。

### 2026-11-12 docs: 完成历史核对和当前文档整理

- 阅读本项目可访问的 14 个历史对话、260 个轮次，建立按时间排序的 84 条可追溯记录、来源索引及可机读元数据。
- 重写当前架构与快速指南，校正参数、默认引擎、M4A、缓存、跳过/失败行为、认证和外部写入边界。
- 新增完整文档索引、维护交接、核对报告和代码快照；原设计备份，历史选型/草稿明确标记。
- 本轮仅文档整理，不代表重跑历史测试或完成旧待办，也未提交推送。

### 2026-11-12 refactor: 视频号核心提炼为公共 media-download

- 公共工具维护唯一解析、下载、校验实现，旧 S1 仅映射参数和错误；流水线协议保留。
- 新增单链接 download、只读 status 与 doctor，HTTP 优先、双接口取流、有限回退、编码选择与字节限额。
- 兼容备用接口 HTTP 201，按资产加锁、无覆盖发布；明确公共 64 MiB 与旧 CLI 16 GiB 默认限额差异。
- T01 迁移验收为公共 39 + 项目 11 项离线测试，指定样本双路线全片解码且 SHA 一致；详见 [迁移交接](公共下载工具迁移交接.md)。

### 2026-11-12 docs: 同步实际流程与交付说明
- 修正视频号指南中 Qwen3-ASR 跳过 S4 的旧说明，补充仅下载与 ASR 的运行方式。
- 补充 Qwen3-ASR 的依赖、模型缓存、独立入口参数，以及独立 S3 与流水线的默认引擎区别。
- 记录已完成批次的单字幕整理、时间轴校正和发布时间不可获取的验证边界，区分批次整理与通用 CLI 行为。
- 更新集中认证入口，增加项目首页及通用任务示例；Git 忽略视频号/一堂产物、浏览器登录态、个人任务与缓存。

### 2026-11-07 fix: 视频号统一使用独立输出目录
- 新增 `path_wechat_dir`，默认 `out-wechat/`；视频号独立 S1、下载调度器和全流程使用同一目录规则，后续字幕与报告跟随输入文件保存。
- 更新示例配置和使用文档；其他平台仍沿用原输出目录，独立 S1 的 `--output-dir` 可覆盖视频号目录。

### 2026-11-06 feat: 微信视频号独立下载与全流程
- S1 改用元宝 `wx_export_id → findergetobjecturl`，提供独立 URL/配置 CLI，移除强制微信绑定检查。
- 保留完整媒体签名但不输出至日志；下载检查字节数、MP4 结构、媒体流与时长，默认完整解码；音频提取失败不交付成功清单。
- 流水线新增 `--wechat-url` 及元宝浏览器参数，自动跳过 S2，以分享 ID 和本轮下载清单精确接续音频。
- S4/S5 支持 `--no-transcript`；S5 增加无文稿提示词，视频号直接从字幕提取信息并生成摘要。
- 新增视频号示例配置、完整操作指南及离线回归测试；后续已统一恢复执行 S4。

### fix: s4/s5 Agent Plan 结构化输出与失败判定
- s4 切换到 `doubao-seed-2.1-lite`，s5 切换到 `doubao-seed-2.1-pro`
- Agent Plan 请求显式关闭深度思考，避免思维链耗尽输出额度
- s4/s5 使用 JSON Schema 约束结构化结果，同时兼容旧的顶层数组响应
- Qwen3-ASR 流程恢复执行 s4，s5 使用生成的 `.qwen3_asr_fix.srt`
- 空 `content`、输出截断和非法 JSON 不再按空结果继续，重试后仍失败则以非零状态退出
- s5 分段从 30000 字符缩小为 12000 字符，降低单次输出被截断的概率
- s5 在所有分段成功但无增量信息时仍生成明确的结果文件
- 流水线新增 `--video-file` 参数；已有原始视频时直接从 s3 开始，不再生成同名的持久化音频文件，s3 仅使用并清理临时 WAV

## 2026-10

- 流水线默认改为 Qwen3-ASR；曾跳过 S4，11 月恢复。
- Qwen 增加语言、术语与说话人参数，按识别块恢复标点并以整句分组；不保存完整识别 JSON，未完成真实说话人验收。
- 一堂附件、图片登录态与代理路线修正，目标 Wiki 限定范围；无 yt-dlp 视频回退。

## 2026-09

- Seed-ASR 从普通异步接口方案改为 Agent Plan WebSocket，PCM 分包近实时发送；不使用 endpoint_id。
- 接入本地 Qwen3-ASR 1.7B 8bit 与 ForcedAligner，统一完整缓存判断与 14 天更新检查，缺模型允许下载。
- LLM 读取 credential_source，修正相对路径，移除项目内旧凭据入口。

## 2026-08

### feat: s2_wiki 新增 --attachments-only 模式，支持飞书页面内嵌妙记视频下载
- `s2_wiki.py` 新增 `--attachments-only` 参数，只下载附件（视频、音频、PDF 等），不下载全文
- 附件模式复用 `s2w_yitang_wiki.py` 的 `_download_attachments` 方法，扩展支持 type=26（iframe 飞书妙记视频）
- `_feishu_attr_key` 映射新增 `26: ["iframe"]`，保留 iframe 数据
- 新增 `_download_iframe_video` 方法：解析 iframe URL 中的妙记 token，通过 `get_minutes_media` 获取视频 URL，调用 `download_video` 下载
- 视频下载 cookie 优先使用本机活跃的飞书登录态（`_local_feishu_cookie_header`），兜底当时的 credentials.yaml（该入口后来移除，当前为集中加载器）
- `download_video` 新增可选 `referer` 参数，支持不同飞书租户域名

### feat: s3_subtitle 支持视频文件输入
- `s3_subtitle.py` 的位置参数从音频文件扩展为媒体文件，支持 `.mp4`、`.mov`、`.mkv`、`.webm`、`.ts` 等常见视频格式
- 视频输入会先提取音频轨道并转为本机临时 16kHz WAV，再进入 Whisper 或云端 ASR 流程
- 字幕输出仍基于原始媒体文件名，例如 `demo.mp4` 输出 `demo.mlx_l3.srt`

## 2026-07

- 路径与一堂默认规则迁入 config.yaml；目标 Wiki 校验、全文/章节范围参数、表格拆分和富文本链接完善。
- 一堂附件覆盖文件块及 Sheet，图片/附件代理与 Cookie 兜底改进；本地 MD 导出支持纯本地模式。
- 新音频统一 M4A；MLX 长音频按 20 分钟/2 秒重叠分段，边写边续转。
- 批量任务与中间产物匹配修正，单批手工下载和文件改名不视为通用功能。

## 2026-06

- Panda 网页签名回退、直链/域名支持、SimpleAES 与分片重试。
- 集中凭据适配和刷新回写、模型下载镜像/HTTP 回退、ffmpeg 多路径定位。
- 统一 run_pipeline CLI：直接 URL、起止步骤、计划预览、现有文件接续、批量任务；进度输出节流。

## 2026-05

- CDA 从单课时扩展为课程目录，支持 iframe/qiqiuyun 与 HLS key 处理；目录提取验证不等于整课下载。
- 小鹅通短链/直接 m3u8 扩展，直链文件按视频 ID 命名以减少覆盖。

### feat: 新增 pyannote 说话人标注后处理
- 新增 `src/s3_speaker.py`，读取已有 SRT 和音频，输出 `.speaker.srt`
- 使用 pyannote speaker diarization，对每条字幕按最大时间重叠匹配说话人
- 保留 pyannote 原始标签，输出形式为 `[SPEAKER_00] 字幕内容`

### feat: Mac Apple Silicon 支持 MLX Whisper 后端
- `s3_subtitle.py` 新增 `--backend auto|mlx|faster-whisper`
- Apple Silicon Mac 默认使用 `mlx + large-v3-turbo`
- MLX 输出后缀改为点号形式：`.mlx_turbo.srt`、`.mlx_l3.srt`
- 保留 faster-whisper 作为非 Mac 和 fallback 路径

### feat: model_downloader 支持 MLX 模型预下载
- `src/tools/model_downloader.py` 新增 `--backend faster-whisper|mlx|all`
- 支持下载和列出 `large-v3-turbo`、`large-v3` 等 MLX 模型
- 当时 s3 的 MLX 路径不再转写时隐式下载模型；缺模型时提示具体下载命令。2026-09 统一缓存后已允许缺失时下载
- 默认使用 `HF_ENDPOINT=https://hf-mirror.com`，支持通过环境变量或 `--mirror` 改用官方源

### fix: run_pipeline 支持 MLX 字幕后缀
- 流水线从 `config.yaml` 的 `s3_engine_suffix` 读取有效字幕后缀
- s4 自动识别 `.mlx_turbo.srt`、`.mlx_l3.srt` 等 MLX 输出

### change: s3 字幕输出后缀统一为点号形式
- faster-whisper 输出从 `_wm.srt` / `_wl.srt` 改为 `.wm.srt` / `.wl.srt`
- 云端语音引擎输出从 `_xunfei.srt` 等改为 `.xunfei.srt` 等
- s4/s5/run_pipeline 保留旧下划线后缀识别，兼容已有历史文件

### improve: s3 本地转写增加进度提示
- MLX 转写每 10 秒在同一行刷新估算进度，显示估算百分比、预计剩余时间和已用时间
- MLX 完整转写成功后缓存本机实测速度，后续同模型使用历史速度平滑估算
- faster-whisper 短音频按字幕时间输出百分比
- faster-whisper 长音频分段时输出当前段号、段内心跳和整体进度

## 2026-03

### fix: ffmpeg 长音频提取死锁修复
- `extract_audio()` 将 stderr 从 `subprocess.PIPE` 改为重定向到临时文件
- 修复 165 分钟以上音频提取时 stderr 管道缓冲区溢出导致进程卡死的问题
- 临时 stderr 文件仅在失败时读取用于错误报告，成功后自动清理

### improve: 进度显示优化 — 统一间隔 + 同行更新 + 100% 保证
- 视频/音频下载进度改为 5MB 间隔更新，使用 `\r` 同行刷新
- 音频转换进度改为 1 分钟间隔更新，使用 ffmpeg `-progress pipe:1` 解析
- 所有进度显示在完成时保证输出 100%

### improve: 飞书妙记文件名添加日期前缀 + 日期去重
- 从妙记页面 SSR 数据提取 `create_time` 时间戳，转为 `YYMMDD-` 前缀
- 新增 `strip_date_from_title()` 去除标题中已有的日期信息，避免 `260313-03-13` 重复
- 支持多种日期格式：YYMMDD、YYYYMMDD、YYYY-MM-DD、M月D日 等

### improve: 文件完整性保护 — 全面临时文件机制
- ffmpeg_utils 所有函数（extract_audio、download_hls、remux_ts_to_mp4、concat_ts、mp3_to_wav、mp3_to_pcm）改为先写临时文件，成功后再 rename
- 避免中断后留下不完整的输出文件，下次运行时误判为已完成

### improve: 视频/音频下载断点续传
- 飞书妙记、腾讯会议、知乎训练营的视频下载支持 HTTP Range 续传
- 下载完成后校验文件大小，不完整时标记为续传状态
- 服务器不支持 Range 时自动从头下载

### feat: s1w 平台模块支持独立运行
- 飞书妙记、腾讯会议、知乎、小鹅通、淘宝直播模块均添加 `main()` 入口
- 独立运行时自动从 input.yaml 筛选对应 source_type 的任务

### feat: s1_huifang.py 调度器支持 URL 自动识别
- 新增 `_infer_source_type()` 函数，根据 URL 模式自动推断平台类型
- source_type 优先（显式指定时直接使用），URL 推断作为 fallback
- 每个任务独立 try/except，单任务失败不影响后续任务

### feat: Whisper 长音频转写断点续转
- 转写中断后重新运行时，解析已有 srt 文件的最后一条字幕，从断点继续
- 每段转写完成后 `flush()` 落盘，CUDA OOM 崩溃时已完成段落不丢失
- 每段转写后执行 `gc.collect()` + `torch.cuda.empty_cache()` 释放显存

### refactor: 新建 src/modules/ 公共模块包，消除跨文件重复代码
- 新建 `src/modules/` 目录（含 `__init__.py`）
- `ffmpeg_utils.py`：统一 ffmpeg 调用 — find_ffmpeg、extract_audio、download_hls、remux_ts_to_mp4、concat_ts、mp3_to_wav、mp3_to_pcm，消除 6+ 个文件中的重复 ffmpeg 代码
- `config_utils.py`：统一配置加载 — load_config（返回 config+creds 二元组）、safe_filename，消除 10+ 个文件中的重复代码
- `feishu_auth.py`、`feishu_token.py`、`feishu_minutes.py` 从 `src/` 移入 `src/modules/`
- 所有平台模块改为 `from modules.xxx import ...`

### improve: 一堂音频获取增加 ffmpeg 回退
- 服务端 audioUrl 返回 404 或无独立 MP3 时，自动从本地视频文件提取音频
- 日志明确提示"服务端无独立音频文件，从本地视频提取"

### fix: 一堂讨论区 XLSX 导出过滤 XML 非法控制字符
- `filter_non_gbk()` 重命名为 `_clean_cell_text()`，移到循环外部
- 新增 XML 控制字符过滤（`\x00`-`\x1f` 中除 `\t\n\r` 外），修复 openpyxl IllegalCharacterError

### improve: 执行顺序调整为"文本优先，大文件最后"
- `s1w_yitang_video.py`：讨论区导出提前到视频下载之前
- `s1w_feishumiaoji.py`：字幕下载和文字记录获取提前到视频下载之前
- `s1w_tencentmeeting.py`：已是视频最后，无需调整
- 其他平台（panda/taobao/zhihu/xiaoe）仅有视频+音频，无轻量操作可前置

### cleanup: 一堂 _detect_url_type() 清理
- 去掉 `"fs"` 兜底返回值（容易误解为专门类型）
- 未匹配已知类型时返回空字符串并输出 warning 日志

### improve: s2w 日志系统重构 — 按文章独立日志 + 警告收集 + 上下文定位
- 主运行日志改为按文章生成独立文件：`wiki_{标题}_{时间戳}.log`
- 新增警告及错误日志：`err_{标题}_{时间戳}.log`，统一记录跳过块和警告信息
- 警告和跳过块记录增加丰富上下文：block 位置（第N/M个）、block_id、parent_id、文本预览、所在章节
- 上下文包含前2段和后2段内容（各80字），便于在原文中定位问题
- 所有警告和跳过块同时输出到主日志和错误日志，便于对照查阅
- API 重试成功后标注"重试后正常"
- block 类型使用中文名称映射（如 type=27 显示为"图片"）

### refactor: 统一配置驱动的日志和输出目录
- `input.yaml` 中 `pathdir` 重命名为 `path_output_dir`
- `input.yaml` 新增 `path_log_dir` 字段，所有模块的日志目录从配置读取，不再硬编码 `log-err/`
- 全部 16 个源文件更新为从 `input.yaml` 读取 `path_log_dir` 和 `path_output_dir`

### improve: url2md.py 参数调整
- `-o`/`--output` 参数改为必填（`required=True`），不再提供默认输出路径

### refactor: 抽离飞书妙记公共函数，消除跨层依赖
- 新建 `feishu_minutes.py`，从 `s1w_feishumiaoji.py` 抽出 `extract_minutes_token()` 和 `get_minutes_info()`
- `s2_wiki.py` 改为从 `feishu_minutes` 导入，消除 s2→s1w 的跨层依赖
- `auth.py` 重命名为 `feishu_auth.py`，与 `feishu_token.py`、`feishu_minutes.py` 形成统一的 `feishu_*` 命名族
- 将 `feishu_token.py`、`s1w_yitang_video.py`、`s2w_yitang_wiki.py`、`tools/` 等未跟踪文件加入 git

### refactor: 提示词文件迁移至独立 prompt 目录
- `cfg/prompt-srtfix-ref.md` → `prompt/srtfix-ref.md`
- `cfg/prompt-srtfix-noref.md` → `prompt/srtfix-noref.md`
- `cfg/prompt-subtitle.md` → `prompt/addon-subtitle.md`
- `cfg/prompt-discussion.md` → `prompt/addon-discussion.md`
- `cfg/prompt-digest.md` → `prompt/addon-digest.md`
- s4/s5 代码新增 `PROMPT_DIR` 常量，与 `CFG_DIR` 分离

### refactor: model_downloader.py 移至 src/tools/
- `src/model_downloader.py` → `src/tools/model_downloader.py`
- s3_subtitle.py 的 import 路径同步更新

### feat: 新增一堂视频下载模块 s1w_yitang_video.py
- 一堂直播回放视频/音频/讨论区下载
- 支持 AI落地Live 系列自动编号命名，非系列课程用标题命名
- 讨论区导出为 XLSX（含时间轴、发言人、内容）

### feat: 新增一堂文稿复制模块 s2w_yitang_wiki.py
- 一堂文档 blocks 抓取 → 过滤 → 转换 → 写入飞书 wiki
- 支持全文复制和按标题范围裁剪两种模式
- 本地同步导出 Markdown

### refactor: 分离 yitang 依赖，统一配置结构
- 配置拆分为 config.yaml（步骤参数）+ input.yaml（任务列表与输入配置）
- s2/s3/s4/s5 不再依赖外部 yitang 项目的 sys.path，相关模块已内化
- 去掉 `setup_yitang_path()` 和 `sync_credentials_to_yitang()`
- task 字段统一：`source_url` → `source_huifang_url`，`wiki_url` → `source_wiki_url`
- 输出目录字段：`output_dir` → `path_output_dir`，新增 `path_yitang_dir`、`path_log_dir`
- LLM 配置从 `llm` 改为 `llm_plan`，支持多模型池和按步骤选择
- s4/s5 输入输出配置统一为 `input.s4`/`output.s4`/`input.s5`/`output.s5` 结构
- s5 输出目录跟随输入文件所在目录，不再硬编码

### rename: s1 平台脚本统一改名 s1w_ 前缀
- `s1_feishumiaoji.py` → `s1w_feishumiaoji.py`
- `s1_tencentmeeting.py` → `s1w_tencentmeeting.py`
- `s1_zhihu.py` → `s1w_zhihu.py`
- `s1_xiaoe.py` → `s1w_xiaoe.py`
- `s1_panda.py` → `s1w_panda.py`
- `s1_taobao.py` → `s1w_taobao.py`
- 区分调度器（s1_huifang.py、s2_wiki.py）与平台模块（s1w_、s2w_ 前缀）

### refactor: 统一一堂配置项命名，消除硬编码
- 合并 `yitang-output_prefix` 和 `yitang-full_copy_titles` 为 `s1_yitang_ailive`（含 `output_prefix` 和 `query_copystr`）
- `yitang-content_range` 更名为 `s1_yitang_wikicopy`
- task 新增 `output_name` 字段，支持手动指定输出文件名（视频和文稿共用）

### feat: 新增知乎训练营视频下载支持 s1w_zhihu.py
- 从 URL 提取 course_id 和 video_id
- 通过多个 API 端点尝试获取视频播放地址
- 从 catalog API 自动获取发布日期，用于文件名前缀
- API 失败时自动使用 Playwright 拦截 m3u8 请求
- 支持从 video_id（雪花算法）提取时间戳作为备选日期

### feat: 新增腾讯会议回放下载支持 s1w_tencentmeeting.py
- 最小化浏览器使用：sharing_id 直接从 URL 提取，浏览器仅提取 meeting_id、recording_id 和媒体 URL
- 通过 API 获取会议数据（标题、日期、时间轴、纪要）和逐字稿，避免不稳定的页面解析
- 支持 `/cw/` 和 `/crm/` 两种 URL 格式
- 音频直接从网页提取，无需 ffmpeg 转码
- 输出毫秒级时间戳字幕和会议摘要
- cookies 可跨会议复用，避免重复登录

### improve: 腾讯会议纪要和时间轴改用 API 获取
- 纪要通过 query-summary-and-note API 获取结构化数据（deepseek_summary.topic_summary），保留层次格式（总结段落 → 加粗编号标题 → 列表子项 → 会议待办）
- 时间轴通过 query-timeline API 获取（timeline_info.timeline_infos[]），start_time 为秒数
- 页面文本解析保留为 fallback：修复了"纪要"精确匹配防止误截断时间轴、去除连续重复条目、跳过"模版：主题摘要 会议总结"前缀
- 新增 `_parse_api_summary()` 函数解析 API 响应为格式化 markdown

### simplify: 精简腾讯会议 API 参数
- 实测三个 API（query-timeline、query-summary-and-note、minutes/detail）仅需 recording_id + cookies
- 去掉 meeting_id 和 auth_share_id 参数，浏览器端不再拦截提取 auth_share_id

### simplify: 进一步精简腾讯会议模块
- 去掉 meeting_id、sharing_id 在 API 调用中的所有残留引用
- 会议日期改从视频 URL 解析（`TM-YYYYMMDD` 格式），不再依赖页面文本解析
- 修复 DOM 标题提取 bug（提取到标题但未赋值给变量）
- 注释掉页面文本解析 fallback（时间轴和纪要），仅保留 API 获取
- 去掉重复的日期/标题解析代码块
- `extract_meeting_id()` 重命名为 `extract_sharing_id()`，语义更准确

## 2025-06

### 4e74404 — rename: s1_feishu.py → s1w_feishumiaoji.py
- 飞书模块重命名，文件名更准确地反映"飞书妙记"功能

### d6f7626 — refactor: 拆分 s1_huifang.py，提取飞书模块为独立脚本
- 将飞书妙记下载逻辑从 s1_huifang.py 中拆出，形成独立的 s1_feishu.py
- s1_huifang.py 简化为纯调度器，根据 source_type 分发到各平台模块
- 各平台模块统一暴露 `process_xxx(task, config, creds)` 入口函数

### 93f5e25 — feat: 新增小鹅通视频下载支持 s1w_xiaoe.py
- 使用 Playwright 无头浏览器拦截 m3u8 请求
- 支持 AES-128-CBC 加密 HLS 流的手动解密下载
- 支持无加密 HLS 的 ffmpeg 直接下载
- 自动处理小鹅通多域名 cookie 注入和登录跳转

### e227aef — feat: 实现腾讯云 SimpleAES DRM 解密，熊猫学院视频可自动下载
- 实现完整的腾讯云 SimpleAES DRM 解密流程
- RSA 加密 overlay key → getplayinfo/v4 获取 drmToken → license 获取加密 key → AES-CBC 解密得到真正的 key
- 逐片下载解密 + ffmpeg 合并，支持断点续传

### 3898b65 — feat: 新增熊猫学院回放下载框架 s1w_panda.py
- 熊猫学院 API 链路：shortLink → inviteId → course → liveRoom → videoSign
- 腾讯云 VOD 播放签名获取
- 基础下载框架搭建

### 8dde827 — fix: s2 同步 credentials 到 yitang 避免 refresh_token 冲突
- 修复 run_pipeline 中 s2 步骤与 yitang 项目共享 credentials 时的 token 冲突问题

### 9fbaec1 — feat: 飞书妙记回放下载工具 — 视频+音频+字幕全流程
- 项目初始版本
- 飞书妙记视频下载（流式 + 断点续传）
- 音频提取（ffmpeg MP3）
- VTT 字幕下载转 SRT
- 文字记录获取（Open API + Cookie API 双通道）
- 五步流水线框架（run_pipeline.py）
- 飞书群通知（失败/成功）
- 飞书 token 自动刷新
