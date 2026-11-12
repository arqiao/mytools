"""一堂文稿章节边界筛选测试。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s2w_yitang_wiki import YitangCopier


def heading(level, text):
    return {
        "type": level + 2,
        "blockAttr": {f"heading{level}": {"elements": [{"textRun": {"content": text}}]}},
    }


def paragraph(text):
    return {"type": 2, "blockAttr": {"text": {"elements": [{"textRun": {"content": text}}]}}}


class FilterBlocksTest(unittest.TestCase):
    def setUp(self):
        self.copier = YitangCopier.__new__(YitangCopier)
        self.copier.config = {
            "s1_yitang_wikicopy": {
                "default_exclude_before_heading": "",
                "default_include_start_heading": "Start",
                "default_exclude_after_heading": "",
                "default_include_end_heading": "End",
            }
        }
        self.copier.full_copy_titles = []
        self.copier._warn = lambda _message: None
        self.blocks_data = {"blocks": {"type": 1, "childrens": [
            heading(1, "Intro"), paragraph("intro text"),
            heading(1, "Start"), paragraph("start text"),
            heading(2, "Nested"), paragraph("nested text"),
            heading(1, "End"), paragraph("end text"),
            heading(1, "After"), paragraph("after text"),
        ]}}

    def texts(self, mapping=None):
        return [self.copier._get_block_text(block) for block in self.copier.filter_blocks(
            self.blocks_data, mapping=mapping
        )]

    def test_default_include_boundaries_keep_complete_end_section(self):
        self.assertEqual(
            self.texts(),
            ["Start", "start text", "Nested", "nested text", "End", "end text"],
        )

    def test_exclude_boundaries_skip_complete_sections(self):
        self.assertEqual(
            self.texts({"exclude_before_heading": "Start", "exclude_after_heading": "After"}),
            ["End", "end text"],
        )

    def test_exclude_boundaries_win_over_include_boundaries(self):
        self.assertEqual(
            self.texts({
                "exclude_before_heading": "Start",
                "include_start_heading": "Intro",
                "exclude_after_heading": "After",
                "include_end_heading": "End",
            }),
            ["End", "end text"],
        )

    def test_full_copy_ignores_defaults(self):
        self.assertEqual(
            self.texts({"full_copy": True}),
            ["Intro", "intro text", "Start", "start text", "Nested", "nested text", "End", "end text", "After", "after text"],
        )

    def test_local_export_path_uses_sanitized_title(self):
        self.copier.config["path_yitang_dir"] = "out-yitang/"
        self.assertEqual(
            str(self.copier.local_export_path({}, '标题: A/B')),
            "out-yitang/标题_ A_B.md",
        )

    def test_split_large_table_by_rows_preserves_cell_order(self):
        cells = [[{"text": str(index)}] for index in range(75)]
        table_data = {
            "table_def": {"block_type": 31, "table": {"property": {
                "row_size": 25, "column_size": 3, "header_row": True,
            }}},
            "cells_content": cells,
            "merge_info": [],
        }

        parts = self.copier._split_table_rows(table_data, max_rows=16)

        self.assertEqual([part["table_def"]["table"]["property"]["row_size"] for part in parts], [16, 9])
        self.assertEqual(
            [cell for part in parts for cell in part["cells_content"]],
            cells,
        )
        self.assertTrue(parts[0]["table_def"]["table"]["property"]["header_row"])
        self.assertNotIn("header_row", parts[1]["table_def"]["table"]["property"])

    def test_sheet_header_row_skips_cover_and_empty_rows(self):
        rows = [
            [{"type": "embed-image"}, None, None],
            [None, None, None],
            ["名称", "文档链接", "简介"],
            ["项目 A", "https://example.com", "说明"],
        ]
        self.assertEqual(self.copier._find_sheet_header_row(rows, 3), 2)

    def test_sheet_url_and_mention_links_are_preserved(self):
        blocks = self.copier._sheet_val_to_blocks([
            {"type": "url", "text": "回放", "link": "https://example.com/minutes"},
            {"type": "mention", "text": "项目 Wiki", "link": "https%3A%2F%2Fexample.feishu.cn%2Fwiki%2Fabc"},
        ])
        elements = blocks[0]["blockAttr"]["text"]["elements"]
        self.assertEqual(elements[0]["textRun"]["textElementStyle"]["link"]["url"], "https://example.com/minutes")
        self.assertEqual(elements[1]["textRun"]["textElementStyle"]["link"]["url"], "https://example.feishu.cn/wiki/abc")

    def test_sheet_attachment_becomes_file_block(self):
        blocks = self.copier._sheet_val_to_blocks({
            "type": "attachment", "fileToken": "file-token", "text": "附件.pdf",
            "mimeType": "application/pdf",
        })
        self.assertEqual(blocks[0]["type"], 23)
        self.assertEqual(blocks[0]["blockAttr"]["file"]["token"], "file-token")

    def test_download_attachments_recurses_and_honors_switch(self):
        downloaded = []
        self.copier.download_attachments = True
        self.copier._download_attachment = lambda file_info, title: downloaded.append((file_info["token"], title))
        blocks = [{
            "type": 31, "childrens": [{
                "type": 32, "childrens": [{
                    "type": 23, "blockAttr": {"file": {"token": "inside-table"}}, "childrens": [],
                }],
            }],
        }, {
            "type": 23, "blockAttr": {"file": {"token": "outside-table"}}, "childrens": [],
        }]
        self.copier._download_attachments(blocks, "测试文稿")
        self.assertEqual(downloaded, [("inside-table", "测试文稿"), ("outside-table", "测试文稿")])

        self.copier.download_attachments = False
        self.copier._download_attachments(blocks, "测试文稿")
        self.assertEqual(len(downloaded), 2)

    def test_attachment_duplicate_path_uses_copy_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.copier.config["path_attachment_dir"] = temp_dir
            self.copier._downloaded_attachment_tokens = set()
            self.copier._ensure_feishu_token = lambda: None

            class Response:
                headers = {"Content-Type": "application/pdf"}
                def raise_for_status(self):
                    pass
                def iter_content(self, chunk_size):
                    yield b"test"

            class Session:
                def get(self, *args, **kwargs):
                    return Response()

            self.copier.session = Session()
            self.copier._feishu_headers = lambda: {}
            first = self.copier._download_attachment({"token": "token-1", "name": "资料.pdf"}, "测试")
            self.copier._downloaded_attachment_tokens = set()
            second = self.copier._download_attachment({"token": "token-2", "name": "资料.pdf"}, "测试")

            self.assertEqual(first.name, "资料.pdf")
            self.assertEqual(second.name, "资料-copy1.pdf")
if __name__ == "__main__":
    unittest.main()
