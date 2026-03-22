# dl-video 需求及设计描述

## 项目背景

多平台在线课程回放的自动下载与后处理工具。

**解决的问题**：
- 各平台回放视频无统一下载方式，需手动录屏或使用不同工具
- 部分平台采用 DRM 加密（腾讯云 SimpleAES、HLS AES-128），无法直接下载
- 缺少字幕或字幕质量差，需人工整理
- 视频内容与教学文档不同步，补充信息散落各处

**支持平台**（7个）：
1. **一堂**：AES 加密 API 认证，支持视频/音频/讨论区下载 + 文稿复制
2. **飞书妙记**：双通道认证（Open API + Cookie），支持跨租户
3. **腾讯会议**：最小化浏览器使用，API获取会议数据（纪要、时间轴、逐字稿）
4. **知乎训练营**：多 API 端点尝试，雪花算法 ID 时间戳提取
5. **小鹅通**：HLS AES-128 解密，多域名 cookie 注入
6. **熊猫学院**：腾讯云 SimpleAES DRM 解密，RSA + AES-CBC 多层加密
7. **淘宝直播**：螳螂直播 API，双 token 认证

**核心功能**：
- 五步流水线：视频下载 → 教学文档 → Whisper 字幕 → LLM 修订 → 补充内容提取
- 统一配置管理：config.yaml（步骤参数）+ input.yaml（任务列表与输入配置）+ credentials.yaml（敏感凭证）
- 自动化处理：飞书 token 自动刷新、失败通知、断点续传

## 架构设计

### 整体架构

```
调度器 (s1_huifang.py)
  ├── 一堂模块           (s1w_yitang_video.py)
  ├── 飞书妙记模块       (s1w_feishumiaoji.py)
  ├── 腾讯会议模块       (s1w_tencentmeeting.py)
  ├── 知乎训练营模块     (s1w_zhihu.py)
  ├── 小鹅通模块         (s1w_xiaoe.py)
  ├── 熊猫学院模块       (s1w_panda.py)
  └── 淘宝直播模块       (s1w_taobao.py)

调度器 (s2_wiki.py)
  └── 一堂文稿模块       (s2w_yitang_wiki.py)

五步流水线 (run_pipeline.py)
  s1(视频下载) → s2(教学文档) → s3(Whisper字幕) → s4(字幕修订) → s5(生成Addon)
```

### 目录结构

```
dl-video/
├── cfg/
│   ├── config.yaml          # 步骤参数（LLM、输入输出路径、引擎配置）
│   ├── input.yaml           # 任务列表与输入配置（tasks、输出目录、一堂专用配置）
│   └── credentials.yaml     # 敏感凭证（token、cookie、secret）
├── src/
│   ├── run_pipeline.py      # 五步流水线调度
│   ├── s1_huifang.py        # Step1 调度器：按 source_type 分发
│   ├── s1w_yitang_video.py  # 一堂视频下载模块
│   ├── s1w_feishumiaoji.py  # 飞书妙记下载模块
│   ├── s1w_tencentmeeting.py # 腾讯会议下载模块
│   ├── s1w_zhihu.py         # 知乎训练营下载模块
│   ├── s1w_xiaoe.py         # 小鹅通下载模块
│   ├── s1w_panda.py         # 熊猫学院下载模块
│   ├── s1w_taobao.py        # 淘宝直播下载模块
│   ├── s2_wiki.py           # Step2 调度器：下载教学文档 + 写入飞书 wiki
│   ├── s2w_yitang_wiki.py   # 一堂文稿复制模块
│   ├── s3_subtitle.py       # Step3: MP3 → 字幕（Whisper fallback）
│   ├── s4_srt_fix.py        # Step4: LLM 对比文档修订字幕
│   ├── s5_addon.py          # Step5: 提取字幕中的补充内容
│   ├── url2md.py            # 飞书 wiki 转 markdown 工具
│   ├── modules/             # 公共模块
│   │   ├── __init__.py
│   │   ├── ffmpeg_utils.py  # ffmpeg 工具（音频提取、HLS 下载、TS 合并、音频转换）
│   │   ├── config_utils.py  # 配置加载（load_config、safe_filename）
│   │   ├── feishu_auth.py   # 飞书 OAuth 授权工具
│   │   ├── feishu_token.py  # 飞书 token 统一管理（刷新、请求头、wiki 解析）
│   │   └── feishu_minutes.py # 飞书妙记公共工具（URL 解析、妙记信息查询）
│   └── tools/               # 辅助工具脚本
│       ├── model_downloader.py  # Whisper 模型下载工具
│       └── filter_changelog.py
├── prompt/                  # 提示词模板
│   ├── srtfix-ref.md        # 字幕修订（有参照文档）
│   ├── srtfix-noref.md      # 字幕修订（无参照文档）
│   ├── addon-subtitle.md    # 补充内容提取（字幕）
│   ├── addon-discussion.md  # 补充内容提取（讨论区）
│   └── addon-digest.md      # 精华摘要
├── output/                  # 默认输出目录（视频、音频、字幕、文档）
├── out-yitang/              # 一堂任务专用输出目录
├── log-err/                 # 日志统一目录（可通过 input.yaml 的 path_log_dir 配置）
└── doc/                     # 项目文档
```

## 平台下载模块设计

### 通用设计原则

**回放类URL的解析下载原则**："文本数据优先，大文件最后"

所有平台模块遵循统一的下载顺序策略：
1. **文本数据优先**：先获取标题、日期、纪要、时间轴、逐字稿等轻量级文本数据
2. **大文件最后**：视频和音频下载放在最后两步
3. **快速失败**：文本数据获取失败时立即中断，避免浪费时间下载大文件后才发现问题

优势：
- 减少无效下载：API 失败或认证过期时，在下载大文件前就能发现
- 提升用户体验：文本数据秒级返回，用户可快速确认任务是否正常
- 节省带宽：避免下载几百MB视频后才发现标题或字幕获取失败

### Step1 调度器 (s1_huifang.py)

读取 `input.yaml` 中的 `tasks` 列表，根据每个任务的 `source_type` 字段分发到对应平台模块。支持的 source_type：`yitang`、`feishu_minutes`、`tencent_meeting`、`zhihu`、`xiaoe`、`panda`、`taobao`。

### 一堂 (s1w_yitang_video.py)

**认证方式**：API token + user_id（从浏览器 Network 面板获取）

**流程**：
1. `fetch_replay_data(live_id)` — `GET /api/air/room/replay` 获取回放数据（视频URL、音频URL、讨论区）
2. `fetch_live_title(source_url, live_id)` — 获取标题：
   - 优先 `GET /api/air/room/info`（用 sid 参数）
   - 其次 `GET /api/lesson/detail`（URL 带 lessonId 时）
3. `extract_number_from_title(title)` — 从标题提取编号（如 "AI落地Live第69场" → "069"）
4. `export_chats(chats, output_path, duration)` — 讨论区导出为 XLSX（轻量操作优先）
5. `download_video(video_url, output_path)` — yt-dlp 下载 m3u8 视频（自带断点续传）
6. `_download_m3u8(m3u8_url, output_path)` — ffmpeg fallback 下载（yt-dlp 失败时）
7. 音频获取（优先服务端下载，失败则从本地视频提取）：
   - `download_audio(audio_url, output_path)` — 流式下载音频 MP3
   - 若服务端 404 或无 audioUrl，调用 `extract_audio()` 从视频文件提取

**API 签名机制**：
- 所有请求需携带 `x-token-1`（AES 加密的时间戳）和 `x-token-2`（HMAC-SHA256 签名）
- 签名基于 URI + 参数 + 固定密钥，与 `s2w_yitang_wiki.py` 共用相同的加密常量

**各要素获取方式详解**：

| 要素 | 获取方式 | 说明 | 依赖 |
|------|----------|------|------|
| **liveId** | 直接从URL提取（正则） | URL路径 `/live/{liveId}` | 无 |
| **标题** | API获取（room/info 或 lesson/detail） | 优先 room/info，其次 lesson/detail | liveId + token |
| **视频URL** | API获取（room/replay） | 回放数据中的 `replay.url`（m3u8） | liveId + token |
| **音频URL** | API获取（room/replay） | 回放数据中的 `replay.audioUrl` | liveId + token |
| **讨论区** | API获取（room/replay） | 回放数据中的 `chats[]` 列表 | liveId + token |
| **输出文件名** | 配置或标题推导 | 优先 output_name → 系列编号 → 标题 | 标题 |

**输出文件**：
- `{output_name}.ts` — 视频文件
- `{output_name}.mp3` — 音频文件
- `{output_name}.xlsx` — 讨论区（含时间轴、发言人、标签、内容）

**踩坑记录**：
- API 签名使用 AES-CBC 加密时间戳 + HMAC-SHA256，密钥硬编码在客户端 JS 中
- AI落地Live 系列课程自动使用 `output_prefix` + 编号命名，非系列课程用标题命名
- 讨论区导出为 XLSX 时需过滤 GBK 无法编码的 emoji（Unicode 码点 >= 0x10000）及 XML 非法控制字符（`\x00`-`\x1f` 中除 `\t\n\r` 外）
- yt-dlp 下载失败时自动 fallback 到 ffmpeg 直接下载 m3u8
- icon 标签映射优先从 API 动态加载，补充静态映射中没有的条目

### 飞书妙记 (s1w_feishumiaoji.py)

**认证方式**：双通道认证
- Open API：user_access_token（OIDC refresh_token 自动刷新，`ensure_feishu_token()` 在过期前 300s 触发）
- Cookie：browser_cookie（用于跨租户场景，Open API 无权限时 fallback）

**流程**：
1. `extract_minutes_token(url)` — 正则提取 URL 中的 minutes token（`/minutes/([A-Za-z0-9]+)`）（定义在 `feishu_minutes.py`）
2. `get_minutes_media(token, cookie)` — 用 cookie 请求妙记页面 HTML，解析 SSR 注入的 JSON 数据：
   - `video_url` — 视频流地址（飞书内部 CDN）
   - `web_vtt_url` — WebVTT 字幕地址
   - `topic` — 标题（fallback 到 `<title>` 标签）
   - 关键：页面 HTML 中含 `\uXXXX` 转义，需先 `decode_unicode_escapes()` 再正则提取
3. `download_video(video_url, output_path, cookie)` — 流式下载视频，支持 HTTP Range 断点续传
4. `extract_audio_from_video()` — ffmpeg 提取 MP3（`-vn -acodec libmp3lame -q:a 2`）
5. `download_vtt_subtitle()` → `vtt_to_srt()` — 下载 WebVTT 并转换为 SRT 格式
6. `get_transcript()` — Open API `/minutes/v1/minutes/{token}/transcript` 获取文字记录
7. `get_transcript_by_cookie()` — Cookie API `/minutes/api/subtitles` fallback（跨租户场景）

**执行顺序**：字幕下载 + 文字记录获取（轻量操作优先）→ 视频下载 → 音频提取（大文件最后）

**各要素获取方式详解**：

| 要素 | 获取方式 | 说明 | 依赖 |
|------|----------|------|------|
| **minutes_token** | 直接从URL提取（正则） | URL中明文存在，无需额外请求 | 无 |
| **标题** | 页面HTML解析（SSR JSON） | 嵌在页面SSR数据中，需解码Unicode转义 | minutes_token + cookie |
| **视频URL** | 页面HTML解析（SSR JSON） | 飞书内部CDN地址，嵌在SSR数据中 | minutes_token + cookie |
| **字幕URL** | 页面HTML解析（SSR JSON） | WebVTT格式字幕地址，嵌在SSR数据中 | minutes_token + cookie |
| **文字记录** | Open API或Cookie API | Open API需user_access_token，跨租户时用Cookie API | minutes_token + (user_access_token 或 cookie) |

**输出文件**：
- `{title}.ts` — 视频文件
- `{title}.mp3` — 音频文件
- `{title}_ori.srt` — 原始字幕（VTT 转 SRT，或 transcript 生成）
- `{title}_ori.md` — 文字记录（带说话人标注）

**踩坑记录**：
- 飞书妙记页面的视频/字幕 URL 嵌在 SSR 渲染的 JSON 中，含 `\uXXXX` Unicode 转义，直接正则匹配会失败，必须先解码
- 跨租户妙记（如 waytoagi.feishu.cn）Open API 无权限，需用 browser_cookie 走 Cookie API
- Cookie API 的 transcript 数据结构与 Open API 不同（`sentences[].contents[]` vs 直接 `text`），需归一化处理
- 视频下载服务器不一定支持 Range，`download_video()` 检测 status_code==200（非 206）时从头下载


### 腾讯会议 (s1w_tencentmeeting.py)

**认证方式**：browser_cookie（通过 Playwright 注入）

**信息获取架构**（最小化浏览器使用）：
1. **直接从 URL 提取**：sharing_id（短代码，如 `lJayL90E87`）
2. **浏览器提取**：meeting_id（19位，8开头）、recording_id（19位，2开头）、视频URL（含签名token）、cookies
3. **API 获取**：会议数据（标题、日期、时间轴、纪要）、逐字稿

**关键 ID 说明**：
- `sharing_id`：URL 中的短代码（支持 `/cw/` 和 `/crm/` 两种格式，后者会重定向到前者），仅用于构造浏览器访问 URL
- `recording_id`：录制唯一标识（19位数字，2开头），从页面HTML提取，是三个 API 的核心参数

**各要素获取方式详解**：

| 要素 | 获取方式 | 说明 | 依赖 |
|------|----------|------|------|
| **sharing_id** | 直接从URL提取（正则） | 仅用于构造浏览器访问 URL | 无 |
| **recording_id** | 浏览器提取（HTML中19位数字，2开头） | 三个 API 的核心参数 | 无 |
| **视频URL** | 浏览器提取（`<video>` 标签src） | 包含动态签名token和日期信息 | 无 |
| **cookies** | 浏览器获取 | 用于后续API认证，可跨会议复用 | 无 |
| **会议标题** | 浏览器 DOM 元素或页面文本 | SPA 页面，原始 HTML 无标题，需 JS 渲染 | 无 |
| **会议日期** | 从视频URL解析（`TM-YYYYMMDD`） | 视频 URL 中固定包含日期时间 | 视频URL |
| **时间轴** | query-timeline API | `data.timeline_info.timeline_infos[]`，start_time为秒数 | recording_id + cookies |
| **会议纪要** | query-summary-and-note API | `data.deepseek_summary.topic_summary` | recording_id + cookies |
| **逐字稿** | minutes/detail API | 毫秒级时间戳 | recording_id + cookies |

**优化要点**：
- 浏览器用于提取 recording_id、视频URL、cookies、标题；日期从视频URL中解析
- 三个 API 仅需 recording_id + cookies，不需要 meeting_id、sharing_id、auth_share_id
- sharing_id 仅用于构造浏览器访问 URL，不参与 API 调用
- cookies 可跨会议复用，避免重复登录

**流程**：
1. `extract_sharing_id(url)` — 从 URL 提取 sharing_id（正则 `/c(?:w|rm)/([A-Za-z0-9]+)`）
2. `fetch_meeting_page(sharing_id, cookie)` — Playwright 无头浏览器：
   - 访问 `https://meeting.tencent.com/cw/{sharing_id}`
   - 等待页面加载，检测登录状态（需要时等待用户扫码）
   - 从页面HTML提取 recording_id（正则匹配19位数字，2开头）
   - 从 DOM 元素或页面文本提取标题
   - 从 `<video>` 标签提取视频URL（含签名token和日期）
   - 获取浏览器 cookies
   - 返回 (recording_id, video_url, cookies, title, date_prefix, page_text)
3. `parse_date_from_video_url(video_url)` — 从视频URL中提取日期（`TM-YYYYMMDD` → `YYMMDD-`）
4. `fetch_meeting_data_via_api(recording_id, cookies)` — API 获取纪要和时间轴：
   - `POST query-timeline` → 时间轴
   - `POST query-summary-and-note` → 纪要
   - 仅需 recording_id + cookies
5. `_parse_api_summary(info)` — 将 API 纪要响应解析为格式化 markdown
6. `fetch_transcript_via_api(recording_id, cookies)` — API 获取逐字稿
7. `parse_transcript_to_srt(transcript_lines)` — 将逐字稿转换为SRT格式
8. `generate_abs_md(summary, timeline)` — 生成摘要markdown（纪要 + 时间轴）
9. `download_video(video_url, output_path, cookies)` — 流式下载视频
10. `extract_audio(video_path, audio_path)` — ffmpeg 提取音频
11. `process_tencent_meeting(task, config, creds)` — 主入口函数

**输出文件**：
- `{date_prefix}{title}.mp4` — 视频文件
- `{date_prefix}{title}.mp3` — 音频文件
- `{date_prefix}{title}_ori.srt` — 逐字稿字幕（毫秒级时间戳）
- `{date_prefix}{title}_abs.md` — 会议摘要（纪要 + 时间轴）

**踩坑记录**：
- URL 有两种格式（`/cw/` 和 `/crm/`），需同时支持
- sharing_id 可直接从 URL 提取，无需浏览器
- 媒体 URL 的签名 token 是动态生成的，有时效性，必须从页面实时获取
- 不同会议的 token 不同，无法复用
- 同一用户的 cookies 可跨会议复用，避免重复登录
- 三个 API 实测仅需 recording_id + cookies，不需要 meeting_id、auth_share_id、share_id
- 时间轴 API 的 start_time 是秒数（非毫秒），如 253 对应 00:04:13
- 页面文本解析时间轴时，"纪要"作为结束标记需精确匹配（`line == '纪要'`），因为它也出现在 UI tab 标签中
- 页面文本解析纪要时，需跳过"模版：主题摘要 会议总结"前缀（skip 前2行）
- 标题需从浏览器 DOM 提取（SPA 页面，原始 HTML 无标题数据）；日期从视频 URL 中的 `TM-YYYYMMDD` 格式解析
- get-multi-record-info、common-record-info、get-play-url 等 API 在浏览器外部均无法正常工作（返回空数据或错误码），不可用于获取标题、日期或视频 URL
- 逐字稿 API 返回的时间戳是毫秒级，需转换为 SRT 格式（HH:MM:SS,mmm）


### 知乎训练营 (s1w_zhihu.py)

**认证方式**：browser_cookie

**流程**：
1. `fetch_page_info(page_url, cookie)` — 从知乎训练营页面提取信息：
   - 从 URL 提取 course_id 和 video_id（正则 `/training-video/(\d+)/(\d+)`）
   - 调用 catalog API 获取发布日期（`/api/education/training/{course_id}/video_page/catalog`）
   - 调用 course API 获取课程标题（`/api/education/training/course/{course_id}`）
   - 尝试多个 API 端点获取视频播放地址（play_info、video_page 等）
   - 如果 API 失败，使用 Playwright 访问页面并拦截 m3u8 请求
2. `download_video(video_url, output_path)` — ffmpeg 直接下载 HLS（`-c copy -bsf:a aac_adtstoasc`）
3. `extract_audio(video_path, audio_path)` — ffmpeg 提取 MP3

**各要素获取方式详解**：

| 要素 | 获取方式 | 说明 | 依赖 |
|------|----------|------|------|
| **course_id** | 直接从URL提取（正则） | URL路径中包含课程ID | 无 |
| **video_id** | 直接从URL提取（正则） | URL路径中包含视频ID（雪花算法生成） | 无 |
| **发布日期** | API获取（catalog）或从video_id提取 | 优先从catalog API获取，失败时从雪花ID位运算提取时间戳 | course_id + cookie |
| **课程标题** | API获取（course） | 课程基本信息 | course_id + cookie |
| **视频URL** | API获取或浏览器拦截 | 优先尝试多个API端点，失败时用Playwright拦截m3u8 | video_id + cookie |

**输出文件**：
- `{date_prefix}{title}.mp4` — 视频文件
- `{date_prefix}{title}.mp3` — 音频文件

**踩坑记录**：
- 知乎视频播放地址有多个可能的 API 端点，需依次尝试
- 发布日期优先从 catalog API 获取，失败时从 video_id（雪花算法）提取
- 部分视频需要 Playwright 拦截 m3u8 请求才能获取播放地址
- video_id 是雪花算法生成的，可通过位运算提取时间戳


### 小鹅通 (s1w_xiaoe.py)

**认证方式**：browser_cookie（通过 Playwright 注入到多个小鹅通域名）

**流程**：
1. `fetch_page_info(page_url, cookie_str)` — Playwright 无头浏览器：
   - 从 URL 提取 app_id（正则 `(app\w+)\.h5\.`）
   - 为所有小鹅通域名预设 cookie（`.xiaoecloud.com`、`.xiaoeknow.com`、`.xiaoe-tech.com`、`.xet.citv.cn`）
   - 监听 `page.on("request")` 拦截含 `.m3u8` 的请求 URL
   - 检测登录跳转（`/login/auth`），从 redirect_url 提取真实 app_id 并补充 cookie
   - 等待 12 秒让 SPA 加载视频播放器
   - 返回 (title, m3u8_url)
2. `download_m3u8(m3u8_url, referer)` — 下载 m3u8 内容，校验签名有效性
3. `fetch_aes_key(key_url)` — 从 key server 获取 16 字节 AES-128 密钥
4. 分支处理：
   - **有 AES 加密**：`download_video()` → `parse_ts_urls()` 提取分片 URL → `download_and_decrypt_segments()` 逐片下载 + AES-128-CBC 解密 + PKCS7 去填充 → 合并 TS → `remux_ts_to_mp4()` 转封装
   - **无加密**：`ffmpeg_download_hls()` 直接 ffmpeg 下载
5. `extract_audio()` — ffmpeg 提取 MP3

**AES-128 解密细节**：
- 标准 HLS AES-128-CBC 加密，m3u8 中 `#EXT-X-KEY:METHOD=AES-128,URI="..."` 指定 key URL
- IV 从 m3u8 的 `IV=0x...` 提取，无 IV 字段时默认全零 16 字节
- 每个 TS 分片独立解密：`AES-CBC(key, iv)` → 去 PKCS7 填充（`pad_len = decrypted[-1]`）
- 解密后直接拼接写入单个 TS 文件，再 ffmpeg remux 为 MP4（`-c copy -bsf:a aac_adtstoasc`）

**各要素获取方式详解**：

| 要素 | 获取方式 | 说明 | 依赖 |
|------|----------|------|------|
| **app_id** | 直接从URL提取（正则） | URL域名中包含app_id，用于设置cookie域 | 无 |
| **标题** | 浏览器提取（page.title） | SPA页面动态渲染，需等待JS执行 | cookie |
| **日期** | 页面内容解析或当前日期 | 尝试从页面HTML提取日期，失败时用当前日期 | cookie |
| **m3u8 URL** | 浏览器拦截网络请求 | SPA加载时发起m3u8请求，需监听network | cookie |
| **AES密钥** | HTTP请求key server | m3u8中指定的key URL，返回16字节密钥 | m3u8 URL |
| **视频分片** | HTTP下载+解密 | m3u8中的TS分片URL，需AES-128-CBC解密 | m3u8 URL + AES密钥 |

**输出文件**：
- `{title}.mp4` — 视频文件（remux 后）
- `{title}.mp3` — 音频文件

**踩坑记录**：
- 小鹅通有多个域名后缀（xiaoecloud/xiaoeknow/xiaoe-tech/xet.citv.cn），cookie 必须注入到所有域名，否则 SPA 加载时跨域请求无认证
- 页面可能跳转到 `/login/auth`，需从 redirect_url 解析真实 app_id 再补充 cookie 并重新导航
- m3u8 URL 含签名参数（`whref`），有时效性，过期返回 "sign not match"
- TS 分片下载有重试机制（3 次，指数退避 2s/4s/6s），应对 CDN 偶发超时
- remux 时必须加 `-bsf:a aac_adtstoasc`，否则 MP4 容器中的 AAC 音频流格式不兼容


### 熊猫学院 (s1w_panda.py)

**认证方式**：Bearer JWT token（从浏览器 Network 面板获取）

**流程**：
1. `extract_short_link(url)` — 从 URL 提取 shortLink（支持 `?param=xxx`、`/p/xxx`、`/playback/xxx` 三种格式）
2. `get_invite_info(session, short_link)` — `GET /live-student/getInviteMsg` → 返回 inviteId, inviteUserId
3. `get_course_info(session, invite_id)` — `POST /live-student/getCourse` → 课程名称、videoId、thirdPartyId（腾讯云 VOD fileId）、videoSource、isAllowPlayBack
4. `get_live_room(session, invite_id, invite_user_id)` — `POST /live-student/getLiveRoom` → liveRoomId
5. `get_video_sign(session, room_id)` — `GET /live-student/getVideoSign` → {appId, fileId, sign(psign)}
   - 先调 `get-video-seek-sign` 检查视频是否可用
6. `get_drm_playinfo(psign, file_id, app_id)` — 腾讯云 DRM 播放信息获取（详见下方）
7. `get_real_aes_key(drm_url, drm_token, overlay_key, overlay_iv)` — 从 m3u8 获取真正的 AES key
8. `download_drm_video(...)` — 逐片下载解密 + ffmpeg 合并
9. `extract_audio()` — ffmpeg 提取 MP3

**腾讯云 SimpleAES DRM 解密流程**（核心技术难点）：

```
┌─ 客户端 ─────────────────────────────────────────────────────┐
│ 1. 生成随机 overlay_key (16B hex) 和 overlay_iv (16B hex)     │
│ 2. 用腾讯云 tcplayer 硬编码 RSA 公钥 (1024-bit PKCS1v15)     │
│    加密 overlay_key → cipheredOverlayKey                      │
│    加密 overlay_iv  → cipheredOverlayIv                       │
└──────────────────────────────────────────────────────────────┘
        │
        ▼ GET getplayinfo/v4/{appId}/{fileId}?psign=...&cipheredOverlayKey=...
┌─ 腾讯云 VOD ─────────────────────────────────────────────────┐
│ 返回: drmOutput[0].url (m3u8 基础 URL) + drmToken            │
└──────────────────────────────────────────────────────────────┘
        │
        ▼ 构造 master m3u8: voddrm.token.{drmToken}.{原文件名}
┌─ CDN ────────────────────────────────────────────────────────┐
│ master m3u8 → 选最高分辨率 → 子 m3u8                          │
│ 子 m3u8 中 #EXT-X-KEY URI → license URL                      │
│ license URL → 返回 16 字节 base_key（被 overlay 加密的）       │
└──────────────────────────────────────────────────────────────┘
        │
        ▼ AES-CBC 解密: Decrypt(base_key, overlay_key, overlay_iv) → real_key
┌─ 客户端 ─────────────────────────────────────────────────────┐
│ 用 real_key + HLS IV 逐片解密 TS 分片                         │
│ ffmpeg concat 合并为最终视频                                   │
└──────────────────────────────────────────────────────────────┘
```

关键常量：
- `VOD_APPID = 1254019786`（腾讯云 VOD appId，从 ts URL 路径确认）
- RSA 公钥：tcplayer 硬编码的 1024-bit RSA 公钥（`RSA_PUB_KEY_B64`）

**各要素获取方式详解**：

| 要素 | 获取方式 | 说明 | 依赖 |
|------|----------|------|------|
| **shortLink** | 直接从URL提取（正则） | URL中明文存在，支持3种格式 | 无 |
| **inviteId** | API获取（getInviteMsg） | 通过shortLink换取邀请ID | shortLink + Bearer token |
| **课程标题** | API获取（getCourse） | 课程基本信息，包含标题和视频ID | inviteId + Bearer token |
| **liveRoomId** | API获取（getLiveRoom） | 直播间ID，用于获取播放签名 | inviteId + inviteUserId + Bearer token |
| **psign** | API获取（getVideoSign） | 腾讯云VOD播放签名，有时效性 | liveRoomId + Bearer token |
| **DRM密钥** | 多步计算获取 | 需RSA加密overlay key，调用getplayinfo/v4，再从license解密 | psign + fileId + 自生成overlay key |
| **视频分片** | CDN下载+解密 | m3u8指向的加密TS分片，需用真实AES key解密 | DRM密钥 + m3u8 URL |

**输出文件**：
- `{title}.ts` — 视频文件
- `{title}.mp3` — 音频文件

**踩坑记录**：
- overlay key/iv 必须是 hex 字符串（32 字符），不是原始 16 字节，RSA 加密的也是 hex 字符串的 bytes
- license 返回的 base_key 恰好 16 字节，AES-CBC 解密后取前 16 字节即为 real_key（无 PKCS7 填充）
- master m3u8 URL 需要在文件名前插入 `voddrm.token.{drmToken}.` 前缀，不是作为 query 参数
- 分片下载支持续传：`_tmp_{stem}/` 临时目录存放已解密分片，中断后重新运行跳过已有分片
- 临时目录仅在 ffmpeg concat 成功后才 `shutil.rmtree` 清理，避免合并失败丢失已下载数据


### 淘宝直播 (s1w_taobao.py)

**认证方式**：螳螂直播 API 双 token
- `Authorization: Bearer {bearer_token}` — 主认证
- `X-AuthorizationAccess: Bearer {access_token}` — 辅助认证
- 额外 headers：`cid`（company_id）、`scene: browser`

**流程**：
1. `parse_taobao_url(source_url)` — 正则提取 companyId 和 linkCode（`https://(\d+)\.tbkflow\.cn/pcLive/([0-9a-fA-F]+)`）
2. `get_link_params(session, api_base, link_code)` — `POST /scrm-course-api/pass/linkParam/getParamByCode`
   - 自动尝试原始 linkCode 和去 `c` 前缀两种格式（有些 URL 带 c 前缀，有些不带）
   - 返回 companyId、courseId（id）、unionId、liveId
3. `get_course_info(session, api_base, company_id, union_id, course_id)` — `POST /scrm-course-api/pass/selectCourseInfo`
   - body: `{companyId, unionId, id, flag: "CAMP-COURSE"}`
   - 返回课程标题、liveId（liveNum 优先）、liveVendor、liveMode、liveStatus
4. `get_replay_url(session, api_base, live_id)` — `POST /micor-live-guest/agora/live/video`
   - body: `{liveId, mediaType: "PC"}`（失败时去掉 mediaType 重试）
   - 返回 m3u8 URL（优先 domain 线路，fallback volcanoUrl、huaweiUrl）
5. `download_video(m3u8_url, output_path)` — ffmpeg 直接下载 HLS（`-c copy -bsf:a aac_adtstoasc`）
6. `extract_audio(video_path, audio_path)` — ffmpeg 提取 MP3（`-err_detect ignore_err` 容错）

**各要素获取方式详解**：

| 要素 | 获取方式 | 说明 | 依赖 |
|------|----------|------|------|
| **companyId** | 直接从URL提取（正则） | URL域名中包含公司ID | 无 |
| **linkCode** | 直接从URL提取（正则） | URL路径中的十六进制代码 | 无 |
| **courseId** | API获取（getParamByCode） | 通过linkCode解析得到课程ID | linkCode + Bearer token |
| **课程标题** | API获取（selectCourseInfo） | 课程详细信息，包含标题和直播ID | courseId + companyId + unionId + Bearer token |
| **liveId** | API获取（selectCourseInfo） | 直播间ID，用于获取回放地址 | courseId + Bearer token |
| **m3u8 URL** | API获取（agora/live/video） | 回放视频地址，返回3条CDN线路 | liveId + Bearer token |

**输出文件**：
- `{title}.mp4` — 视频文件
- `{title}.mp3` — 音频文件

**踩坑记录**：
- linkCode 有两种格式：带 `c` 前缀和不带，API 端两种都可能有效，代码自动尝试两种
- agora/live/video 返回 3 条 CDN 线路（domain/volcanoUrl/huaweiUrl），优先选 domain
- 部分直播 mediaType 参数不支持 "PC"，需去掉该参数重试
- ffmpeg 下载超时设为 1800s（30 分钟），音频提取加 `-err_detect ignore_err` 容忍视频流中的小错误


## 五步流水线设计

`run_pipeline.py` 依次执行 5 个步骤，任一步骤失败则中断并通知飞书群。

### 流水线调度器 (run_pipeline.py)

**运行方式**：`python src/run_pipeline.py`

**启动流程**：
1. 加载 `credentials.yaml`，调用 `ensure_token()` 检查飞书 token 是否过期（过期前 60s 触发刷新）
2. 读取 `input.yaml` 获取任务列表，检测 yitang 任务时自动切换输出目录到 `out-yitang/`
3. 发送飞书群通知（开始）
4. 依次执行 STEPS 列表中的脚本，每步用 `subprocess.Popen` 启动子进程
5. 实时输出子进程 stdout 并写入日志文件（`log-err/{step_id}_pipeline.log`）
6. 失败时发送飞书群通知（错误详情 + 日志路径）并中断
7. 全部成功后发送飞书群通知（成功 + 输出文件统计）

**飞书通知机制**：
- 使用 app_access_token（非 user token），通过 `POST /im/v1/messages` 发送文本消息
- 目标群 chat_id 硬编码为 `FEISHU_NOTIFY_CHAT_ID`

### Step1: 视频下载 (s1_huifang.py)

**输入**：`input.yaml` 中的 tasks 列表
**输出**：`output/{title}.ts` 或 `.mp4`、`output/{title}.mp3`、可能的 `_ori.srt`/`_ori.md`
**逻辑**：遍历 tasks，按 `source_type` 动态 import 并调用对应模块的 `process_xxx()` 函数

### Step2: 教学文档 (s2_wiki.py)

**输入**：`input.yaml` 中每个 task 的 `source_wiki_url`、`target_wiki_url`
**依赖**：`url2md.feishu_url_to_md()`（已内化）和 `s2w_yitang_wiki.YitangCopier`
**输出**：`output/{title}_wiki.md`（普通任务）或 `out-yitang/{title}.md`（一堂任务）

**逻辑**：
1. 遍历 tasks，yitang 类型任务分发到 `s2w_yitang_wiki.process_yitang_wiki()`
2. 其他类型：`download_wiki()` — 调用 `url2md.feishu_url_to_md()` 下载飞书 wiki 为 markdown
3. `write_to_wiki()` — 如果配置了 target_wiki_url，将内容写入飞书 wiki

### Step3: Whisper 字幕 (s3_subtitle.py)

**输入**：`output/{title}.mp3`（s1 产出的音频）
**依赖**：`subtitle_from_mp3.transcribe_whisper()`（已内化）+ openai-whisper
**输出**：`output/{title}_wm.srt`（Whisper 生成的字幕）
**跳过条件**：如果 `output/{title}_ori.srt` 已存在（s1 已获取到字幕），则跳过

**配置参数**（config.yaml）：
```yaml
whisper:
  model: "medium"      # Whisper 模型大小
  force_cpu: false      # 强制使用 CPU
```

### Step4: 字幕修订 (s4_srt_fix.py)

**输入**：
- 字幕：`output/{title}_ori.srt`（优先）或 `output/{title}_wm.srt`
- 教学文档：`output/{title}_wiki.md`（可选）
**依赖**：`srt_fix` 模块（已内化）+ LLM API
**输出**：
- `output/{title}_ori_fix.srt` 或 `output/{title}_wm_fix.srt` — 修订后的字幕
- `output/{title}_ori_fix_changelog.md` — 修订变更日志

**逻辑**：
1. `parse_srt()` — 解析字幕文件
2. `extract_terms_from_transcript()` — 从教学文档提取专有名词
3. `apply_dict_fixes()` — 自定义词典替换（来自 yitang 的 cfg 目录）
4. `run_llm_fix()` — LLM 对比教学文档修订字幕（纠正 ASR 错误、专业术语等）
5. `apply_llm_fixes()` — 应用 LLM 修订结果
6. 输出修订后的 SRT 和变更日志

**LLM 配置**（config.yaml）：
```yaml
llm:
  provider: "minimax"
  minimax:
    model: "MiniMax-Text-01"
    base_url: "https://api.minimax.chat/v1"
    max_tokens: 8192
    temperature: 0.3
```

**修订策略**：
- 有教学文档时使用 `srtfix-ref.md`（参考文档修订）
- 无教学文档时使用 `srtfix-noref.md`（纯 ASR 纠错）
- chunk_size: 80 条字幕为一批送 LLM

### Step5: 生成 Addon (s5_addon.py)

**输入**：
- 字幕：优先 `_ori_fix.srt` → `_wm_fix.srt` → `_ori.srt` → `_wm.srt`
- 教学文档：`output/{title}_wiki.md`（可选）
**依赖**：`addon` 模块（已内化）+ LLM API
**输出**：`output/{title}_addon.md` — 补充内容报告

**逻辑**：
1. `parse_srt()` — 解析字幕
2. `parse_transcript()` — 解析教学文档为章节
3. `chunk_srt_text()` — 字幕按 chunk_size（默认 30000 字符）分段
4. 逐段调用 LLM 对比字幕与教学文档，提取"字幕中有但文档中没有"的补充信息
5. `merge_results()` + `render_full_report()` — 合并结果并渲染为 markdown 报告

## 配置文件设计

### config.yaml — 步骤参数

```yaml
# 各步骤输入配置
input:
  s4:
    subtitle: ""         # 字幕文件路径
    transcript: ""       # 逐字稿（文件路径或飞书 URL）
  s5:
    transcript: ""       # 逐字稿
    subtitle: ""         # 字幕文件路径
    discussion: ""       # 讨论区文件路径（.xlsx）

# 各步骤输出配置
output:
  s4:
    suffix: "_fix"       # 修正后字幕文件后缀
  s5:
    fnprefix: ""         # 输出文件名自定义前缀

# 字幕引擎后缀映射
s3_engine_suffix:
  whisper_medium: "_wm"
  whisper_large: "_wl"
  # ...

# 字幕修订配置
s4_fix:
  prompt: "srtfix-ref.md"
  prompt_noref: "srtfix-noref.md"
  chunk_size: 80
  custom_dict: "srtfix-dict.yaml"

# 补充内容提取配置
s5_analysis:
  chunk_size: 30000
  prompt_subtitle: "addon-subtitle.md"
  prompt_discussion: "addon-discussion.md"
  prompt_digest: "addon-digest.md"

# LLM 模型配置池
llm_plan:
  current_s4:
    name: "minimax"
    temperature: 0.2
  current_s5:
    name: "minimax"
    temperature: 0.3
  minimax:
    model: "MiniMax-Text-01"
    base_url: "https://api.minimax.chat/v1"
    max_tokens: 8192
  # volcengine, dashscope, deepseek 等其他模型...
```

### input.yaml — 任务列表与输入配置

```yaml
titlePPL: ""                    # pipeline 专用标题前缀
path_output_dir: "output/"      # 默认输出目录
path_log_dir: "log-err/"        # 日志输出目录（所有模块共用）
path_yitang_dir: "out-yitang/"  # 一堂任务专用输出目录
titleShougong: ""               # 手工指定输入标题

# 一堂 AILive 类课程的特殊处理
s1_yitang_ailive:
    output_prefix: "AI落地Live_"    # 视频文件命名约定
    query_copystr: "AI落地Live"     # 全文复制的标题关键词

# 一堂专题课 wiki 的复制范围
s1_yitang_wikicopy:
  start_heading: "开始上课"
  end_heading: "作业与Candy"

# 回放任务列表
tasks:
  - source_type: "yitang"
    source_huifang_url: "https://air.yitang.top/live/xxx"
    source_wiki_url: "https://yitang.top/fs-doc/xxx"
    target_wiki_url: ""           # 写入目标飞书 wiki（留空则不写入）
    title: ""                     # 手动指定标题（留空则自动获取）
    output_name: ""               # 手动指定输出文件名（不含扩展名）

  - source_type: "feishu_minutes"
    source_huifang_url: "https://xxx.feishu.cn/minutes/xxx"
    source_wiki_url: "https://xxx.feishu.cn/wiki/xxx"
    target_wiki_url: ""
    title: ""
```

### credentials.yaml — 敏感凭证（.gitignore）

按平台分区存放 token、cookie、secret 等认证信息。

```yaml
yitang:
  token: "xxx"                       # 一堂 API token
  user_id: "xxx"                     # 一堂用户 ID

feishu:
  app_id: "cli_xxx"                    # 飞书应用 ID
  app_secret: "xxx"                    # 飞书应用密钥
  redirect_uri: "http://localhost:8080/callback"  # OAuth 回调地址
  scopes: ["minutes:minutes:readonly"] # OAuth 权限范围
  user_access_token: "u-xxx"           # 用户 token（自动刷新）
  user_refresh_token: "ur-xxx"         # 刷新 token
  user_token_expire_time: 1234567890   # 过期时间戳（秒）
  browser_cookie: "session=xxx; ..."   # 浏览器 cookie（跨租户 fallback）

panda:
  token: "eyJhbGciOiJIUzI1NiJ9..."     # JWT Bearer token

xiaoe:
  browser_cookie: "xe_token=xxx; ..."  # 浏览器 cookie

taobao:
  bearer_token: "xxx"                  # 主认证 token
  access_token: "xxx"                  # 辅助认证 token
  company_id: "81025"                  # 公司 ID（从 URL 提取）
  union_id: "xxx"                      # 用户 union ID（可选 fallback）
  api_base: "https://cg.infyrasys.cn"  # API 基础地址

minimax:
  api_key: "xxx"                       # MiniMax API key（s4/s5 使用）
```

### 辅助工具 modules/feishu_auth.py

飞书 OAuth 授权工具（位于 `src/modules/feishu_auth.py`），用于首次获取 user_access_token：
- 启动本地 HTTP 服务器（localhost:8080）接收 OAuth 回调
- 自动打开浏览器跳转飞书授权页面
- 收到 authorization_code 后换取 user_access_token + refresh_token
- 写回 credentials.yaml


## 设计决策与经验总结（2026-03-16 ~ 03-22）

本节记录项目从创建到当前状态的关键设计决策、方案取舍和踩坑经验。

### 零、项目演进时间线

```
03-16  9fbaec1  项目创建 — 飞书妙记下载 + 五步流水线框架
       8dde827  fix: s2 同步 credentials 到 yitang 避免 refresh_token 冲突
       3898b65  feat: 新增熊猫学院下载框架
       e227aef  feat: 实现腾讯云 SimpleAES DRM 解密
03-17  93f5e25  feat: 新增小鹅通 HLS AES-128 解密下载
03-18  d6f7626  refactor: 拆分 s1_huifang.py 为调度器 + 平台模块
       4e74404  rename: s1_feishu.py → s1_feishumiaoji.py
       654afa5  docs: 新增 DESIGN/CHANGELOG/GUIDE 三份文档
       35e927c  docs: GUIDE.md → ReadMe.md，新增 QuickGuide.md
03-19  8dc190a  feat: 新增腾讯会议 + 知乎模块，流水线支持任意步骤启动
       29f5d97  simplify: 腾讯会议精简 — 去掉 meeting_id/auth_share_id
       a4ed759  docs: 腾讯会议 API 精简后更新文档
03-20  4598474  rename: 平台脚本统一改名 s1w_ 前缀
       6b6c496  feat: s3 多引擎字幕重写 — 脱离 yitang
       2a2b7d0  feat: s4 字幕修订重写 — 脱离 yitang
       242b186  feat: s5 信息拓展重写 — 脱离 yitang
03-21  247dd92  refactor: 分离 yitang 依赖，统一配置结构（config/input/credentials 三文件）
              + 新增一堂视频/文稿模块（s1w_yitang_video, s2w_yitang_wiki）
              + 抽离 feishu_minutes.py，消除 s2→s1w 跨层依赖
              + auth.py → feishu_auth.py
03-22         improve: s2w 日志系统重构（按文章独立日志 + 警告收集 + 上下文定位）
              refactor: pathdir → path_output_dir，新增 path_log_dir，全模块配置驱动
              improve: url2md.py -o 参数改为必填
03-22~23      refactor: 新建 src/modules/ 公共模块包
              + ffmpeg_utils.py — 统一 ffmpeg 调用（extract_audio、download_hls、remux_ts_to_mp4、concat_ts、mp3_to_wav、mp3_to_pcm）
              + config_utils.py — 统一配置加载（load_config、safe_filename）
              + feishu_auth/token/minutes 移入 modules/
              improve: 一堂音频回退（服务端 MP3 不存在时从视频提取）
              fix: 一堂讨论区 XLSX 导出过滤 XML 非法控制字符
              improve: 一堂/飞书妙记执行顺序调整为"文本优先，大文件最后"
              cleanup: _detect_url_type() 去掉 "fs" 兜底，未知类型给 warning
```

6 天内完成：7 个平台接入、5 步流水线独立化、3 轮架构重构。节奏很快，但每一步都有明确的驱动力（下文逐一展开）。

### 一、架构演进：从 yitang 寄生到独立项目

**背景**：dl-video 最初作为 yitang 项目的子模块存在，s2-s5 步骤通过 `sys.path.insert()` 引用 yitang 的代码（`subtitle_from_mp3.py`、`yitang_srt_fix.py`、`yitang_addon.py`、`yitang_wiki.py`）。随着平台增多，这种寄生关系带来了严重问题。

**问题**：
- 部署耦合：dl-video 无法独立运行，必须同时部署 yitang 项目
- 路径脆弱：`sys.path.insert()` 依赖相对路径，目录结构变动即崩溃
- credentials 冲突：两个项目共享 `credentials.yaml`，飞书 token 刷新互相覆盖（8dde827 修复过一次）
- 配置散落：步骤参数分散在 yitang 的多个 config 文件中

**方案对比**：

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| 保持 sys.path 引用 | 零改动 | 部署耦合、路径脆弱、credentials 冲突 | 否决 |
| pip install yitang 作为包 | 标准 Python 方式 | yitang 不是通用库，过度工程化 | 否决 |
| 将核心逻辑内化到 dl-video | 彻底解耦，独立部署 | 一次性工作量大，后续需同步维护两份代码 | **采用** |

**执行**（247dd92）：
- s3_subtitle.py：内化 Whisper 调用 + 讯飞/火山/阿里多引擎支持，新增 `model_downloader.py` 管理模型缓存
- s4_srt_fix.py：内化 LLM 字幕修订，提示词文件从 yitang cfg 复制到 `prompt/` 目录
- s5_addon.py：内化信息拓展 + 精华摘要，新增 `url2md.py` 处理飞书文档转 markdown
- 配置统一为 `config.yaml`（步骤参数）+ `input.yaml`（任务列表）+ `credentials.yaml`（凭证）

**经验**：内化后 yitang 项目的对应模块仍保留，作为"上游参考"。两边独立演进，不再尝试同步。事实证明这是正确的——内化后 dl-video 的 s4/s5 已经根据自身需求做了大量定制，与 yitang 版本差异越来越大。

### 二、s1_huifang.py 的拆分：从单体到调度器

**背景**：项目初版（9fbaec1）只有飞书妙记一个平台，所有下载逻辑都在 `s1_huifang.py` 中（423 行）。随着熊猫学院、小鹅通陆续加入，单文件膨胀到不可维护。

**拆分过程**（d6f7626）：
- `s1_huifang.py` 从 423 行缩减为纯调度器（~80 行），只做 `source_type` 分发
- 飞书妙记逻辑提取为 `s1_feishu.py`（后改名 `s1w_feishumiaoji.py`）
- 各平台模块统一暴露 `process_xxx(task, config, creds)` 入口函数

**设计约定**：
- 调度器只负责遍历 tasks + 动态 import + 调用入口函数，不包含任何平台逻辑
- 平台模块可独立运行（有 `__main__` 入口），也可被调度器调用
- 新增平台只需：新建 `s1w_xxx.py` + 在调度器加一行 `from s1w_xxx import process_xxx`

### 三、模块命名：s1w_ / s2w_ 前缀约定

**背景**：最初所有平台模块都叫 `s1_xxx.py`，与调度器 `s1_huifang.py` 同名前缀，容易混淆。

**方案对比**：

| 方案 | 示例 | 优点 | 缺点 |
|------|------|------|------|
| 放入 `platforms/` 子目录 | `platforms/feishumiaoji.py` | 物理隔离 | 需要 `__init__.py`，import 路径变长 |
| 加 `w` 后缀区分 | `s1w_feishumiaoji.py` | 最小改动，一眼区分调度器和平台模块 | 命名不够"标准" |
| 加 `_worker` 后缀 | `s1_feishumiaoji_worker.py` | 语义明确 | 文件名太长 |

**结论**：采用 `s1w_` 前缀（w = worker），改动最小且足够清晰。调度器保持 `s1_`/`s2_`，平台模块用 `s1w_`/`s2w_`。

### 四、公共模块抽离：modules/ 目录

**背景**：飞书相关功能散落在多个文件中，且存在跨层依赖（s2_wiki.py 反向 import s1w_feishumiaoji.py 的函数）。ffmpeg 调用、配置加载、文件名安全化等工具函数在 10+ 个文件中重复。

**演进过程**：
1. `feishu_token.py`（最早抽出）：token 刷新、请求头、wiki 解析 — 被 6 个文件依赖
2. `feishu_minutes.py`（本次抽出）：妙记 URL 解析 + 信息查询 — 消除 s2→s1w 跨层依赖
3. `feishu_auth.py`（本次改名）：OAuth 授权工具 — 与上述两个模块形成统一命名
4. `ffmpeg_utils.py`：统一 ffmpeg 调用（find_ffmpeg、extract_audio、download_hls、remux_ts_to_mp4、concat_ts、mp3_to_wav、mp3_to_pcm）— 消除 6+ 个文件中的重复代码
5. `config_utils.py`：统一配置加载（load_config、safe_filename）— 消除 10+ 个文件中的重复代码
6. 以上模块统一移入 `src/modules/` 目录，形成独立的公共模块包

**设计原则**：
- 公共模块只放"被多个层级调用"的函数，不做过度抽象
- 仅在一个模块内使用的函数留在原处，不为了"整洁"而搬动
- `url2md.py` → `s2w_yitang_wiki.YitangCopier` 的依赖虽然跨层，但属于合理复用（YitangCopier 的加密/认证体系深度耦合，拆分成本远大于收益），不做处理

### 五、浏览器使用策略：能不用就不用

**经验**：Playwright 无头浏览器是最后手段，不是首选。

各平台的浏览器使用程度：

| 平台 | 浏览器使用 | 原因 |
|------|-----------|------|
| 飞书妙记 | 不用 | Open API + Cookie API 覆盖所有需求 |
| 腾讯会议 | 最小化使用 | 仅提取 recording_id、视频URL、cookies；数据全走 API |
| 知乎 | API 优先，浏览器 fallback | 多个 API 端点尝试，全失败才用 Playwright 拦截 m3u8 |
| 小鹅通 | 必须使用 | SPA 页面，m3u8 URL 只能通过网络请求拦截获取 |
| 熊猫学院 | 不用 | 纯 API 链路（shortLink → invite → course → videoSign → DRM） |
| 淘宝/一堂 | 不用 | 纯 API |

**原因**：
- 浏览器启动慢（2-5 秒）、资源占用高（200-500MB 内存）
- SPA 页面渲染不确定，等待时间难以精确控制（小鹅通需等 12 秒）
- Cookie/登录状态管理复杂，跨域注入容易遗漏
- 调试困难：无头模式下看不到页面状态

**腾讯会议的精简过程**是典型案例：
1. 初版：浏览器提取 meeting_id、recording_id、auth_share_id、视频URL、cookies、标题、日期、时间轴、纪要
2. 发现 API 仅需 recording_id + cookies → 去掉 meeting_id、auth_share_id
3. 日期从视频 URL 解析（`TM-YYYYMMDD`）→ 不再从页面文本提取
4. 纪要/时间轴改用 API → 不再解析页面文本
5. 最终：浏览器只做 4 件事（recording_id、视频URL、cookies、标题），其余全走 API

### 六、DRM 解密：逆向工程的边界

**熊猫学院的腾讯云 SimpleAES DRM** 是本周技术难度最高的部分。

**关键发现**：
- 腾讯云 tcplayer 的 RSA 公钥是硬编码在 JS 中的（1024-bit），不会变
- overlay_key/iv 必须是 hex 字符串（32 字符），不是原始 16 字节 — 这个坑浪费了大量时间
- license 返回的 base_key 恰好 16 字节，AES-CBC 解密后取前 16 字节即为 real_key（无 PKCS7 填充）
- master m3u8 URL 的 drmToken 是插入到文件名前缀（`voddrm.token.{drmToken}.`），不是 query 参数

**小鹅通的 HLS AES-128** 相对简单，但也有坑：
- m3u8 URL 含签名参数（`whref`），有时效性，Playwright 捕获后必须立即下载
- IV 可能在 m3u8 中显式指定，也可能缺失（默认全零 16 字节），两种情况都要处理
- TS 分片下载需要重试机制（CDN 偶发超时），指数退避 2s/4s/6s

### 七、配置架构：三文件分离

**演进**：
- 初版：所有配置塞在一个 `config.yaml` 里
- 中期：config.yaml 膨胀到难以维护，任务列表和步骤参数混在一起
- 现在：三文件分离

| 文件 | 内容 | 变更频率 |
|------|------|----------|
| `config.yaml` | 步骤参数（LLM、引擎、prompt 路径） | 低，调好后很少改 |
| `input.yaml` | 任务列表 + 输出目录 + 一堂专用配置 | 高，每次任务都改 |
| `credentials.yaml` | token、cookie、secret | 中，token 过期时改 |

**好处**：
- `credentials.yaml` 加入 `.gitignore`，不会误提交敏感信息
- `input.yaml` 频繁修改不会干扰步骤参数
- 三个文件职责清晰，不会出现"改了一个配置影响了不相关的功能"

### 八、腾讯会议 API 精简：从 260318 快照到最终版

**对比**（DESIGN-260318.md 快照 vs 当前版本）：

260318 快照中腾讯会议模块的设计：
- 浏览器提取 4 项：meeting_id、recording_id、视频URL、cookies
- API 调用需要 sharing_id 作为 `auth_share_id` 参数
- 标题和日期通过 `get-multi-record-info` API 获取
- 时间轴通过 `get-multi-record-timeline` API 获取
- 函数名为 `extract_meeting_id()`（语义不准确，实际提取的是 sharing_id）

精简后的变化：
- 去掉 meeting_id — API 实测不需要
- 去掉 auth_share_id（即 sharing_id）— API 实测不需要
- 标题改从浏览器 DOM 提取 — `get-multi-record-info` 在浏览器外部返回空数据
- 日期改从视频 URL 解析（`TM-YYYYMMDD`）— 更可靠
- 时间轴/纪要改用 `query-timeline` 和 `query-summary-and-note` — 仅需 recording_id + cookies
- `extract_meeting_id()` 重命名为 `extract_sharing_id()` — 语义准确

**教训**：初版设计时参考了浏览器 DevTools 中看到的所有参数，以为都是必需的。实际上很多 API 参数是"传了不报错但不影响结果"的冗余参数。正确做法是从最少参数开始测试。

### 九、配置字段重命名与 LLM 配置演进

**字段重命名**（247dd92）：
- `source_url` → `source_huifang_url` — 明确是回放视频 URL，与 `source_wiki_url` 对称
- `wiki_url` → `source_wiki_url` — 加 `source_` 前缀，与 `target_wiki_url` 对称
- `output_dir` → `path_output_dir` — 与 `path_yitang_dir`、`path_log_dir` 风格统一

**LLM 配置演进**：
- 初版（260318 快照）：`llm` 单模型配置，provider + 一个模型参数块
- 现在：`llm_plan` 多模型池，`current_s4`/`current_s5` 指定各步骤使用哪个模型
- 好处：s4 和 s5 可以用不同模型（如 s4 用低温度保证准确性，s5 用高温度增加创造性），切换模型只需改 `current_s4.name`，不用改模型参数

### 十、s2w 日志系统：按文章独立日志 + 双通道记录

**背景**：s2w_yitang_wiki.py 原来只有一个固定的 `s2w_yitang_wiki.log`，多篇文章的日志混在一起，且跳过块和警告信息缺乏上下文，难以定位问题在原文中的位置。

**方案**：
- 主运行日志按文章独立生成：`wiki_{标题}_{时间戳}.log`，模块级只保留 console 输出
- 警告及错误日志：`err_{标题}_{时间戳}.log`，统一记录跳过块和警告信息
- 通过动态 FileHandler 实现：`_start_article_log()` 添加 handler，`_stop_article_log()` 移除
- 所有问题同时输出到主日志和错误日志，便于对照查阅

**上下文定位**：
- 每个跳过块/警告记录包含：block 位置（第N/M个）、block_id、parent_id、文本预览、所在章节
- 上下文包含前2段和后2段内容（各80字），通过 `_fmt_context()` 统一格式化
- block 类型使用 `_BLOCK_TYPE_NAMES` 中文名称映射（如 type=27 → "图片"）

### 十一、配置驱动的日志和输出目录

**背景**：所有模块原来硬编码 `LOG_DIR = PROJECT_DIR / "log-err"`，修改日志目录需要改 16 个文件。

**方案**：
- `input.yaml` 新增 `path_log_dir` 字段（默认 `"log-err/"`）
- `pathdir` 重命名为 `path_output_dir`，与 `path_log_dir`、`path_yitang_dir` 风格统一
- 所有 16 个源文件改为模块级读取 `input.yaml`：
  ```python
  _input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
  LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
  ```
- 修改日志目录只需改一处配置

### 十二、踩坑备忘

**credentials 冲突**（8dde827）：dl-video 和 yitang 共享同一个 `credentials.yaml` 时，两边都会刷新飞书 token 并写回文件。A 刷新后 B 读到旧的 refresh_token 再刷新就会失败（refresh_token 是一次性的）。解决方案：独立化后各自维护自己的 credentials。

**飞书妙记 Unicode 转义**：页面 HTML 中的视频/字幕 URL 嵌在 SSR 渲染的 JSON 中，含 `\uXXXX` 转义。直接正则匹配 URL 会失败，必须先 `decode_unicode_escapes()` 解码整段 JSON 再提取。

**腾讯会议 API 的"假参数"**：初版代码传了 meeting_id、sharing_id、auth_share_id 等一堆参数给 API，实测发现三个 API 只认 recording_id + cookies，其余参数完全无效。教训：先用最少参数测试，再逐步添加，而不是一开始就传所有能拿到的参数。

**ffmpeg 音频提取容错**：部分平台的视频流有小错误（如淘宝直播的 TS 流），ffmpeg 默认会中断。加 `-err_detect ignore_err` 可以容忍这些错误继续提取音频。

**Whisper 模型缓存**：`faster-whisper` 默认每次启动都联网检查模型版本，在离线环境会超时失败。`model_downloader.py` 的 `get_model_path()` 优先返回本地缓存路径，避免联网检查。

**飞书妙记跨租户双通道**：同一个飞书应用的 user_access_token 只能访问本租户的妙记。跨租户（如 waytoagi.feishu.cn）必须用 browser_cookie 走 Cookie API。Cookie API 的 transcript 数据结构与 Open API 不同（`sentences[].contents[]` vs 直接 `text`），代码中需要归一化处理。

**小鹅通多域名 cookie 注入**：小鹅通有 4 个域名后缀（`.xiaoecloud.com`、`.xiaoeknow.com`、`.xiaoe-tech.com`、`.xet.citv.cn`），SPA 加载时会跨域请求。如果只给当前域名注入 cookie，其他域名的请求会因无认证而失败。必须在 Playwright 启动时为所有域名预设 cookie。

**淘宝直播 linkCode 双格式**：URL 中的 linkCode 有带 `c` 前缀和不带两种格式，API 端两种都可能有效。代码自动尝试两种，先试原始值，失败后去掉 `c` 前缀重试。

**视频下载断点续传的局限**：飞书妙记的视频 CDN 不一定支持 HTTP Range。`download_video()` 检测到 status_code==200（而非 206）时放弃续传，从头下载。熊猫学院的分片下载则通过临时目录实现续传——已解密的分片保留在 `_tmp_{stem}/` 中，中断后重新运行跳过已有分片。
