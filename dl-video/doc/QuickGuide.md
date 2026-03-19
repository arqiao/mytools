# dl-video 快速使用指南

## 前置条件

```bash
pip install requests pyyaml cryptography playwright faster-whisper opencc-python-reimplemented openpyxl
playwright install chromium
```

确保 ffmpeg 在 PATH 中，且 `cfg/credentials.yaml` 已配置好对应平台的凭证。
s2 步骤依赖 yitang 项目（`../yitang/src`），s3/s4/s5 为独立脚本无跨项目依赖。

## 各程序速查

### run_pipeline.py — 全流水线

```bash
python src/run_pipeline.py
```

自动执行 s1→s2→s3→s4→s5 全部五步，失败中断并飞书通知。
输入：`cfg/config.yaml`（tasks 列表）
输出：`output/` 下的全部产出文件

---

### s1_huifang.py — 视频下载调度器

```bash
python src/s1_huifang.py
```

读取 config.yaml 中所有 task，按 `source_type` 分发到对应平台模块。
输入：`cfg/config.yaml` + `cfg/credentials.yaml`
输出：`output/{title}.ts` 或 `.mp4` + `.mp3`（飞书妙记额外产出 `_ori.srt`、`_ori.md`）

---

### s1w_feishumiaoji.py — 飞书妙记

不单独运行，由 s1_huifang.py 调度（`source_type: "feishu_minutes"`）。

config.yaml 示例：
```yaml
tasks:
  - source_type: "feishu_minutes"
    source_url: "https://waytoagi.feishu.cn/minutes/obcnxxx"
    wiki_url: "https://waytoagi.feishu.cn/wiki/xxx"
    title: ""
```

凭证：`feishu.browser_cookie`（必需）+ `feishu.user_access_token`（自动刷新）
输出：`.ts` + `.mp3` + `_ori.srt` + `_ori.md`

---

### s1w_tencentmeeting.py — 腾讯会议

不单独运行，由 s1_huifang.py 调度（`source_type: "tencent_meeting"`）。

config.yaml 示例：
```yaml
tasks:
  - source_type: "tencent_meeting"
    source_url: "https://meeting.tencent.com/cw/lJayL90E87"
    wiki_url: ""
    title: ""
```

凭证：`tencent_meeting.cookie`（浏览器 cookie，可跨会议复用）
输出：`.mp4` + `.mp3` + `_ori.srt` + `_abs.md`（纪要含层次格式 + 时间轴，优先 API 获取）

---

### s1w_zhihu.py — 知乎训练营

```bash
python src/s1w_zhihu.py
```

仅处理 `source_type: "zhihu"` 的任务。

config.yaml 示例：
```yaml
tasks:
  - source_type: "zhihu"
    source_url: "https://www.zhihu.com/training-video/1234567890/9876543210"
    title: ""
```

凭证：`zhihu.browser_cookie`（浏览器 cookie）
输出：`.mp4` + `.mp3`

---

### s1w_xiaoe.py — 小鹅通

```bash
python src/s1w_xiaoe.py
```

仅处理 `source_type: "xiaoe"` 的任务。需要 Playwright。

config.yaml 示例：
```yaml
tasks:
  - source_type: "xiaoe"
    source_url: "https://appxxx.h5.xet.citv.cn/v4/course/alive/l_xxx"
    title: ""
```

凭证：`xiaoe.browser_cookie`（从浏览器 Cookies 面板复制完整字符串）
输出：`.mp4` + `.mp3`

---

### s1w_panda.py — 熊猫学院

```bash
python src/s1w_panda.py
```

仅处理 config.yaml 中 `source_type: "panda"` 的任务。

config.yaml 示例：
```yaml
tasks:
  - source_type: "panda"
    source_url: "https://fclive.pandacollege.cn/p/9nubG3"
    wiki_url: ""
    title: ""
```

凭证：`panda.token`（Bearer JWT，从浏览器 Network 面板获取）
输出：`.ts` + `.mp3`

---

### s1w_taobao.py — 淘宝直播

```bash
python src/s1w_taobao.py
```

仅处理 `source_type: "taobao"` 的任务。

config.yaml 示例：
```yaml
tasks:
  - source_type: "taobao"
    source_url: "https://81025.tbkflow.cn/pcLive/980e81dbbe100..."
    title: ""
```

凭证：`taobao.bearer_token` + `taobao.access_token` + `taobao.company_id`
输出：`.mp4` + `.mp3`

---

### s2_wiki.py — 教学文档下载

```bash
python src/s2_wiki.py
```

下载 task 中 `wiki_url` 指定的飞书 wiki 为 markdown；如配置了 `target_wiki_url` 则将处理结果写回飞书。
依赖：yitang 项目（`../yitang/src/yitang_wiki.py`）
输入：config.yaml 中的 `wiki_url`、`target_wiki_url`
输出：`output/{title}_wiki.md`

---

### s3_subtitle.py — 多引擎字幕生成

```bash
python src/s3_subtitle.py                          # pipeline 模式
python src/s3_subtitle.py audio.mp3 --whisper      # CLI 模式（Whisper）
python src/s3_subtitle.py audio.mp3 --xunfei       # CLI 模式（讯飞）
python src/s3_subtitle.py audio.mp3 --all          # CLI 模式（全部引擎）
```

支持 5 个引擎：Whisper（本地）、讯飞、飞书、阿里云、豆包。
Pipeline 模式下，当 s1 未产出 `_ori.srt` 时，用 Whisper 从 MP3 生成字幕。已有 `_ori.srt` 则跳过。
依赖：faster-whisper + opencc（无跨项目依赖）
输入：`output/{title}.mp3`
输出：`output/{title}_{engine_suffix}.srt`（如 `_wm.srt`、`_xunfei.srt`）
配置：`config.yaml` 中 `whisper.model`（默认 medium）、`whisper.force_cpu`、`engine_suffix`

---

### s4_srt_fix.py — LLM 字幕修订

```bash
python src/s4_srt_fix.py
```

用 LLM 对比教学文档修订字幕中的 ASR 错误和专业术语。已有 `_fix.srt` 则跳过。
依赖：无跨项目依赖（LLMClient 内联，OpenAI 兼容接口）
输入：`output/{title}_ori.srt`（优先）或 `_wm.srt` + `output/{title}_wiki.md`（可选）
输出：`output/{title}_ori_fix.srt` + `output/{title}_ori_fix_changelog.md`
配置：`config.yaml` 中 `srt_fix`（prompt、chunk_size、custom_dict）+ `llm` 段

---

### s5_addon.py — 信息拓展

```bash
python src/s5_addon.py                                                    # pipeline 模式
python src/s5_addon.py --subtitle xxx.srt --transcript xxx.md             # CLI 模式
python src/s5_addon.py --subtitle xxx.srt --transcript xxx.md --discussion xxx.xlsx --dry-run
```

从字幕和讨论区中挖掘逐字稿遗漏的信息，生成完整报告和精华摘要。已有 `_addon.md` 则跳过。
依赖：无跨项目依赖（LLMClient 内联，飞书逐字稿通过 url2md 获取）
输入：字幕（`_ori_fix.srt` > `_wm_fix.srt` > `_ori.srt` > `_wm.srt`）+ `_wiki.md`（可选）+ `_discussion.xlsx`（可选）
输出：`output/{title}_addon.md`（完整报告）+ `output/{title}_精华摘要.md`
配置：`config.yaml` 中 `addon`（chunk_size、提示词文件名）+ `llm` 段
CLI 参数：--dry-run / --provider / --subtitle-only / --discussion-only / --no-digest

---

### auth.py — 飞书 OAuth 授权

```bash
python src/auth.py
```

首次使用飞书妙记时运行。启动本地服务器（localhost:8080），自动打开浏览器完成 OAuth，token 写入 `cfg/credentials.yaml`。超时 300 秒。

---

### url2md.py — 飞书文档下载

```bash
python src/url2md.py "https://xxx.feishu.cn/wiki/xxx"           # 下载到 localscript/
python src/url2md.py "https://xxx.feishu.cn/wiki/xxx" -o out.md  # 指定输出路径
```

将飞书 wiki/docx/一堂文档 URL 转为本地 Markdown 文件。也供 s5_addon.py 的飞书逐字稿获取功能调用。
依赖：yitang 项目（`../yitang/src/yitang_wiki.py`）

---

### model_downloader.py — Whisper 模型管理

```bash
python src/model_downloader.py medium    # 下载 medium 模型
python src/model_downloader.py large     # 下载 large 模型
```

管理 faster-whisper 模型的下载和路径。供 s3_subtitle.py 自动调用。
