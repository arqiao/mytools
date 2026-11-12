# media-download 1.0.0

视频号公共下载器，唯一源码、测试和接口说明位于本目录。支持已核验的 `https://weixin.qq.com/sph/...` 分享链接；不采集微信消息、不生成 sph、不运行 ASR 或飞书发布。dl-video 的 `src/s1w_wechatchannels.py` 是薄兼容层。

## 安装与依赖

全局命令：`/Users/arqiao/.local/bin/media-download`，符号链接到本目录 `bin/media-download`。入口按自身真实路径导入包，与当前工作目录无关。

依赖 Python 3.10+（本机验收 3.14.10）、requests、Playwright、Chromium、ffmpeg、ffprobe。版本约束见 `requirements.txt`；本机复用已有 requests 2.34.2、Playwright 1.61.0 和 Playwright 浏览器缓存，没有另装第三方 Skill、修改 shell 或迁移凭据。命令使用 PATH 中的 `python3`；换 Python 环境须在所用环境安装本工具依赖。`FFMPEG_PATH` 可显式指定 ffmpeg；`PLAYWRIGHT_CHROMIUM_PATH` 可指定已有 Chromium。

```bash
cd /tmp
/Users/arqiao/.local/bin/media-download --version
/Users/arqiao/.local/bin/media-download doctor
```

`doctor` 默认只检查本机依赖和 profile 目录是否存在，`auth_status=unchecked` 不能代表已登录。在线身份检查必须显式加 `--check-auth`；它不解析、不下载、不打开交互登录流程：

```bash
media-download doctor --check-auth \
  --browser-profile /Volumes/DATADRIVE/workspace/mytools/dl-video/.browser_data_wxchannels
```

## GitHub 版本与新机器安装

GitHub 版本副本位于 [arqiao/mytools 的 media-download/](https://github.com/arqiao/mytools/tree/main/media-download)，与 `dl-video/` 平级；它保存可恢复的代码、测试、依赖说明和许可证。本机仍只在本目录维护和运行，发布时将这些文件同步到临时 Git 检出目录，不在 dl-video 内恢复第二套下载实现。

首次安装示例（目标目录和命令须尚不存在；已有安装先核对差异，不覆盖）：

```bash
git clone git@github.com:arqiao/mytools.git mytools
cd mytools
# 先检查上述目标未占用，然后复制纯源码目录
mkdir -p "$HOME/.local/share/arqiao-tools" "$HOME/.local/bin"
test ! -e "$HOME/.local/share/arqiao-tools/media-download" && \
  cp -R media-download "$HOME/.local/share/arqiao-tools/media-download"
# 在命令所用的 Python 环境安装 requirements.txt 中的依赖
python3 -m pip install -r "$HOME/.local/share/arqiao-tools/media-download/requirements.txt"
# 如缺少 Chromium，再执行：python3 -m playwright install chromium
ln -s "$HOME/.local/share/arqiao-tools/media-download/bin/media-download" "$HOME/.local/bin/media-download"
"$HOME/.local/bin/media-download" doctor
```

还需本机提供 ffmpeg/ffprobe；已有安装直接复用。GitHub 不携带账号、浏览器 profile、Cookie、运行索引或媒体。在线使用时显式指定本机已有 profile。dl-video 依赖另外两个共享工具的准备方法仍见其文档。

## 稳定 CLI

每次输出一份 JSON 到 stdout。正常业务失败也输出 JSON；命令行语法错误由 argparse 返回 usage（退出码 2）。成功退出 0，失败 1，已有产物缺失/未完成为 3。版本 `1.0.0`，回执 `schema_version=1`；新增可选字段保持 v1 兼容。

```bash
# 显式单链接；默认 headless、64 MiB、全片严格解码、不提取独立音频
media-download download 'https://weixin.qq.com/sph/A3tC41vWaX' \
  --browser-profile /Volumes/DATADRIVE/workspace/mytools/dl-video/.browser_data_wxchannels \
  --output-dir /Users/arqiao/Downloads/media-download \
  --audio --require-audio --max-bytes 67108864

# 只读本地状态，重新校验；不认证、不联网、不提取音轨、不写清单/索引
media-download status 'https://weixin.qq.com/sph/A3tC41vWaX' \
  --output-dir /Users/arqiao/Downloads/media-download --audio --require-audio

# 指定备用接口/编码。已有同名不同编码文件会报冲突，不会覆盖或转码
media-download download 'https://weixin.qq.com/sph/A3tC41vWaX' \
  --browser-profile /Volumes/DATADRIVE/workspace/mytools/dl-video/.browser_data_wxchannels \
  --output-dir /Users/arqiao/Downloads/media-download --route feed --codec h264
```

参数：

| 参数 | 默认与含义 |
|---|---|
| `--output-dir` | `~/Downloads/media-download`；相对路径基于调用者 cwd，回执写规范化绝对路径 |
| `--browser-profile` | `~/.local/state/arqiao-tools/media-download/browser`；不隐式引用 dl-video。过渡期由调用方显式传旧 profile |
| `--audio` | 下载时提取 M4A；status 时要求独立音轨已完成（无音轨且未要求 require-audio 则允许 absent） |
| `--require-audio` | 必须有有效音轨；dl-video 总是开启该选项并提取音轨 |
| `--codec auto/h264/h265` | auto；显式编码严格选择相应流，并以 ffprobe 实测确认，不静默降级、不改标签冒充 |
| `--route auto/finder/feed` | auto；显式路线便于有界诊断，不能绕过认证/权限 |
| `--request-timeout` | 30 秒；每次连接最多 min(15,N) 秒、空闲读取 N 秒，不是严格总任务期限 |
| `--retries` | 1，允许 0–2；只重试 HTTP API 网络/5xx，媒体下载从不自动重试 |
| `--max-bytes` | 67108864；单次媒体正文的准入与应用读取/写入上限；不含 API JSON、HTTP/TLS/TCP 开销、系统 socket 预取 |
| `--lock-timeout` | 30 秒；等待同目录同分享链接的排他锁 |
| `--headless / --no-headless` | headless；后者显式允许在同一个 profile 会话里交互登录，不自动创建第二个登录窗口 |
| `--browser-fallback / --no-browser-fallback` | 开启；整个解析最多回退一次，不含读取 profile Cookie 所需的 Chromium 启动 |
| `--login-timeout` | 300 秒；可见浏览器等待用户登录的轮询窗口，另有单请求超时 |
| `--verify-decode / --no-verify-decode` | 默认全片严格解码；关闭后仍查 MP4 box、流、时长、大小与摘要，但回执明确 `full_decode=false` |

有 Content-Length 时，超过上限在读正文前拒绝，接收后核对精确长度。无长度时按剩余额度读取，恰好达到上限也保守报 `byte_limit`，不会多读一个字节来探测 EOF。`received_bytes` 是应用实际取得的媒体正文，`written_bytes` 是写入本轮临时文件的正文；并非网卡总流量。

单次 download 只处理一个链接、最多发起一次媒体 GET，不批量、不调度。网络慢、API 重试、浏览器等待和本地解码可以让总耗时超过 request-timeout。ffprobe 上限 60 秒；每次完整解码/音轨提取上限 max(300 秒, 声明时长×2)。

## Python 接口

```python
import sys
sys.path.insert(0, '/Users/arqiao/.local/share/arqiao-tools/media-download')
from media_download import download, status, doctor

result = download(
    'https://weixin.qq.com/sph/A3tC41vWaX',
    '/Users/arqiao/Downloads/media-download',
    {'browser_profile': '/Volumes/DATADRIVE/workspace/mytools/dl-video/.browser_data_wxchannels',
     'extract_audio': True, 'require_audio': True, 'max_bytes': 67108864},
)
if result['status'] != 'success':
    raise RuntimeError(result['error_code'])
# result['video_path'] / result['audio_path'] 为已验证的文件
```

`download(url, output_dir=None, options=None, *, title='')`、`status(url, output_dir=None, options=None)`、`doctor(options=None, *, check_auth=False)` 返回回执。Python options 对应 CLI 的下划线名，`--audio` 对应 `extract_audio`；额外 `state_dir` 用于调用者显式隔离运行状态，默认不变。需要互斥协作的调用方应共用默认 state_dir 与输出目录。其余下划线函数属于内部实现，不作为 group-digest 接入合同。

## 回执与状态

实际成功回执示例（节选，字段值来自 2026-11-12 验收）：

```json
{
  "schema_version": 1,
  "tool_version": "1.0.0",
  "status": "success",
  "asset_id": "wx_A3tC41vWaX",
  "source_url": "https://weixin.qq.com/sph/A3tC41vWaX",
  "resolved_by": "yuanbao-http/findergetobjecturl",
  "video_path": "/Volumes/DATADRIVE/data/Downloads/media-download/wx_A3tC41vWaX.mp4",
  "audio_path": "/Volumes/DATADRIVE/data/Downloads/media-download/wx_A3tC41vWaX.m4a",
  "audio_status": "extracted",
  "duration": 325.009002,
  "bytes": 7097201,
  "sha256": "42500a9ad4a6c3978bc6b5832312b27eb5dc2eb97614dbbff604e0a0f941eddc",
  "codec": "h264",
  "verification": {"mp4_structure": true, "streams": true, "duration": true,
                   "full_decode": true, "audio_full_decode": true, "content_length": "matched"},
  "reused": false,
  "error_code": null,
  "activity": {"api_requests": 3, "parse_requests": 1, "browser_fallbacks": 0,
               "download_attempts": 1, "received_bytes": 7097201, "written_bytes": 7097201,
               "content_length": 7097201}
}
```

- `status`：success、failed；本地产物查询另有 missing、incomplete。仅有 `.part` 或视频已发布而音轨/清单未完成，都不能冒充成功。状态查询描述当前文件状态，不是历史失败任务日志。
- `audio_status`：extracted、not_requested（视频内有音轨但未提取）、absent（视频本身无音轨）、missing（清单声明的音频已缺失）、unknown（尚未验证）。
- `bytes`/`sha256` 只对应 MP4；提取后另有 `audio_sha256`。`duration` 单位秒；保留旧 `duration_seconds`、`title`、`completed_at` 字段供 dl-video。
- `verification.content_length` 为 matched 或 not_available；复用可保留此前证据，历史文件未保存 HTTP 长度时不捏造。
- `reused=true` 表示已校验复用本地视频。允许补提取缺失音轨、更新本轮成功清单；不代表零本地工作。`activity.download_attempts=0` 才表示本次没有媒体 GET。
- `api_requests` 统计 HTTP 尝试（包括重试）与浏览器接口 fetch；不含页面加载、Cookie 读取和媒体 GET。`parse_requests` 是其中 get_parse_result 的实际尝试次数。
- `resolved_by` 形如 `yuanbao-http/findergetobjecturl`、`yuanbao-http/get_feed_info` 或 browser 版本；已保存清单优先保留原解析来源。无历史清单的文件写 existing-file。
- 失败回执仍包含合同字段，未验证字段为 null/0/空对象。原始响应、Cookie、wx_export_id、token 和签名直链不进入回执/清单/普通日志。

## 解析、认证和回退

1. 通过 Playwright 打开调用者明确提供的既有 profile，只取适用于元宝的 Cookie 放入内存 requests Session。HTTP 优先并不意味着完全不启动 Chromium；常规成功路线不加载元宝页面。
2. `getuserinfo` 必须有真实用户标识且不是 anonUser；匿名 200 也失败。未找到 profile/Cookie 为 auth_missing，HTTP 401/匿名身份为 auth_expired，403 为 permission_denied；不把 HTTP 200 当作登录证明。
3. HTTP `get_parse_result` 成功后只解析一份数据。网络/5xx/字段结构异常才允许一次浏览器页面回退；空解析、权限、限流和未知业务错误直接停止。headless 下认证失败不回退。显式 no-headless 才允许同一会话等待登录。
4. auto 默认使用 `wx_export_id → findergetobjecturl`。原接口缺 videoUrl、网络/5xx、缺所需编码且已有 token/eid 时，可一次转到备用接口；认证、权限、限流和不可信来源错误不切换路线。
5. 备用路线从受限 `channels.weixin.qq.com` playable_url 取得 token + eid（缺 eid 可用 wx_export_id），请求固定 `finder-preview/api/feed/get_feed_info`；支持实际观察到的 HTTP 200/201，再验证业务码和 feedInfo。该会话不携带元宝 Cookie。
6. 显式 h264/h265 在有 token/eid 时直接选备用接口的对应 VideoInfo；auto 按 h264/base/h265。选择完成后以本地 ffprobe 核实实际编码；编码不符拒绝发布。无 token 时原接口只接受能明确对应请求编码的流。
7. 元宝 API HTTP 重定向不跟随；浏览器 API fetch 使用 redirect:error。媒体只接受 HTTPS `finder.video.qq.com`，拒绝所有媒体重定向，不向第三方解析服务回退。

错误码主要分组：auth_missing / auth_expired、permission_denied、rate_limited、network_error / server_error / http_error、parse_empty / parse_missing_fields / schema_changed / business_error / stream_empty、codec_unavailable / codec_mismatch、unsafe_media_url / media_redirect / media_access_denied / media_http_error、byte_limit / length_mismatch / download_interrupted、invalid_media / truncated_media / decode_failed / verification_failed / audio_absent / duration_mismatch / audio_extract_failed、asset_busy / file_conflict / manifest_conflict / asset_incomplete、invalid_url / invalid_options / dependency_missing / browser_unavailable / io_error / internal_error。错误正文只含固定说明或 HTTP 数字状态，不含原响应。

## 存储、重复调用和安全发布

没有隐式业务配置文件；配置来自 CLI / Python options，不读取 dl-video 的 YAML 或 secrets-loader。认证容器只显式引用，不复制 Cookie、不导出 storage_state、不迁移账号。

- 源码：本目录；没有凭据或媒体。
- 默认运行状态：`~/.local/state/arqiao-tools/media-download/{locks,assets}/`。
- 默认媒体：`~/Downloads/media-download/`，本机该路径解析到 `/Volumes/DATADRIVE/data/Downloads/media-download/`。
- 文件：`wx_<share_id>.mp4`、可选 `.m4a`、`.download.json`。唯一 `.part.mp4/.part.m4a` 用于临时写入；失败分片不续传、不当正式文件。
- 同一规范输出目录与 share_url 共用 flock；先校验已有文件，再决定下载。每份新文件通过校验后用原子 hard link 做无覆盖发布，不能覆盖冲突文件。媒体文件系统需支持硬链接（本机 APFS 已验收）。音视频时长容差 max(2 秒, 视频时长×1%)。
- 成功清单在音轨要求满足后原子更新；旧流水线需要本轮 completed_at，因此零下载复用也更新清单。损坏的历史文件报错，不自动删改。
- assets 索引按输出目录和分享链接的摘要隔离；只登记，不跨目录自动查找或跨授权范围共享。不要修改各调用方 state_dir 以绕开互斥。
- `asset_id=wx_<share_id>` 是分享级标识，不是稳定视频对象 ID。不同 sph、同标题都不自动合并；不承诺最高画质/上传原片。

## dl-video 兼容

保留旧 CLI 参数、配置覆盖顺序、默认 `out-wechat/` 与命名；项目自行将相对路径转为项目根目录下的绝对路径，再显式传给公共工具。`process_wechat_channels(task, config, creds=None)` 保留；creds 参数不用于元宝认证。原流水线导入的 artifact_paths / extract_share_url / wechat_output_dir / ChannelsError 保留，解析/下载/校验只维护本工具一份。

dl-video 总是要求音轨并提取 M4A；为长课程单独映射默认 `max_bytes=16 GiB`，可由 `wechat_channels.max_bytes` 覆盖；这是一项新增保护上限。公共 CLI 默认仍为 64 MiB。未修改 S3/S4/S5 或其他平台实现。

迁移备份：`~/.local/state/arqiao-tools/migrations/media-download-20261112T150531Z/manifest.json`。按 source/backup 清单逐文件恢复前先核对迁移后新改动；不覆盖当前新增工作。备份仅代码和说明，无凭据/profile/媒体。公共工具全为本轮新增，回滚旧入口后可先将全局符号链接移开以停用，保留源码和运行状态便于追查；不要删除媒体或认证容器。

## 测试与真实验收

```bash
python3 -m unittest discover -s /Users/arqiao/.local/share/arqiao-tools/media-download/tests -v
cd /Volumes/DATADRIVE/workspace/mytools/dl-video
# 全新检出目录先准备公开任务示例；已有私人配置不覆盖
cp -n cfg/input.example.yaml cfg/input.yaml
python3 -m unittest discover -s src -p 'test_wechat_channels.py' -v
```

公共测试为离线响应替身和临时合成 MP4，覆盖双接口、认证/权限、201、有限回退、真实 h264/h265、截断和等长损坏、长度/HTML/上限、音轨、冲突、同资产并发、失败恢复、只读状态与无网络复用。dl-video 测试覆盖薄层参数、导入身份及原 S1/S3/S4/S5 调用链，不启动模型或飞书。测试中的网络已替换，不自动进行真实验收。

2026-11-12 本机：两个接口分别对唯一授权样本 A3tC41vWaX 完成一次真实下载，均为 7,097,201 字节、325.009002 秒、H.264；两者 SHA-256 均为 `42500a9ad4a6c3978bc6b5832312b27eb5dc2eb97614dbbff604e0a0f941eddc`，与历史验收相同。两次都通过严格完整解码；主路线还提取并校验 M4A。备用验收在本轮临时目录完成，相同摘要核实后只删除该临时副本，保留主资产与证据。历史媒体未搬运、复制或删除。

证据：`~/.local/state/arqiao-tools/media-download/acceptance-20261112/`；最终测试计数、API 探测累计和交接说明见 dl-video 的 `doc/公共下载工具迁移交接.md`。真实浏览器回退、在线 h265 和其他链接未实测；故障/回退已离线覆盖，不保证服务未来不变。没有 sph 的消息卡片仍是消息输入层缺口。

来源及授权见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
