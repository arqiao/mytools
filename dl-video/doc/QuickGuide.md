# dl-video 快速使用指南

## 前置条件

```bash
pip install requests pyyaml cryptography playwright
playwright install chromium
```

确保 ffmpeg 在 PATH 中，且 `cfg/credentials.yaml` 已配置好对应平台的凭证。

## 各程序速查

### run_pipeline.py — 全流水线

```bash
python src/run_pipeline.py
```

自动执行 s1→s2→s3→s4→s5 全部五步，失败中断并飞书通知。
输入：`cfg/input.yaml`（tasks 列表）+ `cfg/config.yaml`（步骤参数）
输出：`output/` 或 `out-yitang/`（一堂任务自动切换）下的全部产出文件

---

### s1_huifang.py — 视频下载调度器

```bash
python src/s1_huifang.py
```

读取 input.yaml 中所有 task，按 `source_type` 分发到对应平台模块。
输入：`cfg/input.yaml` + `cfg/credentials.yaml`
输出：`output/{title}.ts` 或 `.mp4` + `.mp3`（飞书妙记额外产出 `_ori.srt`、`_ori.md`）

---

### s1w_yitang_video.py — 一堂

```bash
python src/s1w_yitang_video.py --video --audio --chat
```

由 s1_huifang.py 调度（`source_type: "yitang"`），也可单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "yitang"
    source_huifang_url: "https://air.yitang.top/live/xxx"
    source_wiki_url: "https://yitang.top/fs-doc/xxx"
    target_wiki_url: ""
    title: ""
    output_name: ""
```

凭证：`yitang.token` + `yitang.user_id`
输出：`out-yitang/{output_name}.ts` + `.mp3` + `.xlsx`（讨论区）

---

### s2w_yitang_wiki.py — 一堂文稿复制

```bash
python src/s2w_yitang_wiki.py [--dry-run]
```

由 s2_wiki.py 调度（`source_type: "yitang"`），也可单独运行。
将一堂文档 blocks 抓取、过滤、转换后写入飞书 wiki，同时本地导出 Markdown。
按文章生成独立日志（`wiki_{标题}_{时间戳}.log`）和警告错误日志（`err_{标题}_{时间戳}.log`）。

凭证：`yitang.token` + `feishu.user_access_token`
输出：`out-yitang/{title}.md` + 飞书 wiki 写入

---

### s1w_feishumiaoji.py — 飞书妙记

由 s1_huifang.py 调度（`source_type: "feishu_minutes"`），不单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "feishu_minutes"
    source_huifang_url: "https://waytoagi.feishu.cn/minutes/obcnxxx"
    source_wiki_url: "https://waytoagi.feishu.cn/wiki/xxx"
    title: ""
```

凭证：`feishu.browser_cookie`（必需）+ `feishu.user_access_token`（自动刷新）
输出：`output/{title}.ts` + `.mp3` + `_ori.srt` + `_ori.md`

---

### s1w_tencentmeeting.py — 腾讯会议

由 s1_huifang.py 调度（`source_type: "tencent_meeting"`），不单独运行。需要 Playwright。

input.yaml 示例：
```yaml
tasks:
  - source_type: "tencent_meeting"
    source_huifang_url: "https://meeting.tencent.com/cw/lJayL90E87"
    source_wiki_url: ""
    title: ""
```

凭证：`tencent_meeting.cookie`（浏览器 cookie，可跨会议复用）
输出：`output/{date_prefix}{title}.mp4` + `.mp3` + `_ori.srt` + `_abs.md`（纪要含层次格式 + 时间轴，优先 API 获取）

---

### s1w_zhihu.py — 知乎训练营

```bash
python src/s1w_zhihu.py
```

由 s1_huifang.py 调度（`source_type: "zhihu"`），也可单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "zhihu"
    source_huifang_url: "https://www.zhihu.com/training-video/1234567890/9876543210"
    source_wiki_url: ""
    title: ""
```

凭证：`zhihu.browser_cookie`（浏览器 cookie）
输出：`output/{date_prefix}{title}.mp4` + `.mp3`

---

### s1w_xiaoe.py — 小鹅通

```bash
python src/s1w_xiaoe.py
```

由 s1_huifang.py 调度（`source_type: "xiaoe"`），也可单独运行。需要 Playwright。

input.yaml 示例：
```yaml
tasks:
  - source_type: "xiaoe"
    source_huifang_url: "https://appxxx.h5.xet.citv.cn/v4/course/alive/l_xxx"
    source_wiki_url: ""
    title: ""
```

凭证：`xiaoe.browser_cookie`（从浏览器 Cookies 面板复制完整字符串）
输出：`output/{title}.mp4` + `.mp3`

---

### s1w_panda.py — 熊猫学院

```bash
python src/s1w_panda.py
```

由 s1_huifang.py 调度（`source_type: "panda"`），也可单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "panda"
    source_huifang_url: "https://fclive.pandacollege.cn/p/9nubG3"
    source_wiki_url: ""
    title: ""
```

凭证：`panda.token`（Bearer JWT，从浏览器 Network 面板获取）
输出：`output/{title}.ts` + `.mp3`

---

### s1w_taobao.py — 淘宝直播

```bash
python src/s1w_taobao.py
```

由 s1_huifang.py 调度（`source_type: "taobao"`），也可单独运行。

input.yaml 示例：
```yaml
tasks:
  - source_type: "taobao"
    source_huifang_url: "https://81025.tbkflow.cn/pcLive/980e81dbbe100..."
    source_wiki_url: ""
    title: ""
```

凭证：`taobao.bearer_token` + `taobao.access_token` + `taobao.company_id`
输出：`output/{title}.mp4` + `.mp3`

---

### s2_wiki.py — 教学文档下载

```bash
python src/s2_wiki.py
```

下载 task 中 `source_wiki_url` 指定的飞书 wiki 为 markdown；yitang 类型任务分发到 s2w_yitang_wiki.py；如配置了 `target_wiki_url` 则将处理结果写回飞书。
输入：input.yaml 中的 `source_wiki_url`、`target_wiki_url`
输出：`output/{title}_wiki.md`（普通任务）或 `out-yitang/{title}.md`（一堂任务）

---

### s3_subtitle.py — Whisper 字幕生成

```bash
python src/s3_subtitle.py
```

当 s1 未产出 `_ori.srt` 时，用 Whisper 从 MP3 生成字幕。已有 `_ori.srt` 则跳过。
输入：`output/{title}.mp3`
输出：`output/{title}_wm.srt`
配置：`config.yaml` 中 `s3_engine_suffix`

---

### s4_srt_fix.py — LLM 字幕修订

```bash
python src/s4_srt_fix.py
```

用 LLM 对比教学文档修订字幕中的 ASR 错误和专业术语。已有 `_fix.srt` 则跳过。
输入：`config.yaml` 中 `input.s4.subtitle`（优先）或 `_wm.srt` + `input.s4.transcript`（可选）
输出：`{stem}_fix.srt` + `{stem}_fix_changelog.md`（输出到输入文件同目录）
配置：`config.yaml` 中 `s4_fix`（prompt、chunk_size）、`llm_plan`

---

### s5_addon.py — 补充内容提取

```bash
python src/s5_addon.py
```

LLM 对比字幕与教学文档，提取"字幕中有但文档中没有"的补充信息。已有 `_addon.md` 则跳过。
输入：`config.yaml` 中 `input.s5`（subtitle、transcript、discussion）
输出：`{prefix}_信息拓展.md` + `{prefix}_精华摘要.md`（输出到输入文件同目录）
配置：`config.yaml` 中 `s5_analysis`（prompt、chunk_size）、`llm_plan`

---

### feishu_auth.py — 飞书 OAuth 授权

```bash
python src/modules/feishu_auth.py
```

首次使用飞书妙记时运行。启动本地服务器（localhost:8080），自动打开浏览器完成 OAuth，token 写入 `cfg/credentials.yaml`。超时 300 秒。
