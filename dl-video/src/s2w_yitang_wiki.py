"""一堂文档 → 飞书文档 自动复制工具"""

import hashlib
import hmac
import json
import logging
import re
import time
from base64 import b64decode, b64encode
from pathlib import Path

import requests
import yaml
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from modules.config_utils import load_config

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent  # 项目根目录（src/ 的上级）
CFG_DIR = PROJECT_DIR / "cfg"
_input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# 一堂签名常量
G_KEY = b"BDFHJLNPRTVXZ\\^`"  # 16 bytes
M_KEY = b"ether7sv6te7sv6he7sv6there7sv6r0"  # 32 bytes

# 飞书 API
from modules.feishu_token import (
    FEISHU_BASE,
    ensure_feishu_token as _ensure_token,
    feishu_headers as _feishu_hdrs,
    resolve_wiki_token as _resolve_wiki,
)

# 不可复制的 block 类型
UNSUPPORTED_TYPES = {
    40: "add_ons/倒计时",
}

# 容器类 block（有 children 但自身无文本，需要递归处理子节点）
CONTAINER_TYPES = {
    19: "callout",       # 高亮块
    34: "quote_container",  # 引用容器
}

# heading 类型集合 (heading1~heading9 对应 type 3~11)
HEADING_TYPES = set(range(3, 12))


class YitangCopier:
    def __init__(self):
        config, creds = load_config()
        self.config = config
        self.creds = creds
        self.session = requests.Session()
        self.skipped_blocks = []  # 记录无法复制的 blocks
        self._quota_exhausted = False  # 额度耗尽标志
        self.full_copy_titles = [self.config["s1_yitang_ailive"]["query_copystr"]]  # 需要全文复制的标题关键词

    # ── 一堂认证 ──────────────────────────────────────────

    def _aes_encrypt(self, plaintext: bytes, key: bytes, iv: bytes) -> str:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        padder = PKCS7(128).padder()
        padded = padder.update(plaintext) + padder.finalize()
        ct = encryptor.update(padded) + encryptor.finalize()
        return b64encode(ct).decode()

    def _aes_decrypt(self, ciphertext: str, key: bytes, iv: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        ct = b64decode(ciphertext)
        padded = decryptor.update(ct) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()

    def _generate_x_token_1(self) -> str:
        token = self.creds["yitang"]["token"]
        ts = str(int(time.time()))
        plaintext = f"{G_KEY.decode()}~{token}~{ts}".encode()
        # CryptoJS.enc.Utf8.parse(CryptoJS.MD5(g)) → MD5 hex 字符串的 UTF8 编码 = 32 bytes
        key = hashlib.md5(G_KEY).hexdigest().encode()
        return self._aes_encrypt(plaintext, key, G_KEY)

    def _generate_x_token_2(self, uri: str, params: dict) -> str:
        sorted_qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
        message = f"{uri}?{sorted_qs}"
        sig = hmac.new(M_KEY, message.encode(), hashlib.sha1).hexdigest()
        return sig

    # ── URL 判断 ──────────────────────────────────────────

    @staticmethod
    def _is_feishu_url(url: str) -> bool:
        """检测 URL 是否为飞书原始文档链接（非一堂链接）"""
        return "feishu.cn/docx/" in url or "feishu.cn/wiki/" in url

    def _is_feishu_wiki_url(self, url: str) -> bool:
        """检测 URL 是否为飞书 wiki 链接"""
        return "feishu.cn/wiki/" in url

    # ── 一堂 API ─────────────────────────────────────────

    def _decrypt_response(self, encrypted: str) -> dict:
        token = self.creds["yitang"]["token"]
        key = hashlib.md5(token.encode()).hexdigest().encode()  # 32 bytes
        iv = token.encode()  # 16 bytes
        decrypted = self._aes_decrypt(encrypted, key, iv)
        return json.loads(decrypted)

    def _parse_source_url(self, url: str) -> tuple:
        """从 source_url 解析 acl 和 doc_token（忽略查询参数）"""
        # 先去掉查询参数
        base_url = url.split("?")[0]
        parts = base_url.rstrip("/").split("/")
        return parts[-2], parts[-1]  # acl, doc_token

    def _detect_url_type(self, url: str) -> str:
        """根据 URL 路径判断类型"""
        if "/fs-doc/" in url:
            return "fs-doc"
        return ""

    def _extract_url_param(self, url: str, param: str) -> str:
        """从 URL 查询参数中提取值"""
        import urllib.parse
        try:
            parsed = urllib.parse.urlparse(url)
            queries = urllib.parse.parse_qs(parsed.query)
            val = queries.get(param, [])
            return val[0] if val else ""
        except Exception:
            return ""

    def fetch_source_blocks(self, source_url: str) -> dict:
        acl, doc_token = self._parse_source_url(source_url)
        url_type = self._detect_url_type(source_url)
        if not url_type:
            log.warning(f"未识别的一堂 URL 类型: {source_url}")
        uri = "/api/feishu/get-doc-blocks"
        # 从 URL 查询参数中提取 fromAcl 和 utm_content，兜底默认值
        from_acl = self._extract_url_param(source_url, "fromAcl") or "token"
        utm_media = "web"
        utm_content = self._extract_url_param(source_url, "utm_content") or (
            self.creds["yitang"]["cookie"].split("utm_content=")[-1].split(";")[0].strip()
            if "utm_content=" in self.creds["yitang"]["cookie"] else ""
        )
        params = {
            "doc": doc_token,
            "mode": "0",
            "fromAcl": from_acl,
            "acl": acl,
            "utm_media": utm_media,
            "utm_content": utm_content,
            "utm_source": "",
            "utm_medium": "",
            "utm_activity": "yitang",
            "_uds": "",
        }
        headers = {
            "Cookie": self.creds["yitang"]["cookie"],
            "x-token-1": self._generate_x_token_1(),
            "x-token-2": self._generate_x_token_2(uri, params),
            "x-ctxid": self.creds["yitang"]["request_id"],
            "x-request-by": "fe-01075ae",
            "Referer": source_url,
        }
        url = f"https://yitang.top{uri}"
        resp = self.session.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"一堂 API 错误: {data}")
        encrypted = data["data"]
        return self._decrypt_response(encrypted)

    # ── 飞书原始文档读取 ──────────────────────────────────

    def fetch_feishu_blocks(self, source_url: str) -> dict:
        """从飞书 docx API 读取文档 blocks，转换为一堂格式的树结构"""
        # 提取 doc_token
        doc_token = source_url.split("?")[0].rstrip("/").split("/")[-1]
        self._ensure_feishu_token()

        # 分页获取所有 blocks
        all_blocks = []
        page_token = None
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            url = f"{FEISHU_BASE}/docx/v1/documents/{doc_token}/blocks"
            resp = self.session.get(
                url, params=params,
                headers=self._feishu_headers(), timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"飞书 docx API 错误: {data}")
            items = data.get("data", {}).get("items", [])
            all_blocks.extend(items)
            if not data["data"].get("has_more"):
                break
            page_token = data["data"].get("page_token")

        log.info(f"飞书文档 {doc_token}: 共 {len(all_blocks)} 个 blocks")

        # 构建 block_id → block 索引
        index = {b["block_id"]: b for b in all_blocks}

        # 找到根节点（block_type=1, parent_id 为空或等于自身）
        root = None
        for b in all_blocks:
            if b.get("block_type") == 1:
                root = b
                break
        if not root:
            raise RuntimeError("飞书文档缺少 Page 根节点")

        # 递归组装树
        root_tree = self._feishu_block_to_tree(root, index)
        return {"blocks": root_tree}

    def fetch_feishu_wiki_blocks(self, source_url: str) -> dict:
        """从飞书 wiki URL 读取文档 blocks（先解析 wiki token）"""
        wiki_token = source_url.split("?")[0].rstrip("/").split("/")[-1]
        doc_token = self.resolve_wiki_token(source_url)
        log.info(f"飞书 wiki {wiki_token} -> document_id {doc_token}")

        self._ensure_feishu_token()

        # 分页获取所有 blocks
        all_blocks = []
        page_token = None
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            url = f"{FEISHU_BASE}/docx/v1/documents/{doc_token}/blocks"
            resp = self.session.get(
                url, params=params,
                headers=self._feishu_headers(), timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"飞书 docx API 错误: {data}")
            items = data.get("data", {}).get("items", [])
            all_blocks.extend(items)
            if not data["data"].get("has_more"):
                break
            page_token = data["data"].get("page_token")

        log.info(f"飞书文档 {doc_token}: 共 {len(all_blocks)} 个 blocks")

        # 构建 block_id → block 索引
        index = {b["block_id"]: b for b in all_blocks}

        # 找到根节点
        root = None
        for b in all_blocks:
            if b.get("block_type") == 1:
                root = b
                break
        if not root:
            raise RuntimeError("飞书文档缺少 Page 根节点")

        root_tree = self._feishu_block_to_tree(root, index)
        return {"blocks": root_tree}

    def _feishu_block_to_tree(self, block: dict, index: dict) -> dict:
        """将飞书 API 的 block 递归转换为一堂格式"""
        btype = block.get("block_type", 0)
        node = {"type": btype, "blockId": block.get("block_id", "")}

        # 构建 blockAttr：把飞书 block 的直接属性包裹进 blockAttr
        block_attr = {}
        attr_keys = self._feishu_attr_key(btype)
        for key in attr_keys:
            if key in block:
                block_attr[key] = block[key]

        # 图片特殊处理：token → feishu://token/{file_token}
        if btype == 27 and "image" in block:
            img = block["image"]
            token = img.get("token", "")
            if token:
                block_attr["cdnUrl"] = f"feishu://token/{token}"
            block_attr["image"] = img

        # sheet 嵌入表格：读取内容转为 table block
        if btype == 30 and "sheet" in block:
            sheet_token = block["sheet"].get("token", "")
            if sheet_token:
                table_node = self._fetch_sheet_as_table(sheet_token)
                if table_node:
                    return table_node

        node["blockAttr"] = block_attr

        # 递归组装子节点
        children_ids = block.get("children", [])
        if children_ids:
            childrens = []
            for cid in children_ids:
                child_block = index.get(cid)
                if child_block:
                    childrens.append(
                        self._feishu_block_to_tree(child_block, index)
                    )
            node["childrens"] = childrens
        else:
            node["childrens"] = []

        return node

    @staticmethod
    def _feishu_attr_key(block_type: int) -> list:
        """根据 block_type 返回飞书 block 中需要提取到 blockAttr 的属性名"""
        mapping = {
            1: ["page"], 2: ["text"], 3: ["heading1"], 4: ["heading2"],
            5: ["heading3"], 6: ["heading4"], 7: ["heading5"],
            8: ["heading6"], 9: ["heading7"], 10: ["heading8"],
            11: ["heading9"], 12: ["bullet"], 13: ["ordered"],
            14: ["code"], 15: ["quote"], 19: ["callout"],
            22: ["divider"], 23: ["file"], 24: ["grid"], 30: ["sheet"],
            25: ["grid_column"], 27: ["image"],
            31: ["table"], 32: ["table_cell"], 33: ["merge_cell"],
            34: ["quote_container"],
        }
        return mapping.get(block_type, [])

    def _sheet_val_to_blocks(self, val) -> list:
        """将 sheet 单元格值转换为一堂格式的 block 列表"""
        if not val:
            return [self._make_text_block("")]
        # 单个 embed-image
        if isinstance(val, dict) and val.get("type") == "embed-image":
            return [self._make_image_block(val)]
        # 单个 dict（mention 等）
        if isinstance(val, dict):
            text = val.get("text") or val.get("name") or str(val)
            return [self._make_text_block(text)]
        # list：混合内容，分离文本和图片
        if isinstance(val, list):
            blocks = []
            elements_buf = []
            for item in val:
                if isinstance(item, dict) and item.get("type") == "embed-image":
                    if elements_buf:
                        blocks.append(self._make_rich_text_block(elements_buf))
                        elements_buf = []
                    blocks.append(self._make_image_block(item))
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("name") or str(item)
                    el = {"textRun": {"content": text}}
                    style = self._convert_segment_style(item.get("segmentStyle"))
                    if style:
                        el["textRun"]["textElementStyle"] = style
                    elements_buf.append(el)
                else:
                    elements_buf.append({"textRun": {"content": str(item) if item else ""}})
            if elements_buf:
                blocks.append(self._make_rich_text_block(elements_buf))
            return blocks or [self._make_text_block("")]
        # 普通标量
        return [self._make_text_block(str(val))]

    @staticmethod
    def _convert_segment_style(seg_style: dict) -> dict:
        """将 sheet segmentStyle 转为飞书 text_element_style"""
        if not seg_style:
            return {}
        style = {}
        if seg_style.get("bold"):
            style["bold"] = True
        if seg_style.get("italic"):
            style["italic"] = True
        if seg_style.get("strikeThrough"):
            style["strikethrough"] = True
        if seg_style.get("underline"):
            style["underline"] = True
        return style

    @staticmethod
    def _make_rich_text_block(elements: list) -> dict:
        return {
            "type": 2,
            "blockAttr": {"text": {"elements": elements}},
            "childrens": [],
        }

    @staticmethod
    def _make_text_block(text: str) -> dict:
        return {
            "type": 2,
            "blockAttr": {"text": {"elements": [
                {"textRun": {"content": text}}
            ]}},
            "childrens": [],
        }

    @staticmethod
    def _make_image_block(val: dict) -> dict:
        return {
            "type": 27,
            "blockAttr": {
                "cdnUrl": val.get("link", ""),
                "image": {
                    "token": val.get("fileToken", ""),
                    "width": val.get("width", 0),
                    "height": val.get("height", 0),
                },
            },
            "childrens": [],
        }

    def _fetch_sheet_as_table(self, sheet_token: str) -> dict:
        """读取飞书嵌入表格内容，转换为一堂格式的 table block"""
        self._ensure_feishu_token()
        # token 格式: {spreadsheet_token}_{sheet_id}
        parts = sheet_token.rsplit("_", 1)
        spreadsheet_token = parts[0]
        sheet_id = parts[1] if len(parts) > 1 else ""

        # 读取 sheet 元信息获取行列数
        meta_url = (f"{FEISHU_BASE}/sheets/v3/spreadsheets"
                    f"/{spreadsheet_token}/sheets/query")
        meta_resp = self.session.get(
            meta_url, headers=self._feishu_headers(), timeout=15,
        )
        meta_data = meta_resp.json()
        if meta_data.get("code") != 0:
            self._warn(f"sheet 元信息获取失败: {meta_data}")
            return {}
        sheet_info = None
        for s in meta_data["data"].get("sheets", []):
            if s.get("sheet_id") == sheet_id:
                sheet_info = s
                break
        if not sheet_info:
            self._warn(f"sheet_id {sheet_id} 未找到")
            return {}

        grid = sheet_info.get("grid_properties", {})
        row_count = grid.get("row_count", 0)
        col_count = grid.get("column_count", 0)

        # 读取单元格数据
        val_url = (f"{FEISHU_BASE}/sheets/v2/spreadsheets"
                   f"/{spreadsheet_token}/values/{sheet_id}")
        val_resp = self.session.get(
            val_url, headers=self._feishu_headers(), timeout=15,
        )
        val_data = val_resp.json()
        if val_data.get("code") != 0:
            self._warn(f"sheet 数据读取失败: {val_data}")
            return {}
        rows = val_data["data"]["valueRange"].get("values", [])

        # 去除尾部空行
        while rows and all(not cell for cell in rows[-1]):
            rows.pop()
        actual_rows = len(rows)
        if not actual_rows:
            return {}

        # 构建一堂格式的 table block
        # 每个 cell 可包含多个子节点（文本 + 图片混合）
        childrens = []
        for ri, row in enumerate(rows):
            for ci in range(col_count):
                val = row[ci] if ci < len(row) else ""
                cell_children = self._sheet_val_to_blocks(val)
                cell_block = {
                    "type": 32, "blockAttr": {},
                    "childrens": cell_children,
                }
                childrens.append(cell_block)

        table_block = {
            "type": 31,
            "blockAttr": {"table": {"property": {
                "row_size": actual_rows,
                "column_size": col_count,
            }}},
            "childrens": childrens,
        }
        log.info(f"sheet → table: {actual_rows}x{col_count}")
        return table_block

    # ── 内容过滤 ──────────────────────────────────────────

    # block 类型中文名（用于日志可读性）
    _BLOCK_TYPE_NAMES = {
        1: "页面", 2: "文本", 3: "标题1", 4: "标题2", 5: "标题3",
        6: "标题4", 7: "标题5", 8: "标题6", 9: "标题7", 10: "标题8",
        11: "标题9", 12: "有序列表", 13: "无序列表", 14: "代码块",
        15: "引用", 17: "待办", 19: "高亮块", 22: "分割线",
        23: "文件", 24: "网格", 25: "网格列", 27: "图片",
        28: "iframe/嵌入", 30: "表格(sheet)", 31: "表格", 32: "表格单元格",
        33: "合并单元格", 34: "引用容器", 40: "倒计时/插件",
    }

    def _get_block_desc(self, block: dict) -> str:
        """获取 block 的可读描述：优先文本内容，无文本则返回类型名"""
        text = self._get_block_text(block)
        if text:
            return text
        btype = block.get("type", 0)
        return f"[{self._BLOCK_TYPE_NAMES.get(btype, f'type={btype}')}]"

    def _get_block_text(self, block: dict) -> str:
        """提取 block 的纯文本内容"""
        attr = block.get("blockAttr", {})
        for key in ("heading1", "heading2", "heading3", "heading4", "heading5",
                     "heading6", "heading7", "heading8", "heading9", "text",
                     "ordered", "bullet", "code", "quote"):
            node = attr.get(key)
            if node and "elements" in node:
                parts = []
                for el in node["elements"]:
                    tr = el.get("textRun") or el.get("text_run")
                    if tr:
                        parts.append(tr.get("content", ""))
                return "".join(parts)
        return ""

    def _flatten_blocks(self, block: dict, parent_type: int = 0) -> list:
        """递归展平 blocks 树为一维列表（跳过 Page 根节点 type=1）"""
        result = []
        btype = block.get("type", 0)
        if btype != 1:
            # 独立 merge_cell（不在 table 内）：跳过容器，只展平子节点
            if btype == 33 and parent_type != 31:
                for child in block.get("childrens", []):
                    result.extend(self._flatten_blocks(child, btype))
                return result
            # 容器类型不加入结果，只展平子节点
            if btype not in CONTAINER_TYPES:
                if btype == 31:
                    # table: 保留整体，子节点由 _convert_table 处理
                    result.append(block)
                    return result
                if btype == 24:
                    # grid: 保留整体，子节点由 _convert_grid 处理
                    result.append(block)
                    return result
                if btype in (12, 13) and self._has_list_children(block):
                    # 列表项有子列表：保留嵌套结构，不展平
                    result.append(block)
                    return result
                result.append(block)
            elif btype == 19:
                # callout: 保留容器本身，子节点内嵌处理
                result.append(block)
                return result  # 不展平子节点，convert_block 会处理
            elif btype == 34:
                # quote_container: 同 callout，保留容器整体
                result.append(block)
                return result
        for child in block.get("childrens", []):
            result.extend(self._flatten_blocks(child, btype))
        return result

    def _auto_heading_numbers(self, blocks: list, heading_cfg: dict) -> list:
        """为 heading blocks 添加编号。

        heading_cfg:
          start_heading: 从哪个标题开始编号（精确匹配）
          end_heading:   到哪个标题结束编号（该标题本身会编号），留空则到末尾
        """
        # 收集 heading 信息: (blocks索引, level, 纯文本)
        headings = []
        for i, b in enumerate(blocks):
            btype = b.get("type", 0)
            if 3 <= btype <= 11:
                level = btype - 2
                attr = b.get("blockAttr", {})
                key = f"heading{level}"
                elements = attr.get(key, {}).get("elements", [])
                text = "".join(
                    (e.get("text_run") or e.get("textRun", {}))
                    .get("content", "")
                    for e in elements
                ).strip()
                headings.append((i, level, text))

        if len(headings) < 2:
            return blocks

        start_text = heading_cfg.get("start_heading", "")
        end_text = heading_cfg.get("end_heading", "")

        start_idx = 0 if not start_text else None
        end_idx = len(headings) - 1

        for j, (_, _, text) in enumerate(headings):
            if start_idx is None and text == start_text:
                start_idx = j
            if end_text and text == end_text:
                end_idx = j

        if start_idx is None:
            return blocks

        top_level = headings[start_idx][1]
        counters = [0] * 10  # heading1~heading9 最多 9 级，rel 最大 8
        for j in range(start_idx, end_idx + 1):
            block_idx, level, _ = headings[j]
            b = blocks[block_idx]
            attr = b.get("blockAttr", {})
            key = f"heading{level}"
            elements = attr.get(key, {}).get("elements", [])
            if not elements:
                continue

            rel = level - top_level
            if rel < 0:
                rel = 0
            counters[rel] += 1
            for r in range(rel + 1, 10):
                counters[r] = 0
            parts = [str(counters[r]) for r in range(rel + 1)]
            number = ".".join(parts)

            first = elements[0]
            tr = first.get("text_run") or first.get("textRun") or {}
            if "content" in tr:
                tr["content"] = f"{number} {tr['content'].lstrip()}"

        return blocks

    @staticmethod
    def _has_list_children(block: dict) -> bool:
        """检查列表项是否有子列表"""
        for child in block.get("childrens", []):
            if child.get("type") in (12, 13):
                return True
        return False

    def filter_blocks(self, blocks_data: dict, doc_title: str = "") -> list:
        # 检查是否需要全文复制（标题匹配关键词时）
        if doc_title and self.full_copy_titles:
            for kw in self.full_copy_titles:
                if kw in doc_title:
                    root = blocks_data.get("blocks", blocks_data.get("block", blocks_data))
                    return self._flatten_blocks(root)

        start_kw = self.config.get("s1_yitang_wikicopy", {})["start_heading"]
        end_kw = self.config.get("s1_yitang_wikicopy", {})["end_heading"]
        root = blocks_data.get("blocks", blocks_data.get("block", blocks_data))
        flat = self._flatten_blocks(root)

        collecting = False
        end_found = False
        result = []
        for b in flat:
            btype = b.get("type", 0)
            text = self._get_block_text(b)
            if not collecting:
                if btype in HEADING_TYPES and start_kw in text:
                    collecting = True
                    result.append(b)
                continue
            result.append(b)
            if not end_found and btype in HEADING_TYPES and end_kw in text:
                end_found = True
                continue
            if end_found and btype in HEADING_TYPES:
                result.pop()  # 移除下一章节的标题
                break

        # start_heading 找不到时 fallback 到全文复制
        if not result:
            self._warn(f"  未找到 start_heading '{start_kw}'，fallback 到全文复制")
            return flat
        return result

    # ── Block 转换 ────────────────────────────────────────

    def _convert_elements(self, elements: list) -> list:
        """转换 text elements 为飞书写入格式"""
        result = []
        for el in elements:
            tr = el.get("textRun") or el.get("text_run")
            if tr:
                new_el = {"text_run": {
                    "content": tr.get("content", ""),
                }}
                style = tr.get("textElementStyle") or tr.get("text_element_style")
                if style:
                    new_style = {}
                    for k in ("bold", "italic", "strikethrough", "underline",
                              "inline_code", "link",
                              "text_color", "background_color"):
                        if k in style and style[k]:
                            new_style[k] = style[k]
                    # 处理驼峰转下划线的字段
                    for camel, snake in [("inlineCode", "inline_code"),
                                         ("textColor", "text_color"),
                                         ("backgroundColor", "background_color")]:
                        if camel in style and snake not in new_style and style[camel]:
                            new_style[snake] = style[camel]
                    if new_style:
                        new_el["text_run"]["text_element_style"] = new_style
                result.append(new_el)
            # mention 系列：提取显示文本，用 text_run 透传
            elif "mention" in el or "equation" in el:
                display = ""
                if "mention" in el:
                    m = el["mention"]
                    display = m.get("name") or m.get("text") or ""
                elif "equation" in el:
                    display = el["equation"].get("content", "")
                result.append({"text_run": {"content": display}})
            # 其他非 text_run 元素：整块透传
            elif el:
                result.append(el)
        return result

    def convert_block(self, block: dict, doc_id: str = "") -> dict | None:
        btype = block.get("type", 0)
        attr = block.get("blockAttr", {})

        # 不支持的类型
        if btype in UNSUPPORTED_TYPES:
            self._record_skipped(block, UNSUPPORTED_TYPES[btype])
            return None

        # file/视频 → 无法直接创建，用文字段落替代显示文件名
        if btype == 23:
            return self._convert_file_block(block)

        # callout → 创建 callout block，子节点递归转换后内嵌
        if btype == 19:
            return self._convert_callout(block, doc_id)

        # quote_container → 同 callout，保留容器，子节点递归转换
        if btype == 34:
            return self._convert_quote_container(block, doc_id)

        # grid → 多列布局，分步写入
        if btype == 24:
            return self._convert_grid(block, doc_id)
        if btype == 25:
            return None  # grid_column 由 _convert_grid 内部处理

        # 其余容器类型 → 跳过容器本身
        if btype in CONTAINER_TYPES:
            return None

        # 表格 → 多步写入
        if btype == 31:
            return self._convert_table(block, doc_id)
        if btype in (32, 33):
            return None  # table_cell/merge_cell 由 _convert_table 内部处理

        # 嵌套列表 → 分步写入（父列表项 + 子列表项）
        if btype in (12, 13) and self._has_list_children(block):
            return self._convert_nested_list(block, doc_id)

        # 图片
        if btype == 27:
            return self.handle_image(block, doc_id)

        # 分割线
        if btype == 22:
            return {"block_type": 22, "divider": {}}

        # 文本类 block 的类型映射
        type_map = {
            2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
            6: "heading4", 7: "heading5", 8: "heading6", 9: "heading7",
            10: "heading8", 11: "heading9",
            12: "bullet", 13: "ordered", 14: "code", 15: "quote",
        }
        block_key = type_map.get(btype)
        if not block_key:
            self._record_skipped(block, f"未知类型 type={btype}")
            return None

        src = attr.get(block_key, {})
        elements = src.get("elements", [])
        if not elements:
            for k in type_map.values():
                if k in attr and attr[k].get("elements"):
                    src = attr[k]
                    elements = src["elements"]
                    break

        converted_els = self._convert_elements(elements)
        if not converted_els:
            return None

        out_key = block_key
        out_type = self._feishu_block_type(btype)

        result = {
            "block_type": out_type,
            out_key: {"elements": converted_els},
        }
        # 段落级样式：对齐方式、代码语言等
        if "style" in src:
            block_style = {}
            for k in ("align", "language", "wrap", "sequence"):
                if k in src["style"]:
                    block_style[k] = src["style"][k]
            if block_style:
                result[out_key]["style"] = block_style
        return result

    @staticmethod
    def _feishu_block_type(src_type: int) -> int:
        """一堂 type → 飞书 API block_type 编号"""
        return src_type

    def _flatten_and_convert_children(self, block: dict, doc_id: str) -> list:
        """展平容器的子节点树，再逐个转换为飞书 block。
        容器（callout/quote_container）的子节点可能还有自己的子节点，
        需要先展平为一维列表，再逐个调用 convert_block。"""
        flat = []
        for child in block.get("childrens", []):
            flat.extend(self._flatten_blocks(child))
        result = []
        for fb in flat:
            converted = self.convert_block(fb, doc_id)
            if converted:
                result.append(converted)
        return result

    def _convert_callout(self, block: dict, doc_id: str) -> dict | None:
        """转换 callout 容器：返回特殊结构，append_to_feishu 会分两步写入"""
        attr = block.get("blockAttr", {})
        co = attr.get("callout", {})
        callout_def = {
            "block_type": 19,
            "callout": {},
        }
        for k in ("background_color", "border_color", "emoji_id"):
            if k in co:
                callout_def["callout"][k] = co[k]

        children = self._flatten_and_convert_children(block, doc_id)
        return {"_callout": True, "container": callout_def, "children": children}

    def _convert_quote_container(self, block: dict, doc_id: str) -> dict | None:
        """转换 quote_container 容器：返回特殊结构，写入时先建容器再追加子节点"""
        container_def = {"block_type": 34, "quote_container": {}}
        children = self._flatten_and_convert_children(block, doc_id)
        return {"_quote_container": True, "container": container_def, "children": children}

    def _convert_file_block(self, block: dict) -> dict | None:
        """file block 无法直接创建，用文字段落显示文件名"""
        attr = block.get("blockAttr", {})
        file_info = attr.get("file", {})
        name = file_info.get("name", "")
        if not name:
            self._record_skipped(block, "file block 无文件名")
            return None
        return {
            "block_type": 2,
            "text": {
                "elements": [{"text_run": {
                    "content": f"\U0001F4CE {name}",
                    "text_element_style": {},
                }}],
            },
        }

    def _convert_nested_list(self, block: dict, doc_id: str) -> dict | None:
        """转换有子列表的列表项：返回特殊结构，append_to_feishu 分步写入"""
        # 先转换父列表项自身（不含子列表）
        parent_copy = dict(block)
        parent_copy["childrens"] = []  # 清空子节点，避免递归
        parent_block = self.convert_block(parent_copy, doc_id)
        if not parent_block:
            return None

        # 递归转换子节点
        children = []
        for child in block.get("childrens", []):
            converted = self.convert_block(child, doc_id)
            if converted:
                children.append(converted)

        if not children:
            return parent_block  # 没有有效子节点，当普通 block 处理

        return {"_nested_list": True, "parent": parent_block, "children": children}

    def _convert_grid(self, block: dict, doc_id: str) -> dict | None:
        """转换 grid 多列布局：返回特殊结构，append_to_feishu 分步写入"""
        attr = block.get("blockAttr", {})
        grid_prop = attr.get("grid", {})
        column_size = grid_prop.get("column_size", 0)
        if not column_size:
            self._record_skipped(block, "grid 缺少 column_size")
            return None

        # 收集每列的宽度比例和内容
        columns = []
        for col_block in block.get("childrens", []):
            if col_block.get("type") != 25:
                continue
            col_attr = col_block.get("blockAttr", {})
            width_ratio = col_attr.get("grid_column", {}).get("width_ratio")
            col_children = []
            for child in col_block.get("childrens", []):
                converted = self.convert_block(child, doc_id)
                if converted:
                    col_children.append(converted)
            columns.append({"width_ratio": width_ratio, "children": col_children})

        return {
            "_grid": True,
            "column_size": column_size,
            "columns": columns,
        }

    def _convert_table(self, block: dict, doc_id: str) -> dict | None:
        """转换 table：返回特殊结构，append_to_feishu 分步写入"""
        attr = block.get("blockAttr", {})
        table_prop = attr.get("table", {}).get("property", {})
        row_size = table_prop.get("row_size", 0)
        col_size = table_prop.get("column_size", 0)
        if not row_size or not col_size:
            self._record_skipped(block, "table 缺少 row/column size")
            return None

        # 构建空表格定义
        table_def = {
            "block_type": 31,
            "table": {
                "property": {
                    "row_size": row_size,
                    "column_size": col_size,
                },
            },
        }
        col_width = table_prop.get("column_width")
        if col_width:
            table_def["table"]["property"]["column_width"] = col_width

        # 收集每个 cell 的内容（按行主序）
        cells_content = []
        for cell_block in block.get("childrens", []):
            if cell_block.get("type") != 32:
                continue
            cell_children = []
            for child in cell_block.get("childrens", []):
                # sheet cell 内的 embed-image：type 是字符串 'embed-image'，不是数字 block type
                if child.get("type") == "embed-image":
                    img_block = {
                        "type": 27,  # 伪装成普通图片 block
                        "blockAttr": {
                            "cdnUrl": child.get("link", ""),
                            "image": {
                                "token": child.get("fileToken", ""),
                                "width": child.get("width", 0),
                                "height": child.get("height", 0),
                            },
                        },
                    }
                    converted = self.handle_image(img_block, doc_id)
                    if converted:
                        cell_children.append(converted)
                    continue
                converted = self.convert_block(child, doc_id)
                if converted:
                    cell_children.append(converted)
            cells_content.append(cell_children)

        # 收集合并信息
        merge_info = table_prop.get("merge_info", [])

        return {
            "_table": True,
            "table_def": table_def,
            "cells_content": cells_content,
            "merge_info": merge_info,
        }

    def handle_image(self, block: dict, doc_id: str) -> dict | None:
        """返回特殊 _image 结构，由 append_to_feishu 三步写入"""
        attr = block.get("blockAttr", {})
        img = attr.get("image", {})
        cdn_url = attr.get("cdnUrl", "") or img.get("cdnUrl", "")

        if not cdn_url:
            self._record_skipped(block, "图片无 CDN URL")
            return None

        return {"_image": True, "cdn_url": cdn_url, "doc_id": doc_id}

    def _download_image(self, cdn_url: str, doc_id: str) -> Path | None:
        """下载图片到临时目录，返回本地路径"""
        try:
            if cdn_url.startswith("feishu://token/"):
                # 飞书文档内图片：通过内部 API + 浏览器 cookie 下载
                file_token = cdn_url.replace("feishu://token/", "")
                dl_url = (
                    f"https://internal-api-drive-stream.feishu.cn"
                    f"/space/api/box/stream/download/preview/{file_token}/"
                    f"?preview_type=16"
                )
                browser_cookie = self.creds["feishu"].get("browser_cookie", "")
                resp = self.session.get(
                    dl_url, headers={
                        "Cookie": browser_cookie,
                        "Origin": "https://yitanger.feishu.cn",
                        "Referer": "https://yitanger.feishu.cn/",
                        "sec-fetch-dest": "empty",
                        "sec-fetch-mode": "cors",
                        "sec-fetch-site": "same-site",
                    }, timeout=30,
                )
            elif "feishu.cn/" in cdn_url:
                # 飞书域名下的图片（sheet_image 等）：同样需要 browser_cookie
                browser_cookie = self.creds["feishu"].get("browser_cookie", "")
                resp = self.session.get(
                    cdn_url, headers={
                        "Cookie": browser_cookie,
                        "Origin": "https://yitanger.feishu.cn",
                        "Referer": "https://yitanger.feishu.cn/",
                    }, timeout=30,
                )
            else:
                resp = self.session.get(cdn_url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            self._warn(f"图片下载失败: {cdn_url} - {e}")
            return None
        content_type = resp.headers.get("Content-Type", "image/png")
        ext = content_type.split("/")[-1].split(";")[0]
        filename = f"image_{int(time.time() * 1000)}.{ext}"
        temp_dir = PROJECT_DIR / "temp_images" / doc_id[:8]
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / filename
        temp_path.write_bytes(resp.content)
        return temp_path

    def _write_image(self, doc_id: str, parent_id: str, image_data: dict):
        """三步写入图片: 创建空 block → 上传到 block → replace_image"""
        cdn_url = image_data["cdn_url"]

        # 1. 下载图片
        temp_path = self._download_image(cdn_url, doc_id)
        if not temp_path:
            return

        file_size = temp_path.stat().st_size
        filename = temp_path.name
        log.info(f"图片已下载: {temp_path} ({file_size} bytes)")

        # 2. 创建空 image block（带 429 重试）
        url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children"
        body = {"children": [{"block_type": 27, "image": {}}], "index": -1}
        result = self._api_call(
            lambda: self.session.post(
                url, json=body, headers=self._feishu_headers(),
                params={"document_revision_id": "-1"}, timeout=30,
            ), "创建空图片block",
        )
        if result.get("code") != 0:
            self._warn(f"创建空图片 block 失败: {result.get('msg', '')[:80]}")
            return
        block_id = result["data"]["children"][0]["block_id"]

        time.sleep(0.5)

        # 3. 上传图片到该 block（带 429 重试）
        upload_url = f"{FEISHU_BASE}/drive/v1/medias/upload_all"
        extra = json.dumps({"drive_route_token": doc_id})

        def _do_upload():
            headers = {"Authorization": f"Bearer {self.creds['feishu']['user_access_token']}"}
            with open(temp_path, "rb") as f:
                return self.session.post(
                    upload_url, headers=headers,
                    files={"file": (filename, f, "image/png")},
                    data={
                        "file_name": filename,
                        "parent_type": "docx_image",
                        "parent_node": block_id,
                        "size": str(file_size),
                        "extra": extra,
                    },
                    timeout=60,
                )

        upload_result = self._api_call(_do_upload, "图片上传")
        if upload_result.get("code") != 0:
            self._warn(f"图片上传失败: {upload_result.get('msg', '')[:80]}")
            return
        file_token = upload_result["data"]["file_token"]

        time.sleep(0.5)

        # 4. replace_image 绑定 token（带 429 重试）
        patch_url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/{block_id}"
        pr = self._api_call(
            lambda: self.session.patch(
                patch_url,
                json={"replace_image": {"token": file_token}, "block_id": block_id},
                headers=self._feishu_headers(),
                params={"document_revision_id": "-1"}, timeout=30,
            ), "replace_image",
        )
        if pr.get("code") != 0:
            self._warn(f"replace_image 失败: {pr.get('msg', '')[:80]}")
            return
        log.info(f"图片写入成功: {file_token}")

    # ── 飞书 API ─────────────────────────────────────────

    def resolve_wiki_token(self, wiki_url: str) -> str:
        """从飞书 wiki URL 解析出 document_id（委托到 feishu_token 模块）"""
        return _resolve_wiki(wiki_url, self.creds, self.session)

    def _ensure_feishu_token(self):
        _ensure_token(self.creds, self.session)

    def _feishu_headers(self) -> dict:
        return _feishu_hdrs(self.creds)

    def _safe_json(self, resp, label="API") -> dict:
        """安全解析响应 JSON，空响应或限流时返回错误 dict"""
        if resp.status_code == 429:
            self._warn(f"{label}: 限流 429，等待 3 秒后重试")
            time.sleep(3)
            return {"code": 429, "msg": "rate limited"}
        try:
            return resp.json()
        except Exception:
            self._warn(f"{label}: 响应无法解析 (status={resp.status_code}, body={resp.text[:200]})")
            return {"code": -1, "msg": f"invalid response: {resp.status_code}"}

    # 飞书 API 错误码
    QUOTA_EXHAUSTED_CODE = 99991403  # 额度耗尽
    RATE_LIMIT_CODES = {429, 99991400}  # 限流

    def _api_call(self, request_fn, label="API", retries=3) -> dict:
        """发请求 + 解析 JSON，遇到限流自动重试，遇到额度耗尽或断连立即返回"""
        retried = False
        for attempt in range(retries):
            try:
                resp = request_fn()
                result = self._safe_json(resp, label)
            except ConnectionResetError as e:
                # 连接被重置（大批量请求后服务器断连）
                self._warn(f"{label}: 连接被重置，等待 5s 后继续 (attempt {attempt + 1})")
                retried = True
                time.sleep(5)
                if attempt < retries - 1:
                    continue
                return {"code": -1, "msg": f"connection reset: {e}"}
            except Exception as e:
                self._warn(f"{label}: 请求异常 ({type(e).__name__}), 等待 3s 后重试")
                retried = True
                time.sleep(3)
                if attempt < retries - 1:
                    continue
                return {"code": -1, "msg": f"request error: {e}"}

            # 额度耗尽：立即返回，不重试（继续重试只会消耗更多额度）
            if result.get("code") == self.QUOTA_EXHAUSTED_CODE:
                log.error(f"{label}: API 额度耗尽，停止调用")
                self._record_warning(f"[ERROR] {label}: API 额度耗尽，停止调用")
                self._quota_exhausted = True
                return result

            # 非限流错误：直接返回
            if result.get("code") not in self.RATE_LIMIT_CODES:
                if retried:
                    self._record_warning(f"{label}: 重试后正常")
                return result

            # 限流：等待后重试
            wait = 3 * (attempt + 1)
            self._warn(f"{label}: 限流 (code={result.get('code')}), 第 {attempt + 1} 次重试，等待 {wait}s")
            retried = True
            time.sleep(wait)

        return result

    def append_to_feishu(self, doc_id: str, blocks: list):
        """批量追加 blocks 到飞书文档末尾，每批最多 20 个"""
        self._ensure_feishu_token()

        # 断点恢复：读取进度
        progress_file = PROJECT_DIR / f".progress_{doc_id}.json"
        start_idx = 0
        if getattr(self, "resume", False) and progress_file.exists():
            try:
                progress = json.loads(progress_file.read_text(encoding="utf-8"))
                saved_total = progress.get("total", 0)
                saved_idx = progress.get("next_idx", 0)
                if saved_total == len(blocks) and saved_idx <= len(blocks):
                    start_idx = saved_idx
                    log.info(f"断点恢复: 从第 {start_idx}/{len(blocks)} 个 block 继续")
                else:
                    self._warn(
                        f"进度文件不匹配 (saved_total={saved_total}, "
                        f"current={len(blocks)})，从头开始"
                    )
            except Exception:
                pass

        # 先获取文档根 block_id
        doc_url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}"
        resp = self.session.get(doc_url, headers=self._feishu_headers(), timeout=15)
        resp.raise_for_status()
        doc_data = self._safe_json(resp, "获取文档信息")
        if doc_data.get("code") != 0:
            raise RuntimeError(f"获取文档信息失败: {doc_data}")
        root_block_id = doc_data["data"]["document"]["document_id"]

        # 分离 callout 和普通 blocks，保持顺序
        # 遇到 callout 时先 flush 之前的普通 blocks，再单独处理 callout
        normal_buf = []
        batch_num = 0
        block_idx = 0  # 当前处理的 block 索引

        def save_progress():
            progress_file.write_text(
                json.dumps({"next_idx": block_idx, "total": len(blocks)}),
                encoding="utf-8",
            )

        def flush_normal():
            nonlocal normal_buf, batch_num
            if not normal_buf:
                return
            self._write_blocks_batched(
                doc_id, root_block_id, normal_buf, batch_num
            )
            batch_num += (len(normal_buf) + 19) // 20
            normal_buf = []
            save_progress()

        for block_idx, b in enumerate(blocks):
            if block_idx < start_idx:
                continue  # 跳过已写入的 blocks

            if isinstance(b, dict) and b.get("_callout"):
                flush_normal()
                self._write_callout(doc_id, root_block_id, b)
                batch_num += 1
            elif isinstance(b, dict) and b.get("_quote_container"):
                flush_normal()
                self._write_quote_container(doc_id, root_block_id, b)
                batch_num += 1
            elif isinstance(b, dict) and b.get("_table"):
                flush_normal()
                self._write_table(doc_id, root_block_id, b)
                batch_num += 1
            elif isinstance(b, dict) and b.get("_image"):
                flush_normal()
                self._write_image(doc_id, root_block_id, b)
                batch_num += 1
            elif isinstance(b, dict) and b.get("_nested_list"):
                flush_normal()
                self._write_nested_list(doc_id, root_block_id, b)
                batch_num += 1
            elif isinstance(b, dict) and b.get("_grid"):
                flush_normal()
                self._write_grid(doc_id, root_block_id, b)
                batch_num += 1
            else:
                normal_buf.append(b)
                continue  # 普通 block 缓冲中，不保存进度

            save_progress()

        flush_normal()
        block_idx = len(blocks)
        save_progress()

        # 写入完成，删除进度文件
        if progress_file.exists():
            progress_file.unlink()
            log.info("写入完成，进度文件已清理")

    def _write_blocks_batched(self, doc_id, parent_id, blocks, batch_offset=0):
        """批量写入普通 blocks，每批最多 20 个"""
        # 过滤无效 block（缺少 block_type 的特殊标记如 _image/_table 等）
        valid = []
        for b in blocks:
            if not isinstance(b, dict) or b.get("block_type") is None:
                self._warn(f"跳过无效 block: {str(b)[:150]}")
                continue
            valid.append(b)
        if not valid:
            return
        blocks = valid
        batch_size = 20
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i:i + batch_size]
            url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children"
            body = {"children": batch, "index": -1}
            batch_label = f"批量写入batch{batch_offset + i // batch_size}"
            result = self._api_call(
                lambda _u=url, _b=body: self.session.post(
                    _u, json=_b, headers=self._feishu_headers(), timeout=30),
                batch_label,
            )
            if result.get("code") != 0:
                self._warn(f"批量写入失败 ({batch_label}): {result.get('msg', '')[:100]}")
                for j, single in enumerate(batch):
                    single_body = {"children": [single], "index": -1}
                    try:
                        sr_json = self._api_call(
                            lambda _u=url, _sb=single_body: self.session.post(
                                _u, json=_sb, headers=self._feishu_headers(), timeout=30
                            ),
                            f"单block写入[{i+j}]",
                        )
                    except Exception:
                        self._warn(f"  block[{i+j}] type={single.get('block_type')} 请求异常")
                        time.sleep(1)
                        continue
                    if sr_json.get("code") != 0:
                        self._warn(f"  block[{i+j}] type={single.get('block_type')} 写入失败: {sr_json.get('msg', '')[:80]}")
                    time.sleep(0.2)
            else:
                log.info(f"已写入 batch {batch_offset + i // batch_size + 1}, {len(batch)} blocks")
            if i + batch_size < len(blocks):
                time.sleep(0.3)

    def _write_callout(self, doc_id, parent_id, callout_data):
        """创建 callout 容器并追加子节点"""
        url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children"
        body = {"children": [callout_data["container"]], "index": -1}
        result = self._api_call(
            lambda: self.session.post(url, json=body, headers=self._feishu_headers(), timeout=30),
            "callout创建",
        )
        if result.get("code") != 0:
            self._warn(f"callout 创建失败: {result.get('msg', '')[:100]}")
            return
        callout_block_id = result["data"]["children"][0]["block_id"]
        children = callout_data.get("children", [])
        if children:
            self._write_column_content(doc_id, callout_block_id, children)
            # 创建 callout 时自动生成空段落在 index 0，写入内容后删除它
            self._api_call(
                lambda: self.session.delete(
                    f"{FEISHU_BASE}/docx/v1/documents/{doc_id}"
                    f"/blocks/{callout_block_id}/children/batch_delete",
                    json={"start_index": 0, "end_index": 1},
                    headers=self._feishu_headers(),
                    params={"document_revision_id": "-1"}, timeout=15,
                ), "删除callout空段落",
            )
        log.info(f"已写入 callout ({len(children)} 子节点)")

    def _write_quote_container(self, doc_id, parent_id, qc_data):
        """创建 quote_container 容器并追加子节点"""
        url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children"
        body = {"children": [qc_data["container"]], "index": -1}
        result = self._api_call(
            lambda: self.session.post(url, json=body, headers=self._feishu_headers(), timeout=30),
            "quote_container创建",
        )
        if result.get("code") != 0:
            self._warn(f"quote_container 创建失败: {result.get('msg', '')[:100]}")
            return
        qc_block_id = result["data"]["children"][0]["block_id"]
        children = qc_data.get("children", [])
        if children:
            self._write_column_content(doc_id, qc_block_id, children)
            # 创建 quote_container 时自动生成空段落在 index 0，写入内容后删除它
            self._api_call(
                lambda: self.session.delete(
                    f"{FEISHU_BASE}/docx/v1/documents/{doc_id}"
                    f"/blocks/{qc_block_id}/children/batch_delete",
                    json={"start_index": 0, "end_index": 1},
                    headers=self._feishu_headers(),
                    params={"document_revision_id": "-1"}, timeout=15,
                ), "删除quote_container空段落",
            )
        log.info(f"已写入 quote_container ({len(children)} 子节点)")

    def _write_nested_list(self, doc_id, parent_id, list_data):
        """创建父列表项 → 将子列表项写入父列表项下"""
        url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children"
        body = {"children": [list_data["parent"]], "index": -1}
        result = self._api_call(
            lambda: self.session.post(url, json=body, headers=self._feishu_headers(), timeout=30),
            "嵌套列表父项",
        )
        if result.get("code") != 0:
            self._warn(f"嵌套列表父项创建失败: {result.get('msg', '')[:100]}")
            return
        parent_block_id = result["data"]["children"][0]["block_id"]
        children = list_data.get("children", [])
        if children:
            # 子节点中可能也有嵌套列表，递归处理
            normal_buf = []
            for child in children:
                if isinstance(child, dict) and child.get("_nested_list"):
                    if normal_buf:
                        self._write_blocks_batched(doc_id, parent_block_id, normal_buf)
                        normal_buf = []
                    self._write_nested_list(doc_id, parent_block_id, child)
                elif isinstance(child, dict) and child.get("_image"):
                    if normal_buf:
                        self._write_blocks_batched(doc_id, parent_block_id, normal_buf)
                        normal_buf = []
                    self._write_image(doc_id, parent_block_id, child)
                else:
                    normal_buf.append(child)
            if normal_buf:
                self._write_blocks_batched(doc_id, parent_block_id, normal_buf)
        log.info(f"已写入嵌套列表 ({len(children)} 子项)")

    def _write_grid(self, doc_id, parent_id, grid_data):
        """创建 grid → 获取 column block_ids → 逐列写入内容"""
        columns = grid_data.get("columns", [])
        column_size = grid_data["column_size"]

        # 飞书 grid 最多 5 列，超过则拆分成多个 grid
        if column_size > 5:
            for start in range(0, len(columns), 5):
                chunk = columns[start:start + 5]
                sub_grid = {
                    "_grid": True,
                    "column_size": len(chunk),
                    "columns": chunk,
                }
                self._write_grid(doc_id, parent_id, sub_grid)
            return

        url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children"
        grid_def = {
            "block_type": 24,
            "grid": {"column_size": column_size},
        }
        body = {"children": [grid_def], "index": -1}
        result = self._api_call(
            lambda: self.session.post(url, json=body, headers=self._feishu_headers(), timeout=30),
            "grid创建",
        )
        if result.get("code") != 0:
            self._warn(f"grid 创建失败: {result.get('msg', '')[:100]}")
            return

        grid_block = result["data"]["children"][0]
        grid_block_id = grid_block["block_id"]
        # children 是 block_id 字符串列表
        col_block_ids = grid_block.get("grid", {}).get("children", [])
        if not col_block_ids:
            col_block_ids = grid_block.get("children", [])

        columns = grid_data.get("columns", [])

        # 设置列宽比例（通过 batch_update，一次性设置所有列）
        width_ratios = [col.get("width_ratio") for col in columns]
        if any(w for w in width_ratios):
            patch_url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/batch_update"
            self.session.patch(
                patch_url,
                json={"requests": [{
                    "block_id": grid_block_id,
                    "update_grid_column_width_ratio": {"width_ratios": width_ratios},
                }]},
                headers=self._feishu_headers(),
                params={"document_revision_id": "-1"}, timeout=30,
            )

        # 逐列写入内容，然后删除每列自带的空 paragraph（创建 grid 时自动生成在 index 0）
        for i, col in enumerate(columns):
            if i >= len(col_block_ids):
                break
            col_id = col_block_ids[i]
            children = col.get("children", [])
            if children:
                self._write_column_content(doc_id, col_id, children)
                # 内容追加到末尾后，空 paragraph 仍在 index 0，删除它
                self._api_call(
                    lambda c=col_id: self.session.delete(
                        f"{FEISHU_BASE}/docx/v1/documents/{doc_id}"
                        f"/blocks/{c}/children/batch_delete",
                        json={"start_index": 0, "end_index": 1},
                        headers=self._feishu_headers(),
                        params={"document_revision_id": "-1"}, timeout=15,
                    ), "删除列空段落",
                )
            time.sleep(0.2)

        log.info(f"已写入 grid ({column_size} 列)")

    def _write_column_content(self, doc_id, parent_id, blocks):
        """写入列/容器内的混合内容（普通 blocks + 各种特殊类型）"""
        normal_buf = []
        def _flush():
            nonlocal normal_buf
            if normal_buf:
                self._write_blocks_batched(doc_id, parent_id, normal_buf)
                normal_buf = []
        for b in blocks:
            if not isinstance(b, dict):
                normal_buf.append(b)
                continue
            if b.get("_image"):
                _flush()
                self._write_image(doc_id, parent_id, b)
            elif b.get("_callout"):
                _flush()
                self._write_callout(doc_id, parent_id, b)
            elif b.get("_quote_container"):
                _flush()
                self._write_quote_container(doc_id, parent_id, b)
            elif b.get("_nested_list"):
                _flush()
                self._write_nested_list(doc_id, parent_id, b)
            elif b.get("_grid"):
                _flush()
                self._write_grid(doc_id, parent_id, b)
            elif b.get("_table"):
                _flush()
                self._write_table(doc_id, parent_id, b)
            else:
                normal_buf.append(b)
        _flush()

    def _write_table(self, doc_id, parent_id, table_data):
        """创建空表格 → 获取 cell block_id → 逐 cell 写入内容 → 合并单元格"""
        url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children"
        body = {"children": [table_data["table_def"]], "index": -1}
        result = self._api_call(
            lambda: self.session.post(url, json=body, headers=self._feishu_headers(), timeout=30),
            "table创建",
        )
        if result.get("code") != 0:
            self._warn(f"table 创建失败: {result.get('msg', '')[:100]}")
            return

        # 从返回中提取 table block 及其 cell block_ids
        table_block = result["data"]["children"][0]
        table_block_id = table_block["block_id"]
        cell_ids = table_block.get("table", {}).get("cells", [])
        cells_content = table_data.get("cells_content", [])

        written = 0
        for i, cell_id in enumerate(cell_ids):
            if i >= len(cells_content) or not cells_content[i]:
                continue
            cell_blocks = cells_content[i]
            # 按原始顺序写入：遇到特殊 block 先 flush 普通 block
            normal_buf = []
            def _flush_normal(cid=cell_id):
                nonlocal normal_buf
                if normal_buf:
                    self._write_blocks_batched(doc_id, cid, normal_buf)
                    normal_buf = []
            for b in cell_blocks:
                if b.get("_image"):
                    _flush_normal()
                    self._write_image(doc_id, cell_id, b)
                elif b.get("_grid"):
                    _flush_normal()
                    self._write_grid(doc_id, cell_id, b)
                elif b.get("_quote_container"):
                    _flush_normal()
                    self._write_quote_container(doc_id, cell_id, b)
                elif b.get("_table"):
                    _flush_normal()
                    self._write_table(doc_id, cell_id, b)
                elif b.get("_nested_list"):
                    _flush_normal()
                    self._write_nested_list(doc_id, cell_id, b)
                else:
                    normal_buf.append(b)
            _flush_normal()
            # 删除 cell 自带的空 paragraph（创建表格时自动生成在 index 0）
            self._api_call(
                lambda cid=cell_id: self.session.delete(
                    f"{FEISHU_BASE}/docx/v1/documents/{doc_id}"
                    f"/blocks/{cid}/children/batch_delete",
                    json={"start_index": 0, "end_index": 1},
                    headers=self._feishu_headers(),
                    params={"document_revision_id": "-1"}, timeout=15,
                ), "删除cell空段落",
            )
            written += 1
            time.sleep(0.2)

        row_size = table_data["table_def"]["table"]["property"]["row_size"]
        col_size = table_data["table_def"]["table"]["property"]["column_size"]
        log.info(f"已写入 table {row_size}x{col_size} ({written} cells 有内容)")

        # 合并单元格
        merge_info = table_data.get("merge_info", [])
        if merge_info:
            self._merge_table_cells(doc_id, table_block_id, merge_info, row_size, col_size)

    def _merge_table_cells(self, doc_id, table_block_id, merge_info, row_size, col_size):
        """根据 merge_info 调用 batch_update 合并表格单元格"""
        # merge_info 按行主序排列，每个 cell 一个条目
        # 找出 col_span>1 或 row_span>1 的 cell，计算其行列索引
        merge_requests = []
        visited = set()  # 跳过已被合并覆盖的 cell

        for idx, info in enumerate(merge_info):
            row = idx // col_size
            col = idx % col_size
            if (row, col) in visited:
                continue
            rs = info.get("row_span", 1)
            cs = info.get("col_span", 1)
            if rs <= 1 and cs <= 1:
                continue
            # 标记被覆盖的 cell
            for r in range(row, row + rs):
                for c in range(col, col + cs):
                    if (r, c) != (row, col):
                        visited.add((r, c))
            # 左闭右开区间
            merge_requests.append({
                "block_id": table_block_id,
                "merge_table_cells": {
                    "row_start_index": row,
                    "row_end_index": row + rs,
                    "column_start_index": col,
                    "column_end_index": col + cs,
                },
            })

        if not merge_requests:
            return

        url = f"{FEISHU_BASE}/docx/v1/documents/{doc_id}/blocks/batch_update"
        body = {"requests": merge_requests}
        result = self._api_call(
            lambda: self.session.patch(
                url, json=body, headers=self._feishu_headers(),
                params={"document_revision_id": "-1"}, timeout=30,
            ), "合并单元格",
        )
        if result.get("code") != 0:
            self._warn(f"合并单元格失败: {result.get('msg', '')[:100]}")
        else:
            log.info(f"已合并 {len(merge_requests)} 组单元格")

    # ── 日志记录 ──────────────────────────────────────────

    def _record_skipped(self, block: dict, reason: str, context: str = ""):
        ctx = context or getattr(self, "_current_context", "")
        text_preview = self._get_block_text(block)[:80] if block else ""
        block_index = getattr(self, "_current_block_index", -1)
        total_blocks = getattr(self, "_current_total_blocks", -1)
        self.skipped_blocks.append({
            "type": block.get("type"),
            "reason": reason,
            "context": ctx,
            "text": text_preview,
            "block_index": block_index,
            "total_blocks": total_blocks,
            "block_id": block.get("blockId", block.get("block_id", "")),
            "parent_id": block.get("parentId", block.get("parent_id", "")),
        })
        # 同步输出到主日志
        pos = f" [{block_index+1}/{total_blocks}]" if block_index >= 0 else ""
        log.warning(f"跳过 block type={block.get('type')}{pos}: {reason}")
        if ctx:
            log.warning(f"  {ctx}")

    def _record_warning(self, msg: str, context: str = ""):
        """记录警告信息，写入错误日志"""
        if not hasattr(self, "warning_logs"):
            self.warning_logs = []
        ctx = context or getattr(self, "_current_context", "")
        block_index = getattr(self, "_current_block_index", -1)
        total_blocks = getattr(self, "_current_total_blocks", -1)
        self.warning_logs.append({
            "msg": msg,
            "context": ctx,
            "block_index": block_index,
            "total_blocks": total_blocks,
        })

    def _warn(self, msg: str, context: str = ""):
        """log.warning + 记录到错误日志"""
        log.warning(msg)
        self._record_warning(msg, context)

    def _start_article_log(self, title: str):
        """为每篇文章创建独立的主运行日志文件"""
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title[:30])
        log_path = LOG_DIR / f"wiki_{safe_title}_{ts}.log"
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)
        self._article_log_handler = handler
        self._article_log_path = log_path

    def _stop_article_log(self):
        """移除当前文章的日志文件 handler"""
        handler = getattr(self, "_article_log_handler", None)
        if handler:
            handler.close()
            log.removeHandler(handler)
            self._article_log_handler = None

    @staticmethod
    def _fmt_context(item: dict, f) -> None:
        """统一输出上下文信息（警告和跳过块共用）"""
        bi, bt = item.get("block_index", -1), item.get("total_blocks", -1)
        if bi >= 0:
            f.write(f"    位置: 第 {bi+1}/{bt} 个 block\n")
        if item.get("block_id"):
            f.write(f"    block_id: {item['block_id']}\n")
        if item.get("parent_id"):
            f.write(f"    parent_id: {item['parent_id']}\n")
        if item.get("text"):
            f.write(f"    文本预览: {item['text']}\n")
        if item.get("context"):
            f.write(f"    上下文: {item['context']}\n")

    def _write_skip_log(self, title: str, source_url: str):
        warnings = getattr(self, "warning_logs", [])
        if not self.skipped_blocks and not warnings:
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title[:30])
        log_path = LOG_DIR / f"err_{safe_title}_{ts}.log"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"文章标题: {title}\n")
            f.write(f"源地址: {source_url}\n")
            f.write(f"记录时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"跳过总数: {len(self.skipped_blocks)}  警告总数: {len(warnings)}\n")
            f.write("=" * 60 + "\n")
            if warnings:
                f.write("\n[警告信息]\n\n")
                for j, w in enumerate(warnings, 1):
                    f.write(f"[{j}] {w['msg']}\n")
                    self._fmt_context(w, f)
                    f.write("-" * 40 + "\n")
                f.write("\n")
            if self.skipped_blocks:
                f.write("[跳过的 block]\n\n")
                for i, item in enumerate(self.skipped_blocks, 1):
                    f.write(f"[{i}] block_type={item['type']}\n")
                    f.write(f"    原因: {item['reason']}\n")
                    self._fmt_context(item, f)
                    f.write("-" * 40 + "\n")
        log.info(f"错误日志已写入: {log_path}")
        self.skipped_blocks.clear()
        if hasattr(self, "warning_logs"):
            self.warning_logs.clear()

    # ── 本地 Markdown 导出 ─────────────────────────────────

    def _block_to_md(self, block: dict) -> str:
        """将单个源 block 转为 Markdown 文本"""
        btype = block.get("type", 0)
        attr = block.get("blockAttr", {})

        # heading1~heading9 → # ~ #########
        if 3 <= btype <= 11:
            level = btype - 2
            text = self._elements_to_md(attr, f"heading{level}")
            return f"{'#' * level} {text}"

        # 普通文本
        if btype == 2:
            return self._elements_to_md(attr, "text")

        # 无序列表
        if btype == 12:
            text = self._elements_to_md(attr, "bullet")
            result = f"- {text}"
            for child in block.get("childrens", []):
                if child.get("type") in (12, 13):
                    child_md = self._block_to_md(child)
                    result += "\n" + "  " + child_md
            return result

        # 有序列表
        if btype == 13:
            text = self._elements_to_md(attr, "ordered")
            result = f"1. {text}"
            for child in block.get("childrens", []):
                if child.get("type") in (12, 13):
                    child_md = self._block_to_md(child)
                    result += "\n" + "   " + child_md
            return result

        # 代码块
        if btype == 14:
            lang = attr.get("code", {}).get("style", {}).get("language", "")
            text = self._elements_to_md(attr, "code")
            return f"```{lang}\n{text}\n```"

        # 引用
        if btype == 15:
            text = self._elements_to_md(attr, "quote")
            return f"> {text}"

        # 分割线
        if btype == 22:
            return "---"

        # 图片 → 占位标记
        if btype == 27:
            token = attr.get("image", {}).get("token", "")
            return f"![图片]({token})"

        # callout / quote_container → 递归子节点
        if btype in (19, 34):
            lines = []
            prefix = "> " if btype == 34 else "> "
            for child in block.get("childrens", []):
                child_md = self._block_to_md(child)
                if child_md:
                    lines.append(prefix + child_md)
            return "\n".join(lines)

        # table → 简化文本
        if btype == 31:
            return self._table_to_md(block)

        # grid → 逐列输出
        if btype == 24:
            parts = []
            for col in block.get("childrens", []):
                for child in col.get("childrens", []):
                    child_md = self._block_to_md(child)
                    if child_md:
                        parts.append(child_md)
            return "\n\n".join(parts)

        # file 块
        if btype == 23:
            name = attr.get("file", {}).get("name", "附件")
            return f"📎 {name}"

        # 兜底
        text = self._get_block_text(block)
        return text if text else ""

    def _elements_to_md(self, attr: dict, key: str) -> str:
        """从 blockAttr 中提取指定 key 的 elements，拼接为纯文本"""
        elements = attr.get(key, {}).get("elements", [])
        parts = []
        for el in elements:
            tr = el.get("textRun") or el.get("text_run")
            if tr:
                content = tr.get("content", "")
                style = tr.get("textElementStyle") or tr.get("text_element_style") or {}
                if style.get("bold"):
                    content = f"**{content}**"
                if style.get("italic"):
                    content = f"*{content}*"
                if style.get("inlineCode") or style.get("inline_code"):
                    content = f"`{content}`"
                if style.get("strikethrough"):
                    content = f"~~{content}~~"
                link = style.get("link", {})
                if link and link.get("url"):
                    content = f"[{content}]({link['url']})"
                parts.append(content)
            elif "equation" in el:
                parts.append(f"${el['equation'].get('content', '')}$")
        return "".join(parts)

    def _table_to_md(self, block: dict) -> str:
        """将 table block 转为 Markdown 表格"""
        rows_data = []
        for row_block in block.get("childrens", []):
            row = []
            for cell in row_block.get("childrens", []):
                cell_text = self._get_block_text(cell)
                if not cell_text:
                    # 尝试从子节点提取
                    parts = []
                    for child in cell.get("childrens", []):
                        parts.append(self._get_block_text(child))
                    cell_text = " ".join(p for p in parts if p)
                row.append(cell_text.replace("|", "\\|"))
            rows_data.append(row)

        if not rows_data:
            return ""

        lines = []
        # 表头
        lines.append("| " + " | ".join(rows_data[0]) + " |")
        lines.append("| " + " | ".join(["---"] * len(rows_data[0])) + " |")
        for row in rows_data[1:]:
            # 补齐列数
            while len(row) < len(rows_data[0]):
                row.append("")
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    def export_local_md(self, blocks: list, export_path: str, doc_title: str = ""):
        """将 filtered blocks 导出为本地 Markdown 文件"""
        path = Path(export_path)
        if not path.is_absolute():
            path = PROJECT_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        if doc_title:
            lines.append(f"# {doc_title}")
            lines.append("")

        for b in blocks:
            md = self._block_to_md(b)
            if md:
                lines.append(md)
                lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        log.info(f"  本地导出完成: {path} ({len(blocks)} blocks)")

    # ── 主流程 ────────────────────────────────────────────

    def run(self, mappings=None):
        if mappings is None:
            # 独立运行：从 tasks 筛选 yitang 任务构建 mappings
            tasks = self.config.get("tasks", [])
            mappings = []
            for t in tasks:
                if t.get("source_type") == "yitang" and t.get("source_wiki_url"):
                    mappings.append({
                        "source_url": t["source_wiki_url"],
                        "target_url": t.get("target_wiki_url", ""),
                        "output_name": t.get("output_name", ""),
                    })
        if not mappings:
            log.warning("没有找到 yitang 文稿任务（tasks 中无 source_wiki_url）")
            return

        for idx, m in enumerate(mappings):
            # 检查是否已额度耗尽
            if self._quota_exhausted:
                log.warning("检测到 API 额度耗尽，停止后续任务")
                break

            source_url = m["source_url"]
            target_url = m["target_url"]
            log.info(f"[{idx + 1}/{len(mappings)}] 处理: {source_url}")

            try:
                # 1. 获取源文档 blocks
                if self._is_feishu_wiki_url(source_url):
                    blocks_data = self.fetch_feishu_wiki_blocks(source_url)
                elif self._is_feishu_url(source_url):
                    blocks_data = self.fetch_feishu_blocks(source_url)
                else:
                    blocks_data = self.fetch_source_blocks(source_url)
                # 标题在根 block 的 page 属性中
                root = blocks_data.get("blocks", {})
                page_attr = root.get("blockAttr", {}).get("page", {})
                doc_title = ""
                for el in page_attr.get("elements", []):
                    tr = el.get("text_run") or el.get("textRun")
                    if tr:
                        doc_title += tr.get("content", "")
                doc_title = doc_title or f"文档{idx + 1}"
                self._start_article_log(doc_title)
                log.info(f"  文档标题: {doc_title}")

                # 2. 过滤内容范围（飞书原始文档直接全文复制）
                is_feishu = self._is_feishu_url(source_url)
                if is_feishu or m.get("full_copy", False):
                    root_block = blocks_data.get("blocks", {})
                    filtered = self._flatten_blocks(root_block)
                else:
                    filtered = self.filter_blocks(blocks_data, doc_title)

                # 3. 标题自动编号（飞书和一堂文档通用）
                heading_cfg = m.get("heading_number", {})
                if heading_cfg:
                    filtered = self._auto_heading_numbers(
                        filtered, heading_cfg
                    )
                log.info(f"  过滤后 blocks 数: {len(filtered)}")
                if not filtered:
                    self._warn("  过滤后无内容，跳过")
                    continue

                # debug: dump 含特定关键词的 block 完整结构
                # self._dump_debug_blocks(blocks_data, filtered)

                # 3. 解析目标文档 ID（图片上传需要 doc_id）
                doc_id = ""
                if not getattr(self, "dry_run", False):
                    doc_id = self.resolve_wiki_token(target_url)
                    log.info(f"  目标文档 ID: {doc_id}")

                # 4. 转换为飞书写入格式
                converted = []
                self._current_total_blocks = len(filtered)
                last_heading = ""
                for i, b in enumerate(filtered):
                    self._current_block_index = i
                    # 追踪最近的标题，用于章节定位
                    if b.get("type", 0) in HEADING_TYPES:
                        last_heading = self._get_block_text(b)[:50]
                    ctx_parts = []
                    if last_heading:
                        ctx_parts.append(f"章节: {last_heading}")
                    for offset in (2, 1):
                        j = i - offset
                        if j >= 0:
                            ctx_parts.append(f"\n      前{offset}: {self._get_block_desc(filtered[j])[:80]}")
                    for offset in (1, 2):
                        j = i + offset
                        if j < len(filtered):
                            ctx_parts.append(f"\n      后{offset}: {self._get_block_desc(filtered[j])[:80]}")
                    context = "".join(ctx_parts)

                    # 临时保存 context 供 _record_skipped 使用
                    self._current_context = context
                    result = self.convert_block(b, doc_id)
                    if result:
                        converted.append(result)
                log.info(f"  转换后 blocks 数: {len(converted)}")

                # 5. 写入飞书文档
                if getattr(self, "dry_run", False):
                    log.info("  [dry-run] 跳过飞书写入")
                else:
                    self.append_to_feishu(doc_id, converted)
                    log.info(f"  写入完成!")

                # 5.5 本地导出 Markdown
                # 优先级：local_export > output_name > 自动标题
                export_dir = self.config["path_yitang_dir"]
                export_name = m.get("local_export", "")
                if not export_name:
                    manual_output = m.get("output_name", "").strip()
                    if manual_output:
                        export_name = f"{manual_output}.md"
                    elif doc_title:
                        safe_title = re.sub(r'[\\/:*?"<>|]', "_", doc_title).strip()
                        export_name = f"{safe_title}.md"
                if export_name:
                    export_path = Path(export_dir) / export_name
                    self.export_local_md(filtered, str(export_path), doc_title)
                    log.info(f"  本地 MD 已导出: {export_path}")

                # 6. 记录跳过的 blocks
                self._write_skip_log(doc_title, source_url)
                self._stop_article_log()

            except Exception as e:
                log.error(f"  处理失败: {e}", exc_info=True)
                self._record_warning(f"[ERROR] 处理失败: {e}")
                self._write_skip_log(f"error_{idx}", source_url)
                self._stop_article_log()
                continue

        log.info("全部处理完成")


def process_yitang_wiki(task, config, creds):
    """供 s2_wiki 调度器调用的入口"""
    copier = YitangCopier()
    mapping = {
        "source_url": task["source_wiki_url"],
        "target_url": task.get("target_wiki_url", ""),
        "output_name": task.get("output_name", ""),
    }
    copier.run(mappings=[mapping])


if __name__ == "__main__":
    import sys
    copier = YitangCopier()
    copier.dry_run = "--dry-run" in sys.argv
    copier.resume = "--resume" in sys.argv
    copier.run()
