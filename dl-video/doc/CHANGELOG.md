# dl-video 更新历史

按时间倒序排列，基于 git log 整理。

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
