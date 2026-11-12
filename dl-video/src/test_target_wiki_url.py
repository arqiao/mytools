"""目标 wiki URL 校验测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from modules.config_utils import is_valid_target_wiki_url


class TargetWikiUrlTest(unittest.TestCase):
    def test_empty_target_is_allowed(self):
        self.assertTrue(is_valid_target_wiki_url(""))

    def test_allowed_targets(self):
        self.assertTrue(is_valid_target_wiki_url("https://arqiaoknow.feishu.cn/wiki/abc"))

    def test_other_targets_are_rejected(self):
        self.assertFalse(is_valid_target_wiki_url("https://yitanger.feishu.cn/wiki/abc"))
        self.assertFalse(is_valid_target_wiki_url("https://yitang.top/homework/abc"))


if __name__ == "__main__":
    unittest.main()
