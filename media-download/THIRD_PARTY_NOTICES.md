# 来源与许可证

核心下载、严格媒体校验、事务发布及 dl-video 兼容实现提炼自本机 dl-video，随后在本工具唯一维护。

备用 get_feed_info 协议、token/eid 提取及 h264/h265 字段参考 Backtthefuture/video-transcript 的 sph_resolver.py，固定 commit `49f2dca568c821175874648cf8b4235540faaf4c`；未导入、安装或打包整个上游仓库。相关 MIT 授权原文保留于 [licenses/video-transcript-MIT.txt](licenses/video-transcript-MIT.txt)，Copyright (c) 2026 Backtthefuture。

已查阅配套 video-download commit `5a82c6bc195407505b82afc453ed12bfeb7a013e` 作为行为比较；未采用其下载/校验函数、公共 Worker 或夸克服务。

原 dl-video 文档还记录元宝两段协议参考 yanlingLabs/video-extract-mcp 的 wechat.ts；本工具未新增复制该仓库源码。
