# dl-video 更新历史

按时间倒序排列，基于 git log 整理。

## 2026-03

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
