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

### s1_feishumiaoji.py — 飞书妙记

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

### s1_panda.py — 熊猫学院

```bash
python src/s1_panda.py
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

### s1_xiaoe.py — 小鹅通

```bash
python src/s1_xiaoe.py
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

### s1_taobao.py — 淘宝直播

```bash
python src/s1_taobao.py
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
依赖：yitang 项目（`../yitang/src/url2md.py`）
输入：config.yaml 中的 `wiki_url`、`target_wiki_url`
输出：`output/{title}_wiki.md`

---

### s3_subtitle.py — Whisper 字幕生成

```bash
python src/s3_subtitle.py
```

当 s1 未产出 `_ori.srt` 时，用 Whisper 从 MP3 生成字幕。已有 `_ori.srt` 则跳过。
依赖：yitang 项目（`../yitang/src/subtitle_from_mp3.py`）+ openai-whisper
输入：`output/{title}.mp3`
输出：`output/{title}_wm.srt`
配置：`config.yaml` 中 `whisper.model`（默认 medium）、`whisper.force_cpu`

---

### s4_srt_fix.py — LLM 字幕修订

```bash
python src/s4_srt_fix.py
```

用 LLM 对比教学文档修订字幕中的 ASR 错误和专业术语。已有 `_fix.srt` 则跳过。
依赖：yitang 项目（`../yitang/src/yitang_srt_fix.py`）+ LLM API
输入：`output/{title}_ori.srt`（优先）或 `_wm.srt` + `output/{title}_wiki.md`（可选）
输出：`output/{title}_ori_fix.srt` + `output/{title}_ori_fix_changelog.md`
配置：`config.yaml` 中 `llm` 段（provider、model、max_tokens 等）

---

### s5_addon.py — 补充内容提取

```bash
python src/s5_addon.py
```

LLM 对比字幕与教学文档，提取"字幕中有但文档中没有"的补充信息。已有 `_addon.md` 则跳过。
依赖：yitang 项目（`../yitang/src/yitang_addon.py`）+ LLM API
输入：字幕（`_ori_fix.srt` > `_wm_fix.srt` > `_ori.srt` > `_wm.srt`）+ `_wiki.md`（可选）
输出：`output/{title}_addon.md`

---

### auth.py — 飞书 OAuth 授权

```bash
python src/auth.py
```

首次使用飞书妙记时运行。启动本地服务器（localhost:8080），自动打开浏览器完成 OAuth，token 写入 `cfg/credentials.yaml`。超时 300 秒。
