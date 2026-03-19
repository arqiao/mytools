# dl-video 更新历史

按时间倒序排列，基于 git log 整理。

## 2026-03

### migrate: s5_addon.py 迁移 — 消除 yitang_addon 跨项目依赖
- 将 yitang_addon.py（784行）全部功能内联到 s5_addon.py（891行）
- 去掉 `setup_yitang_path()` 和 `from yitang_addon import ...`
- 配置从 config.yaml 的 `addon` 节读取（chunk_size、提示词文件名）
- LLM 配置复用 config.yaml 的 `llm` 节
- `fetch_feishu_transcript()` 改为调用本地 `url2md.feishu_url_to_md`
- 新增 3 个提示词文件到 cfg/：prompt-subtitle.md、prompt-discussion.md、prompt-digest.md
- Pipeline 模式新增讨论区分析和精华摘要能力（原 s5 只做字幕对比）
- CLI 模式支持全部参数：--subtitle/--transcript/--discussion/--dry-run/--provider/--subtitle-only/--discussion-only/--no-digest
- main() 合并两种模式：有 --subtitle 走 CLI 模式，否则走 pipeline 模式

### migrate: s4_srt_fix.py 迁移 — 消除 yitang_srt_fix 跨项目依赖
- 将 yitang_srt_fix.py 全部功能内联到 s4_srt_fix.py
- 去掉 `setup_yitang_path()` 和 `from yitang_srt_fix import ...`
- 配置从 config.yaml 的 `srt_fix` 节读取（prompt、chunk_size、custom_dict）
- 新增 2 个提示词文件到 cfg/：prompt-srtfix-ref.md、prompt-srtfix-noref.md
- 新增自定义词典 cfg/srtfix-dict.yaml
- 内联 LLMClient、parse_srt、parse_llm_json、extract_terms_from_transcript 等函数

### rewrite: s3_subtitle.py 重写 — 多引擎字幕生成，消除 yitang 依赖
- 从 yitang 的 subtitle_from_mp3.py 迁移 Whisper 转写逻辑
- 新增 4 个云端转写引擎：讯飞、飞书、阿里云、豆包（火山引擎）
- 支持 CLI 模式（指定音频文件 + 引擎参数）和 pipeline 模式（扫描 output 目录）
- 引擎后缀映射通过 config.yaml 的 `engine_suffix` 节配置
- 新增 model_downloader.py 管理 Whisper 模型下载
- 长音频（>30分钟）自动分段处理，减少内存占用
- 音频预处理：大文件先转 16kHz WAV（优先 PyAV，fallback ffmpeg）
- 繁体自动转简体（opencc）

### feat: 新增 url2md.py — 飞书文档 URL 转 Markdown 下载工具
- 支持飞书 wiki URL 和 docx URL
- 支持一堂（yitang.top）文档 URL
- 通过 yitang_wiki.YitangCopier 获取文档 blocks 并转为 markdown
- CLI 模式：`python src/url2md.py <url> [-o output_path]`

### feat: 新增知乎训练营视频下载支持 s1_zhihu.py
- 从 URL 提取 course_id 和 video_id
- 通过多个 API 端点尝试获取视频播放地址
- 从 catalog API 自动获取发布日期，用于文件名前缀
- API 失败时自动使用 Playwright 拦截 m3u8 请求
- 支持从 video_id（雪花算法）提取时间戳作为备选日期

### feat: 新增腾讯会议回放下载支持 s1_tencentmeeting.py
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

### 4e74404 — rename: s1_feishu.py → s1_feishumiaoji.py
- 飞书模块重命名，文件名更准确地反映"飞书妙记"功能

### d6f7626 — refactor: 拆分 s1_huifang.py，提取飞书模块为独立脚本
- 将飞书妙记下载逻辑从 s1_huifang.py 中拆出，形成独立的 s1_feishu.py
- s1_huifang.py 简化为纯调度器，根据 source_type 分发到各平台模块
- 各平台模块统一暴露 `process_xxx(task, config, creds)` 入口函数

### 93f5e25 — feat: 新增小鹅通视频下载支持 s1_xiaoe.py
- 使用 Playwright 无头浏览器拦截 m3u8 请求
- 支持 AES-128-CBC 加密 HLS 流的手动解密下载
- 支持无加密 HLS 的 ffmpeg 直接下载
- 自动处理小鹅通多域名 cookie 注入和登录跳转

### e227aef — feat: 实现腾讯云 SimpleAES DRM 解密，熊猫学院视频可自动下载
- 实现完整的腾讯云 SimpleAES DRM 解密流程
- RSA 加密 overlay key → getplayinfo/v4 获取 drmToken → license 获取加密 key → AES-CBC 解密得到真正的 key
- 逐片下载解密 + ffmpeg 合并，支持断点续传

### 3898b65 — feat: 新增熊猫学院回放下载框架 s1_panda.py
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
