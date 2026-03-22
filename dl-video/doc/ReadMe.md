# dl-video 操作说明

## 环境准备

### Python 依赖

```bash
pip install requests pyyaml cryptography playwright
playwright install chromium
```

| 包 | 用途 | 使用模块 |
|----|------|----------|
| `requests` | HTTP 请求（API 调用、文件下载） | 所有模块 |
| `pyyaml` | 配置文件解析 | 所有模块 |
| `cryptography` | AES-CBC/RSA 解密 | s1w_panda.py, s1w_xiaoe.py |
| `playwright` | 无头浏览器（SPA 页面渲染、m3u8 拦截） | s1w_xiaoe.py |

s2-s5 步骤的依赖模块已内化到项目中（`url2md.py`、`s2w_yitang_wiki.py` 等），无需外部 yitang 项目。

### ffmpeg

视频下载、音频提取、TS 合并均依赖 ffmpeg。`modules/ffmpeg_utils.py` 按以下顺序查找：
1. 系统 PATH 中的 `ffmpeg`
2. `D:\tools\ffmpeg\bin\ffmpeg.exe`
3. `C:\tools\ffmpeg\bin\ffmpeg.exe`

### Whisper（s3 步骤）

s3 字幕生成依赖 faster-whisper 模型。可通过 `python src/tools/model_downloader.py medium` 预下载模型（支持 HuggingFace 镜像加速）。需安装 `faster-whisper` 及对应的 PyTorch。

### LLM API（s4/s5 步骤）

字幕修订和 addon 生成使用 LLM API。当前配置为 MiniMax，需在 `credentials.yaml` 中配置 `minimax.api_key`。

## 配置说明

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

tasks:
  - source_type: "yitang"           # 一堂（视频+文稿）
    source_huifang_url: "https://air.yitang.top/live/xxx"
    source_wiki_url: "https://yitang.top/fs-doc/xxx"
    target_wiki_url: ""             # 写入目标飞书 wiki（留空则不写入）
    title: ""                       # 手动标题（留空自动获取）
    output_name: ""                 # 手动指定输出文件名（不含扩展名）

  - source_type: "feishu_minutes"
    source_huifang_url: "https://xxx.feishu.cn/minutes/obcnxxx"
    source_wiki_url: "https://xxx.feishu.cn/wiki/xxx"
    target_wiki_url: ""
    title: ""

  - source_type: "tencent_meeting"
    source_huifang_url: "https://meeting.tencent.com/cw/lJayL90E87"
    source_wiki_url: ""
    target_wiki_url: ""
    title: ""

  - source_type: "panda"
    source_huifang_url: "https://fclive.pandacollege.cn/p/9nubG3"
    source_wiki_url: "https://forchangesz.feishu.cn/wiki/xxx"
    target_wiki_url: ""
    title: ""

  - source_type: "xiaoe"
    source_huifang_url: "https://appxxx.h5.xet.citv.cn/v4/course/alive/l_xxx"
    source_wiki_url: ""
    target_wiki_url: ""

  - source_type: "taobao"
    source_huifang_url: "https://81025.tbkflow.cn/pcLive/xxx?f=xxx"
    source_wiki_url: ""
    target_wiki_url: ""
    title: ""

  - source_type: "zhihu"
    source_huifang_url: "https://www.zhihu.com/training-video/1234567/8901234"
    source_wiki_url: ""
    target_wiki_url: ""
    title: ""
```

**字段说明**：
- `source_type`：决定调用哪个下载模块（`yitang`/`feishu_minutes`/`tencent_meeting`/`panda`/`xiaoe`/`taobao`/`zhihu`）
- `source_huifang_url`：各平台的视频/回放页面 URL（格式见下方各平台说明）
- `source_wiki_url`：关联的教学文档 URL（飞书 wiki 或一堂文档，s2 下载用）
- `target_wiki_url`：s2 步骤将处理结果写入此飞书 wiki（留空则不写入）
- `title`：手动指定标题，留空则自动从平台 API 获取
- `output_name`：手动指定输出文件名（不含扩展名），留空则自动生成

### credentials.yaml — 凭证配置

此文件包含各平台的认证信息，已加入 .gitignore，不会提交到 git。

```yaml
yitang:
  token: "xxx"                       # 一堂 API token
  user_id: "xxx"                     # 一堂用户 ID

feishu:
  app_id: "cli_xxx"                    # 飞书应用 ID
  app_secret: "xxx"                    # 飞书应用密钥
  redirect_uri: "http://localhost:8080/callback"
  scopes: ["minutes:minutes:readonly"]
  user_access_token: "u-xxx"           # 自动刷新，无需手动维护
  user_refresh_token: "ur-xxx"         # 自动刷新
  user_token_expire_time: 1234567890   # 自动更新
  browser_cookie: "session=xxx; ..."   # 手动从浏览器复制

tencent_meeting:
  cookie: "wm_login_sid=xxx; ..."      # 浏览器 cookie（用于认证和API调用）

panda:
  token: "eyJhbGciOiJIUzI1NiJ9..."     # JWT Bearer token

xiaoe:
  browser_cookie: "xe_token=xxx; ..."  # 完整 cookie 字符串

taobao:
  bearer_token: "xxx"                  # Authorization 头
  access_token: "xxx"                  # X-AuthorizationAccess 头
  company_id: "81025"                  # 从 URL 中的数字提取
  union_id: "xxx"                      # 可选
  api_base: "https://cg.infyrasys.cn"  # API 基础地址

zhihu:
  browser_cookie: "z_c0=xxx; ..."      # 浏览器 cookie

minimax:
  api_key: "xxx"                       # s4/s5 LLM 调用
```

## 各平台使用方法

### 一堂

**URL 格式**：`https://air.yitang.top/live/{liveId}`

**认证获取**：
1. 浏览器打开一堂直播回放页面
2. F12 → Network → 筛选 `yitang.top` 域名的请求
3. 点击任意 API 请求 → Headers → 找到 token 和 user_id
4. 分别填入 `credentials.yaml` 的 `yitang.token` 和 `yitang.user_id`

**输出文件**：
- `out-yitang/{output_name}.ts` — 视频
- `out-yitang/{output_name}.mp3` — 音频
- `out-yitang/{output_name}.xlsx` — 讨论区
- `out-yitang/{title}.md` — 文稿（s2w_yitang_wiki.py 产出）

**注意事项**：
- AI落地Live 系列课程自动使用 `output_prefix` + 编号命名
- 非系列课程使用标题作为文件名
- 可通过 task 中的 `output_name` 手动指定文件名
- 文稿复制支持全文复制和按 `s1_yitang_wikicopy` 范围裁剪两种模式
- 音频获取优先从服务端下载 MP3，404 时自动从本地视频提取
- 讨论区导出自动过滤 emoji 和 XML 非法控制字符


### 飞书妙记

**URL 格式**：`https://{tenant}.feishu.cn/minutes/{token}`
- 示例：`https://waytoagi.feishu.cn/minutes/obcndie8r4z6726gw2j78zuw`

**认证获取**：

方式一：OAuth 授权（推荐，token 可自动刷新）
```bash
python src/modules/feishu_auth.py
```
- 自动打开浏览器跳转飞书授权页面
- 授权后 token 自动写入 credentials.yaml
- 后续运行时程序自动刷新（过期前 300s 触发）

方式二：浏览器 Cookie（跨租户场景必需）
1. 浏览器打开妙记页面
2. F12 → Application → Cookies → 选择当前域名
3. 复制所有 cookie 为字符串（`name1=value1; name2=value2; ...`）
4. 粘贴到 `credentials.yaml` 的 `feishu.browser_cookie`

**输出文件**：
- `output/{title}.ts` — 视频
- `output/{title}.mp3` — 音频
- `output/{title}_ori.srt` — 字幕（VTT 转 SRT，或 transcript 生成）
- `output/{title}_ori.md` — 文字记录（带说话人标注）

**注意事项**：
- user_access_token 过期时程序自动刷新（过期前 300s 触发），通常无需干预
- user_refresh_token 有 30 天有效期，过期需重新运行 `feishu_auth.py` 授权
- browser_cookie 用于跨租户场景，过期后需从浏览器重新复制


### 腾讯会议

**URL 格式**：`https://meeting.tencent.com/cw/{sharing_id}` 或 `https://meeting.tencent.com/crm/{sharing_id}`
- 示例：`https://meeting.tencent.com/cw/lJayL90E87`
- `/crm/` 格式会自动重定向到 `/cw/`

**认证获取**：
1. 浏览器打开会议回放页面，确认视频正常播放
2. F12 → Application → Cookies → 选择 `.meeting.tencent.com` 域名
3. 复制所有 cookie 为字符串（`name1=value1; name2=value2; ...`）
4. 粘贴到 `credentials.yaml` 的 `tencent_meeting.cookie`

**输出文件**：
- `output/{date_prefix}{title}.mp4` — 视频
- `output/{date_prefix}{title}.mp3` — 音频（直接从网页提取，非转码）
- `output/{date_prefix}{title}_ori.srt` — 逐字稿字幕（毫秒级时间戳）
- `output/{date_prefix}{title}_abs.md` — 会议摘要（纪要 + 时间轴）

**注意事项**：
- 首次运行时如检测到需要登录，程序会打开浏览器窗口等待 15 秒供扫码登录，登录成功后自动继续
- 同一用户的 cookies 可跨会议复用，无需重复登录
- 视频/音频 URL 包含签名 token，有时效性，需实时获取
- 纪要和时间轴通过 API 获取结构化数据（query-summary-and-note、query-timeline）
- 会议日期从视频 URL 中的 `TM-YYYYMMDD` 格式解析，标题从浏览器 DOM 提取
- 纪要输出保留层次格式：总结段落 → 加粗编号标题 → 列表子项 → 会议待办
- API 仅需 recording_id + cookies，无需 meeting_id 或 auth_share_id


### 知乎训练营

**URL 格式**：`https://www.zhihu.com/training-video/{course_id}/{video_id}`
- 示例：`https://www.zhihu.com/training-video/1234567890/9876543210`

**认证获取**：
1. 浏览器打开训练营视频页面
2. F12 → Application → Cookies → 选择 `.zhihu.com` 域名
3. 复制所有 cookie 为字符串（`name1=value1; name2=value2; ...`）
4. 粘贴到 `credentials.yaml` 的 `zhihu.browser_cookie`

**输出文件**：
- `output/{date_prefix}{title}.mp4` — 视频
- `output/{date_prefix}{title}.mp3` — 音频

**注意事项**：
- 发布日期从 catalog API 自动获取，用于文件名前缀
- 如果 API 获取视频地址失败，会自动使用 Playwright 拦截 m3u8 请求


### 小鹅通

**URL 格式**：`https://{app_id}.h5.xet.citv.cn/v4/course/alive/l_{course_id}`
- 也支持 `.xiaoecloud.com`、`.xiaoeknow.com` 等域名

**认证获取**：
1. 浏览器打开课程页面，确认视频正常播放
2. F12 → Application → Cookies → 选择当前域名
3. 复制所有 cookie 为字符串
4. 粘贴到 `credentials.yaml` 的 `xiaoe.browser_cookie`

**输出文件**：
- `output/{title}.mp4` — 视频（解密 + remux 后）
- `output/{title}.mp3` — 音频

**注意事项**：
- cookie 有时效性，过期后需重新获取
- 程序会自动将 cookie 注入到小鹅通的所有域名后缀（`.xiaoecloud.com`、`.xiaoeknow.com`、`.xiaoe-tech.com`、`.xet.citv.cn`）
- 使用 Playwright 无头浏览器拦截 m3u8 请求，需先安装：`playwright install chromium`
- m3u8 URL 中的签名有时效性，捕获后需尽快下载


### 熊猫学院

**URL 格式**：`https://fclive.pandacollege.cn/p/{shortLink}`
- 也支持 `?param={shortLink}` 和 `/playback/{shortLink}` 格式

**认证获取**：
1. 浏览器打开回放页面
2. F12 → Network → 筛选 `pandacollege` 域名的请求
3. 点击任意 API 请求 → Headers → `Authorization: Bearer eyJhbG...`
4. 复制 `eyJhbG...` 部分到 `credentials.yaml` 的 `panda.token`

**输出文件**：
- `output/{title}.ts` — 视频（DRM 解密后）
- `output/{title}.mp3` — 音频

**注意事项**：
- token 为 JWT Bearer token，无自动刷新机制，过期需从浏览器重新获取
- 视频使用腾讯云 SimpleAES DRM 加密，程序自动完成 RSA + AES-CBC 解密流程
- 支持断点续传（逐片下载解密 + ffmpeg 合并）


### 淘宝直播

**URL 格式**：`https://{company_id}.tbkflow.cn/pcLive/{link_code}?f={xxx}`
- 示例：`https://81025.tbkflow.cn/pcLive/980e81dbbe10025fb8d7da8e63ec472f07a1a?f=eccxpIGJ`

**认证获取**：
1. 浏览器打开直播回放页面
2. F12 → Network → 筛选 `infyrasys` 或 `tbkflow` 域名
3. 点击任意 API 请求 → Headers：
   - `Authorization: Bearer xxx` → 复制到 `taobao.bearer_token`
   - `X-AuthorizationAccess: Bearer xxx` → 复制到 `taobao.access_token`
   - `cid: 81025` → 复制到 `taobao.company_id`

**输出文件**：
- `output/{title}.mp4` — 视频
- `output/{title}.mp3` — 音频

**注意事项**：
- 三个 token（bearer_token、access_token、company_id）均无自动刷新机制，过期需从浏览器重新获取
- 程序会自动尝试带/不带 `c` 前缀两种 linkCode 格式
- URL 中的 company_id（数字部分）需与 `taobao.company_id` 一致

## 运行方式

### 0. 全流水线运行

```bash
cd dl-video
python src/run_pipeline.py
```

流水线依次执行 s1→s2→s3→s4→s5，任一步骤失败则中断并发送飞书通知。

**输入**：`cfg/input.yaml`（任务列表）+ `cfg/config.yaml`（步骤参数）+ `cfg/credentials.yaml`（认证凭证）
**输出**：产出文件写入 `output/`（默认）或 `out-yitang/`（一堂任务），日志写入 `path_log_dir` 配置的目录（默认 `log-err/`）

每步产出的文件：

| 步骤 | 输出文件 | 说明 |
|------|----------|------|
| s1 | `{title}.ts` 或 `.mp4` | 视频 |
| s1 | `{title}.mp3` | 音频 |
| s1 | `{title}.xlsx` | 讨论区（一堂才有） |
| s1 | `{title}_ori.srt` | 原始字幕（飞书妙记才有） |
| s1 | `{title}_ori.md` | 文字记录（飞书妙记才有） |
| s2 | `{title}_wiki.md` 或 `{title}.md` | 教学文档 |
| s3 | `{title}_wm.srt` | Whisper 字幕（s1 无字幕时 fallback） |
| s4 | `{title}_fix.srt` | 修订后字幕 |
| s4 | `{title}_fix_changelog.md` | 修订变更日志 |
| s5 | `{title}_信息拓展.md` | 信息拓展报告 |
| s5 | `{title}_精华摘要.md` | 精华摘要报告 |

### 1. 单步运行（仅下载视频）

```bash
cd dl-video
python src/s1_huifang.py
```

**输入**：`cfg/input.yaml` 中的 tasks 列表
**输出**：`output/{title}.ts`/`.mp4` + `.mp3`（+ 飞书妙记的 `_ori.srt`/`_ori.md`）

调度器读取所有 task，按 `source_type` 分发到对应模块。

### 1a) 单独运行某个平台模块

```bash
python src/s1w_yitang_video.py  # 仅处理 source_type=yitang 的任务
python src/s1w_panda.py         # 仅处理 source_type=panda 的任务
python src/s1w_taobao.py        # 仅处理 source_type=taobao 的任务
python src/s1w_xiaoe.py         # 仅处理 source_type=xiaoe 的任务
```

这些模块有独立的 `main()`，会从 input.yaml 筛选对应 source_type 的任务。

### 2. 单独运行后续步骤

```bash
python src/s2_wiki.py        # 下载教学文档 + 写入飞书 wiki
python src/s3_subtitle.py    # Whisper 字幕生成（需要 s1 产出的 .mp3）
python src/s4_srt_fix.py     # LLM 字幕修订（需要 _ori.srt 或 _wm.srt）
python src/s5_addon.py       # 生成补充内容（需要修订后字幕 + 逐字稿）
```

s2-s5 均为独立模块，无需外部依赖。

### 3. 飞书 OAuth 授权

```bash
python src/modules/feishu_auth.py
```

首次使用飞书妙记功能时运行。启动本地 HTTP 服务器（localhost:8080），自动打开浏览器完成 OAuth 授权，token 写入 credentials.yaml。超时 300 秒。

## 常见问题排查

### 飞书 token 过期

**现象**：`获取妙记信息失败` 或 `飞书 token 刷新失败`

**排查**：
- user_access_token 过期：程序自动刷新（过期前 300s 触发），通常无需干预
- user_refresh_token 过期（30 天有效期）：需重新运行 `python src/feishu_auth.py` 授权
- browser_cookie 过期：重新从浏览器复制

### 熊猫学院/小鹅通/淘宝 token 过期

**现象**：API 返回 401 或 `code != 0`

**排查**：这三个平台的 token 无自动刷新机制，需手动从浏览器重新获取。

### ffmpeg 未找到

**现象**：`FileNotFoundError: ffmpeg` 或 `ffmpeg 未安装或不在 PATH 中`

**排查**：确认 ffmpeg 已安装且在 PATH 中，或放置在 `D:\tools\ffmpeg\bin\ffmpeg.exe`。

### 小鹅通 m3u8 捕获失败

**现象**：`未能从页面捕获 m3u8 URL`

**排查**：
- cookie 过期：重新从浏览器获取
- Playwright 未安装：`playwright install chromium`
- 页面加载慢：代码中等待 12 秒，极慢网络可能不够（需修改 `s1w_xiaoe.py` 中的 `_time.sleep(12)`）
- 检测到登录页面：cookie 中缺少关键字段，确认复制了完整的 cookie 字符串

### 小鹅通 m3u8 签名过期

**现象**：`sign not match` 或 `m3u8 签名已过期或无效`

**排查**：m3u8 URL 中的签名有时效性。Playwright 捕获 m3u8 URL 后需尽快下载，不要长时间搁置。

### 熊猫学院 DRM 解密失败

**现象**：`getplayinfo 失败` 或 `获取 AES 解密 key 失败`

**排查**：
- token 过期：重新获取 Bearer token
- 视频不允许回放：检查 `isAllowPlayBack` 字段
- VOD 签名过期：getVideoSign 返回的 psign 有时效性

### 淘宝直播 linkCode 解析失败

**现象**：`getParamByCode 失败`

**排查**：
- URL 格式不对：确认是 `https://{数字}.tbkflow.cn/pcLive/{hex字符串}` 格式
- 程序会自动尝试带/不带 `c` 前缀两种 linkCode，如果都失败说明 token 过期或 URL 无效

### 日志查看

所有模块的日志文件在 `log-err/` 目录下（可通过 `input.yaml` 的 `path_log_dir` 配置）：

| 日志文件 | 来源 |
|----------|------|
| `s1_huifang.log` | s1 调度器 |
| `s1w_yitang_video.log` | 一堂视频模块 |
| `s1w_feishumiaoji.log` | 飞书妙记模块 |
| `s1w_tencentmeeting.log` | 腾讯会议模块 |
| `s1w_zhihu.log` | 知乎训练营模块 |
| `s1w_panda.log` | 熊猫学院模块 |
| `s1w_xiaoe.log` | 小鹅通模块 |
| `s1w_taobao.log` | 淘宝直播模块 |
| `s2_wiki.log` | 教学文档调度器 |
| `wiki_{标题}_{时间戳}.log` | 一堂文稿模块（按文章独立生成） |
| `err_{标题}_{时间戳}.log` | 一堂文稿模块的警告及错误日志 |
| `s3_subtitle.log` | Whisper 字幕模块 |
| `s4_srt_fix.log` | 字幕修订模块 |
| `s5_addon.log` | 补充内容模块 |
| `s1_pipeline.log` ~ `s5_pipeline.log` | 流水线各步骤的合并日志 |
