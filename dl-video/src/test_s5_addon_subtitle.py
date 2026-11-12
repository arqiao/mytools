"""S5 在显式指定字幕时的校订版选择。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import s5_addon


class SubtitleSelectionTest(unittest.TestCase):
    def test_no_prefer_fix_uses_requested_subtitle_and_default_uses_fix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            subtitle = path / "lesson.qwen3_asr.srt"
            subtitle.touch()
            corrected = path / "lesson.qwen3_asr_fix.srt"
            corrected.touch()
            transcript = path / "lesson.md"
            transcript.touch()
            for extra_args, expected in [(["--no-prefer-fix"], subtitle), ([], corrected)]:
                with self.subTest(extra_args=extra_args):
                    argv = ["s5_addon.py", "--subtitle", str(subtitle), "--transcript",
                            str(transcript), "--dry-run", *extra_args]
                    with patch.object(sys, "argv", argv), \
                         patch.object(s5_addon, "load_config", return_value=({}, {})), \
                         patch.object(s5_addon, "parse_transcript", return_value=[("标题", "正文")]), \
                         patch.object(s5_addon, "parse_srt", return_value=[]) as parse_srt:
                        s5_addon.main()
                    parse_srt.assert_called_once_with(str(expected))


if __name__ == "__main__":
    unittest.main()
