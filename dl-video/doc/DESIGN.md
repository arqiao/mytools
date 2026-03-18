# dl-video 需求及设计描述

## 项目背景

多平台在线课程回放的自动下载与后处理工具。支持从飞书妙记、熊猫学院、小鹅通、淘宝直播等平台下载课程视频，并通过五步流水线完成从下载到内容提取的全流程自动化。

## 架构设计

### 整体架构

```
调度器 (s1_huifang.py)
  ├── 飞书妙记模块 (s1_feishumiaoji.py)
  ├── 熊猫学院模块 (s1_panda.py)
  ├── 小鹅通模块   (s1_xiaoe.py)
  └── 淘宝直播模块 (s1_taobao.py)

五步流水线 (run_pipeline.py)
  s1(视频下载) → s2(教学文档) → s3(Whisper字幕) → s4(字幕修订) → s5(生成Addon)
```

### 目录结构

```
dl-video/
├── cfg/
│   ├── config.yaml          # 任务配置（视频源、LLM 参数）
│   └── credentials.yaml     # 敏感凭证（token、cookie、secret）
├── src/
│   ├── run_pipeline.py      # 五步流水线调度
│   ├── s1_huifang.py        # Step1 调度器：按 source_type 分发
│   ├── s1_feishumiaoji.py   # 飞书妙记下载模块
│   ├── s1_panda.py          # 熊猫学院下载模块
│   ├── s1_xiaoe.py          # 小鹅通下载模块
│   ├── s1_taobao.py         # 淘宝直播下载模块
│   ├── s2_wiki.py           # Step2: 下载教学文档 + 写入飞书 wiki
│   ├── s3_subtitle.py       # Step3: MP3 → 字幕（Whisper fallback）
│   ├── s4_srt_fix.py        # Step4: LLM 对比文档修订字幕
│   └── s5_addon.py          # Step5: 提取字幕中的补充内容
├── output/                  # 输出目录（视频、音频、字幕、文档）
├── log-err/                 # 错误日志统一目录
└── doc/                     # 项目文档
```

## 平台下载模块设计

### Step1 调度器 (s1_huifang.py)

读取 `config.yaml` 中的 `tasks` 列表，根据每个任务的 `source_type` 字段分发到对应平台模块。支持的 source_type：`feishu_minutes`、`panda`、`xiaoe`、`taobao`。

### 飞书妙记 (s1_feishumiaoji.py)

**认证方式**：双通道认证
- Open API：user_access_token（OIDC refresh_token 自动刷新，`ensure_feishu_token()` 在过期前 300s 触发）
- Cookie：browser_cookie（用于跨租户场景，Open API 无权限时 fallback）

**API 流程**：
1. `extract_minutes_token(url)` — 正则提取 URL 中的 minutes token（`/minutes/([A-Za-z0-9]+)`）
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

### 熊猫学院 (s1_panda.py)

**认证方式**：Bearer JWT token（从浏览器 Network 面板获取）

**API 流程**：
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

**输出文件**：
- `{title}.ts` — 视频文件
- `{title}.mp3` — 音频文件

**踩坑记录**：
- overlay key/iv 必须是 hex 字符串（32 字符），不是原始 16 字节，RSA 加密的也是 hex 字符串的 bytes
- license 返回的 base_key 恰好 16 字节，AES-CBC 解密后取前 16 字节即为 real_key（无 PKCS7 填充）
- master m3u8 URL 需要在文件名前插入 `voddrm.token.{drmToken}.` 前缀，不是作为 query 参数
- 分片下载支持续传：`_tmp_{stem}/` 临时目录存放已解密分片，中断后重新运行跳过已有分片
- 临时目录仅在 ffmpeg concat 成功后才 `shutil.rmtree` 清理，避免合并失败丢失已下载数据

### 小鹅通 (s1_xiaoe.py)

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

**输出文件**：
- `{title}.mp4` — 视频文件（remux 后）
- `{title}.mp3` — 音频文件

**踩坑记录**：
- 小鹅通有多个域名后缀（xiaoecloud/xiaoeknow/xiaoe-tech/xet.citv.cn），cookie 必须注入到所有域名，否则 SPA 加载时跨域请求无认证
- 页面可能跳转到 `/login/auth`，需从 redirect_url 解析真实 app_id 再补充 cookie 并重新导航
- m3u8 URL 含签名参数（`whref`），有时效性，过期返回 "sign not match"
- TS 分片下载有重试机制（3 次，指数退避 2s/4s/6s），应对 CDN 偶发超时
- remux 时必须加 `-bsf:a aac_adtstoasc`，否则 MP4 容器中的 AAC 音频流格式不兼容

### 淘宝直播 (s1_taobao.py)

**认证方式**：螳螂直播 API 双 token
- `Authorization: Bearer {bearer_token}` — 主认证
- `X-AuthorizationAccess: Bearer {access_token}` — 辅助认证
- 额外 headers：`cid`（company_id）、`scene: browser`

**API 流程**：
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
2. 读取 `config.yaml` 获取任务列表
3. 发送飞书群通知（开始）
4. 依次执行 STEPS 列表中的脚本，每步用 `subprocess.Popen` 启动子进程
5. 实时输出子进程 stdout 并写入日志文件（`log-err/{step_id}_pipeline.log`）
6. 失败时发送飞书群通知（错误详情 + 日志路径）并中断
7. 全部成功后发送飞书群通知（成功 + 输出文件统计）

**飞书通知机制**：
- 使用 app_access_token（非 user token），通过 `POST /im/v1/messages` 发送文本消息
- 目标群 chat_id 硬编码为 `FEISHU_NOTIFY_CHAT_ID`

### Step1: 视频下载 (s1_huifang.py)

**输入**：`config.yaml` 中的 tasks 列表
**输出**：`output/{title}.ts` 或 `.mp4`、`output/{title}.mp3`、可能的 `_ori.srt`/`_ori.md`
**逻辑**：遍历 tasks，按 `source_type` 动态 import 并调用对应模块的 `process_xxx()` 函数

### Step2: 教学文档 (s2_wiki.py)

**输入**：`config.yaml` 中每个 task 的 `wiki_url`、`target_wiki_url`
**依赖**：yitang 项目的 `url2md.feishu_url_to_md()` 和 `yitang_wiki.YitangCopier`
**输出**：`output/{title}_wiki.md`

**逻辑**：
1. `setup_yitang_path()` — 将 yitang/src 加入 sys.path，并同步 credentials.yaml 到 yitang 项目（避免 refresh_token 冲突）
2. `download_wiki()` — 调用 yitang 的 `feishu_url_to_md()` 下载飞书 wiki 为 markdown
3. `write_to_wiki()` — 如果配置了 target_wiki_url，将内容（addon > 修订字幕 > Whisper 修订字幕）写入飞书 wiki
   - markdown 按行转为飞书 text/heading blocks
   - 通过 docx API 批量创建 blocks（每批最多 50 个）

### Step3: Whisper 字幕 (s3_subtitle.py)

**输入**：`output/{title}.mp3`（s1 产出的音频）
**依赖**：yitang 项目的 `subtitle_from_mp3.transcribe_whisper()`
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
**依赖**：yitang 项目的 `yitang_srt_fix` 模块
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
- 有教学文档时使用 `prompt-srtfix-ref.md`（参考文档修订）
- 无教学文档时使用 `prompt-srtfix-noref.md`（纯 ASR 纠错）
- chunk_size: 80 条字幕为一批送 LLM

### Step5: 生成 Addon (s5_addon.py)

**输入**：
- 字幕：优先 `_ori_fix.srt` → `_wm_fix.srt` → `_ori.srt` → `_wm.srt`
- 教学文档：`output/{title}_wiki.md`（可选）
**依赖**：yitang 项目的 `yitang_addon` 模块
**输出**：`output/{title}_addon.md` — 补充内容报告

**逻辑**：
1. `parse_srt()` — 解析字幕
2. `parse_transcript()` — 解析教学文档为章节
3. `chunk_srt_text()` — 字幕按 chunk_size（默认 30000 字符）分段
4. 逐段调用 LLM 对比字幕与教学文档，提取"字幕中有但文档中没有"的补充信息
5. `merge_results()` + `render_full_report()` — 合并结果并渲染为 markdown 报告

## 配置文件设计

### config.yaml — 任务配置

```yaml
output_dir: "output"              # 输出目录（相对于项目根目录）
yitang_dir: "../yitang"           # yitang 项目路径（s2/s3/s4/s5 依赖）

tasks:                            # 任务列表，支持多个任务
  - source_type: "feishu_minutes" # 平台类型：feishu_minutes|panda|xiaoe|taobao
    source_url: "https://..."     # 视频源 URL（各平台格式不同，见 GUIDE.md）
    wiki_url: "https://..."       # 关联的教学文档 URL（飞书 wiki，s2 下载用）
    target_wiki_url: ""           # 写入目标飞书 wiki（s2 写入用，留空不写入）
    title: ""                     # 手动指定标题（留空则自动从平台获取）

llm:                              # LLM 配置（s4 字幕修订、s5 addon 生成）
  provider: "minimax"             # 提供商名称
  minimax:
    model: "MiniMax-Text-01"
    base_url: "https://api.minimax.chat/v1"
    max_tokens: 8192
    temperature: 0.3

whisper:                          # Whisper 配置（s3 字幕生成）
  model: "medium"                 # 模型大小：tiny|base|small|medium|large
  force_cpu: false                # 强制 CPU 推理
```

### credentials.yaml — 敏感凭证（.gitignore）

按平台分区存放 token、cookie、secret 等认证信息。

```yaml
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

### 辅助工具 auth.py

飞书 OAuth 授权工具，用于首次获取 user_access_token：
- 启动本地 HTTP 服务器（localhost:8080）接收 OAuth 回调
- 自动打开浏览器跳转飞书授权页面
- 收到 authorization_code 后换取 user_access_token + refresh_token
- 写回 credentials.yaml
