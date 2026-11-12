"""流水线 S3 音视频输入与音频优先级测试。"""

import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from run_pipeline import (apply_legacy_args, build_plan, command_for_s3, find_s3_audio,
                          get_expected_s3_suffix, parse_args, run_one_task)


class FindS3AudioTest(unittest.TestCase):
    def test_default_s3_engine_is_qwen3_asr(self):
        with patch.object(sys, "argv", ["run_pipeline.py"]):
            self.assertEqual(parse_args().s3_engine, "qwen3-asr")

    def test_subtitle_engine_argument_selects_qwen_or_whisper_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "lesson.m4a"
            audio.touch()
            for engine, flag in [
                ("qwen3-asr", "--qwen3-asr"),
                ("whisper", "--whisper"),
            ]:
                with self.subTest(engine=engine), patch.object(
                    sys, "argv", ["run_pipeline.py", "--audio-file", str(audio),
                                  "--subtitle-engine", engine]
                ):
                    args = parse_args()
                    plan, _, _ = build_plan(args, {}, {}, "test", "lesson")
                    self.assertEqual(args.s3_engine, engine)
                    self.assertIn(flag, plan[2]["cmd"])
                    self.assertFalse(plan[3]["skip"])
                    self.assertNotIn("--no-prefer-fix", plan[4]["cmd"])

    def test_video_file_starts_at_s3_and_passes_video_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "lesson.mp4"
            video.touch()
            with patch.object(
                sys, "argv", ["run_pipeline.py", "--video-file", str(video), "--print-plan"]
            ):
                args = parse_args()

            plan, _, state = build_plan(args, {}, {}, "test", "lesson")

            self.assertEqual(args.audio_file, str(video))
            self.assertTrue(plan[0]["skip"])
            self.assertTrue(plan[1]["skip"])
            self.assertFalse(plan[2]["skip"])
            self.assertEqual(plan[2]["cmd"][2], str(video))
            self.assertEqual(state["mp3_path"], video.absolute())
            self.assertFalse((video.parent / "lesson.m4a").exists())
            self.assertFalse((video.parent / "lesson.mp3").exists())

    def test_legacy_video_path_is_recognized_as_s3_media(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "lesson.ts"
            video.touch()
            with patch.object(sys, "argv", ["run_pipeline.py", str(video)]):
                args = parse_args()
            apply_legacy_args(args)

            self.assertEqual(args.audio_file, str(video))

    def test_qwen3_asr_engine_and_suffix_are_used(self):
        args = Namespace(s3_engine="qwen3-asr", whisper_backend="mlx", whisper_model="large-v3")
        command = command_for_s3(Path("lesson.m4a"), args)
        self.assertEqual(command[-1], "--qwen3-asr")
        self.assertNotIn("--whisper", command)
        self.assertEqual(
            get_expected_s3_suffix({"s3_engine_suffix": {"qwen3_asr": ".qwen3_asr"}},
                                   args.whisper_backend, args.whisper_model, args.s3_engine),
            ".qwen3_asr",
        )

    def test_m4a_is_preferred_over_mp3(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "lesson.m4a").touch()
            (path / "lesson.mp3").touch()
            self.assertEqual(find_s3_audio(path).name, "lesson.m4a")

    def test_mp3_is_used_when_m4a_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "lesson.mp3").touch()
            self.assertEqual(find_s3_audio(path).name, "lesson.mp3")

    def test_seed_asr_engine_is_forwarded_to_s3(self):
        """防止流水线仍硬编码本地 Whisper，忽略云端 ASR 选择。"""
        args = Namespace(
            s3_engine="doubao-seed-asr",
            whisper_backend="mlx",
            whisper_model="large-v3",
        )

        command = command_for_s3(Path("lesson.m4a"), args)

        self.assertEqual(command[-1], "--doubao-seed-asr")
        self.assertNotIn("--whisper", command)

    def test_qwen3_asr_runs_s4_and_s5_uses_corrected_subtitle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            audio = path / "lesson.m4a"
            audio.touch()
            srt = path / "lesson.qwen3_asr.srt"
            srt.touch()
            corrected = path / "lesson.qwen3_asr_fix.srt"
            corrected.touch()
            with patch.object(sys, "argv", ["run_pipeline.py", "--audio-file", str(audio),
                                            "--no-notify"]):
                args = parse_args()

            plan, _, _ = build_plan(args, {}, {}, "test", "lesson")
            self.assertFalse(plan[3]["skip"])
            self.assertEqual(plan[4]["cmd"][-1], "<fix srt discovered after S4>")

            with patch("run_pipeline.run_step", return_value=(True, "")) as run_step:
                self.assertTrue(run_one_task(args, {}, {}, "test", "lesson", path))
            self.assertEqual(
                [call.args[0] for call in run_step.call_args_list],
                ["s3", "s4", "s5"],
            )
            self.assertEqual(run_step.call_args_list[-1].args[2][-2:],
                             ["--subtitle", str(corrected)])

    def test_whisper_still_runs_s4(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "lesson.m4a"
            audio.touch()
            with patch.object(sys, "argv", ["run_pipeline.py", "--audio-file", str(audio),
                                            "--s3-engine", "whisper"]):
                args = parse_args()
            plan, _, _ = build_plan(args, {}, {}, "test", "lesson")
            self.assertFalse(plan[3]["skip"])

    def test_whisper_s5_uses_corrected_subtitle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            audio = path / "lesson.m4a"
            audio.touch()
            (path / "lesson.mlx_l3.srt").touch()
            corrected = path / "lesson.mlx_l3_fix.srt"
            corrected.touch()
            with patch.object(sys, "argv", ["run_pipeline.py", "--audio-file", str(audio),
                                            "--subtitle-engine", "whisper", "--no-notify"]):
                args = parse_args()
            config = {"s3_engine_suffix": {"mlx_whisper_large_v3": ".mlx_l3"}}
            with patch("run_pipeline.run_step", return_value=(True, "")) as run_step:
                self.assertTrue(run_one_task(args, config, {}, "test", "lesson", path))
            self.assertEqual([call.args[0] for call in run_step.call_args_list], ["s3", "s4", "s5"])
            self.assertIn(str(corrected), run_step.call_args_list[-1].args[2])
            self.assertNotIn("--no-prefer-fix", run_step.call_args_list[-1].args[2])

    def test_starting_from_subtitle_still_runs_s4(self):
        with tempfile.TemporaryDirectory() as directory:
            subtitle = Path(directory) / "lesson.qwen3_asr.srt"
            subtitle.touch()
            with patch.object(sys, "argv", ["run_pipeline.py", "--subtitle", str(subtitle)]):
                args = parse_args()
            plan, _, _ = build_plan(args, {}, {}, "test", "lesson")
            self.assertFalse(plan[3]["skip"])
            self.assertNotIn("--no-prefer-fix", plan[4]["cmd"])


if __name__ == "__main__":
    unittest.main()
