# dl-video

多平台视频下载、教学文档处理、ASR 字幕、字幕校订及内容报告工具。

- [快速使用](doc/QuickGuide.md)
- [参数与配置](doc/ReadMe.md)
- [微信视频号流程](doc/WeChatChannels.md)
- [更新记录](doc/CHANGELOG.md)

全流程入口为 `src/run_pipeline.py`。复制 `cfg/input.example.yaml` 为本机的 `cfg/input.yaml` 后填写任务；视频号也可直接使用 `--config cfg/input.wechat.example.yaml`。

```bash
cp -n cfg/input.example.yaml cfg/input.yaml
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --print-plan
```

视频号输出到 `out-wechat/`，一堂输出到 `out-yitang/`，其他任务默认输出到 `output/`。已有媒体或字幕续跑时，产物保存在输入文件旁。

本项目当前复用本机安装的 `secrets-loader` 与 `feishu-access` 共享模块；新机器需按[配置说明](doc/ReadMe.md#集中认证配置)准备这些依赖。凭据、浏览器登录态、个人任务配置、下载文件和运行日志不进入 Git。
