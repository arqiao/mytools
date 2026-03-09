# 变更记录

## 2026-03-09

### subtitle_from_mp3.py — CUDA 支持 & 日志修复
- CUDA cuBLAS DLL 路径注入：顶部修改 `os.environ["PATH"]` 加载 `nvidia-cublas-cu12` 的 DLL
- 本地模型加载：通过 `model_downloader.get_model_path()` 获取缓存路径，避免每次联网检查版本
- 日志修复：`model_downloader.py` 的 `logging.basicConfig()` 从模块级移入 `main()`，解决 import 时抢占 root logger 导致字幕日志写错文件的问题

### yitang_wiki.py — 标题编号 & bug 修复
- 标题编号范围：`_auto_heading_numbers` 中 `3 <= btype <= 9` 改为 `3 <= btype <= 11`，覆盖 heading8/heading9
- lambda 闭包修复：`_write_blocks_batched` 循环中 lambda 用默认参数绑定当前值，避免晚绑定
- 标题编号通用化：`heading_number` 处理移到 filter 之后的统一位置，飞书和一堂文档均支持

### 文档更新
- README.md：补充 CUDA 依赖、飞书数据源、标题编号配置、模型管理命令
- docs/PRD.md：新增标题编号章节、Whisper 本地转写详情、飞书数据源、日志文件说明

## 2026-03-08

### yitang_wiki.py — 容器 & 特殊类型支持
1. quote_container（type=34）：改为整体保留容器结构（同 callout），子节点保持原始类型
2. callout / quote_container 写入后删除自动生成的空段落（index 0）
3. 有序列表编号：style 复制增加 sequence 字段（"1" 首项 / "auto" 续编）
4. 容器内嵌套内容：新增 `_flatten_and_convert_children()` 递归展平子树再转换
5. `_write_column_content` 补全所有特殊类型（grid/table/image/callout/quote_container/nested_list）
6. 独立同步块（type=33, parent_type!=31）：跳过容器，展平子节点
7. file 块（type=23）：从 UNSUPPORTED_TYPES 移出，转为"📎 文件名"文字段落
