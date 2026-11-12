# dl-video

多平台视频下载、教学文档处理、ASR 字幕、字幕校订及内容报告工具。

- [完整文档索引](doc/INDEX.md)
- [按时间查看项目演进](doc/项目演进记录.md)
- [快速使用](doc/QuickGuide.md)
- [参数与配置](doc/ReadMe.md)
- [微信视频号流程](doc/WeChatChannels.md)
- [公共下载工具迁移交接](doc/公共下载工具迁移交接.md)
- [更新记录](doc/CHANGELOG.md)

全流程入口为 `src/run_pipeline.py`。复制 `cfg/input.example.yaml` 为本机的 `cfg/input.yaml` 后填写任务；视频号也可直接使用 `--config cfg/input.wechat.example.yaml`。

```bash
cp -n cfg/input.example.yaml cfg/input.yaml
python src/run_pipeline.py --config cfg/input.wechat.example.yaml --print-plan
```

视频号输出到 `out-wechat/`，一堂输出到 `out-yitang/`，其他任务默认输出到 `output/`。已有媒体或字幕续跑时，产物保存在输入文件旁。

本项目当前复用本机安装的 `secrets-loader`、`feishu-access` 与 `media-download` 共享模块；新机器需按[配置说明](doc/ReadMe.md#集中认证配置)准备这些依赖。凭据、浏览器登录态、个人任务配置、下载文件和运行日志不进入 Git。

说话人分离的 Hugging Face Token 可保存在本机 `~/.config/secrets/gtokens.yaml` 的 `huggingface.token`；Qwen `--qwen3-diarize` 和独立 `s3_speaker.py` 均支持自动读取。配置格式、优先级与模型授权要求见[说话人配置说明](doc/ReadMe.md#说话人分离凭据)。

视频号解析、下载及校验的唯一实现已移至 `/Users/arqiao/.local/share/arqiao-tools/media-download/`；旧 S1 CLI 和流水线保持兼容。公共单链接接口见该工具 README，迁移验证与 group-digest 接入合同见上方交接文档。

公共下载工具的 GitHub 版本位于同仓库 [media-download/](../media-download/README.md)，包含新机器安装步骤；本机唯一维护目录保持不变。
