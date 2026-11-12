"""s3 字幕分段写入测试。"""

import sys
import unittest
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s3_subtitle import _append_segments_to_srt


class AppendSrtSegmentsTest(unittest.TestCase):
    def test_overlap_is_skipped_and_remaining_segment_is_kept(self):
        output = StringIO()
        idx = _append_segments_to_srt(
            output,
            [{"start": 1, "end": 3, "text": "重复"},
             {"start": 3, "end": 6, "text": "保留"}],
            start_idx=4,
            time_offset=120,
            skip_before=2,
        )
        self.assertEqual(idx, 5)
        self.assertIn("00:02:03,000 --> 00:02:06,000", output.getvalue())
        self.assertIn("保留", output.getvalue())


if __name__ == "__main__":
    unittest.main()
