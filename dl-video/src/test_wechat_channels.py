"""视频号离线回归：无账号、无外网、无模型或飞书调用。"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
import s1w_wechatchannels as wx
import run_pipeline as pipeline
import s5_addon
import s4_srt_fix

SHARE = "https://weixin.qq.com/sph/ALRGH1Htvi"
SIGNED = "https://finder.video.qq.com/media?token=secret&encfilekey=key&fileFormat=h264"
SRT = "1\n00:00:00,000 --> 00:00:01,000\n先理解问题，再提出方案。\n"


class CompatibilityTests(unittest.TestCase):
    def test_adapter_passes_explicit_paths_and_audio_contract(self):
        result = {"status": "success", "video_path": "/tmp/wx_test.mp4", "audio_path": "/tmp/wx_test.m4a"}
        with patch.object(wx._core, "download", return_value=result) as call:
            self.assertEqual(wx.process_wechat_channels({"source_huifang_url": SHARE}, {}), result)
        args, kw = call.call_args
        self.assertEqual(args[1], wx.PROJECT_DIR / "out-wechat")
        self.assertEqual(args[2]["browser_profile"], str(wx.PROJECT_DIR / ".browser_data_wxchannels"))
        self.assertTrue(args[2]["require_audio"])
        self.assertTrue(args[2]["extract_audio"])

    def test_adapter_failure_stops_legacy_caller(self):
        with patch.object(wx._core, "download", return_value={"status": "failed", "error_code": "auth_expired"}):
            with self.assertRaises(wx.ChannelsError) as caught:
                wx.process_wechat_channels({"source_huifang_url": SHARE}, {})
            self.assertEqual(caught.exception.code, "auth_expired")

    def test_imported_functions_have_one_implementation(self):
        self.assertIs(wx.artifact_paths, wx._core.artifact_paths)
        self.assertIs(wx.resolve_video, wx._core.resolve_video)
        self.assertIs(wx.validate_media, wx._core.validate_media)


class PipelineTests(unittest.TestCase):
    def args(self, *extra):
        with patch.object(sys, "argv", ["run_pipeline.py", "--wechat-url", SHARE, "--no-notify", *extra]):
            return pipeline.parse_args()

    def test_config_and_browser_flags_forwarded(self):
        args = self.args("--config", "/tmp/custom.yaml", "--wechat-headless", "--wechat-login-timeout", "40")
        tasks, source = pipeline.build_tasks(args, {})
        cmd = pipeline.command_for_s1(tasks[0], args, {"path_wechat_dir": "/tmp/selected"})
        self.assertIn("src/s1w_wechatchannels.py", cmd)
        self.assertIn("--headless", cmd)
        self.assertIn("/tmp/custom.yaml", cmd)
        self.assertIn(str(Path("/tmp/selected").resolve()), cmd)
        self.assertEqual(source, "direct")
        self.assertIsNone(pipeline.command_for_s2({**tasks[0], "source_wiki_url": "https://example.com"}))

    def test_wechat_output_is_separate_from_other_platforms(self):
        config = {"path_output_dir": "output", "path_yitang_dir": "out-yitang"}
        task = {"source_huifang_url": SHARE}
        expected = wx.PROJECT_DIR / "out-wechat"
        self.assertEqual(wx.wechat_output_dir(config), expected)
        self.assertEqual(pipeline.output_dirs_for_task(task, config), (expected, expected))
        self.assertEqual(pipeline.output_dirs_for_task({}, config)[0], wx.PROJECT_DIR / "output")
        custom = {**config, "path_wechat_dir": "custom-wechat"}
        self.assertEqual(pipeline.output_dirs_for_task(task, custom)[0], wx.PROJECT_DIR / "custom-wechat")

    def test_full_flow_selects_exact_audio_and_no_wiki(self):
        for engine, expected in [("whisper", ["s1", "s3", "s4", "s5"]), ("qwen3-asr", ["s1", "s3", "s4", "s5"])]:
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                args = self.args("--subtitle-engine", engine)
                config = {"path_wechat_dir": directory, "s3_engine_suffix": {"mlx_whisper_large_v3": ".mlx_l3"}}
                task = pipeline.build_tasks(args, config)[0][0]
                video, audio, manifest = wx.artifact_paths(SHARE, root)
                (root / "unrelated.m4a").write_bytes(b"other")
                (root / f"{audio.stem}_wiki.md").write_text("unrelated wiki")

                def step(sid, name, cmd, *other):
                    if sid == "s1":
                        video.touch(); audio.touch()
                        manifest.write_text(json.dumps({"status": "success", "source_url": SHARE,
                            "audio_path": str(audio), "video_path": str(video), "completed_at": time.time()}))
                    elif sid == "s3":
                        self.assertEqual(cmd[2], str(audio))
                        suffix = ".qwen3_asr" if engine == "qwen3-asr" else ".mlx_l3"
                        audio.with_name(audio.stem + suffix + ".srt").write_text(SRT)
                    elif sid == "s4":
                        srt = Path(cmd[cmd.index("--subtitle") + 1])
                        srt.with_name(srt.stem + "_fix.srt").write_text(SRT)
                        self.assertIn("--no-transcript", cmd)
                    elif sid == "s5":
                        self.assertIn("--no-transcript", cmd)
                        self.assertIn("--subtitle-only", cmd)
                        self.assertNotIn("--transcript", cmd)
                    return True, None

                with patch.object(pipeline, "run_step", side_effect=step) as run:
                    self.assertTrue(pipeline.run_one_task(args, config, task, "test", "test", root))
                    self.assertEqual([c.args[0] for c in run.call_args_list], expected)

    def test_missing_or_stale_handoff_stops_before_asr(self):
        for stale in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                args = self.args()
                task = pipeline.build_tasks(args, {})[0][0]
                video, audio, manifest = wx.artifact_paths(SHARE, root)
                (root / "unrelated.m4a").touch()
                if stale:
                    video.touch(); audio.touch()
                    manifest.write_text(json.dumps({"status": "success", "source_url": SHARE,
                        "audio_path": str(audio), "video_path": str(video), "completed_at": 1}))
                with patch.object(pipeline, "run_step", return_value=(True, None)) as run:
                    self.assertFalse(pipeline.run_one_task(args, {"path_wechat_dir": directory}, task,
                                                         "test", "test", root))
                    self.assertEqual(run.call_count, 1)

    def test_s1_failure_stops_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args()
            task = pipeline.build_tasks(args, {})[0][0]
            with patch.object(pipeline, "run_step", return_value=(False, "download failed")) as run:
                self.assertFalse(pipeline.run_one_task(args, {"path_wechat_dir": directory}, task,
                                                     "test", "test", Path(directory)))
                self.assertEqual(run.call_count, 1)

    def test_artifact_resume_keeps_wechat_no_wiki_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            subtitle = Path(directory) / "video.qwen3_asr.srt"
            subtitle.write_text(SRT)
            args = self.args("--fix-subtitle", str(subtitle), "--print-plan")
            task, source = pipeline.build_tasks(args, {})
            plan, _, _ = pipeline.build_plan(args, {}, task[0], source, "resume")
            self.assertTrue(plan[0]["skip"])
            self.assertIn("--no-transcript", plan[-1]["cmd"])


class NoTranscriptTests(unittest.TestCase):
    def test_s4_no_transcript_ignores_config_and_neighbor_wiki(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = root / "video.mlx_l3.srt"
            srt.write_text(SRT)
            wiki = root / "video_wiki.md"
            wiki.write_text("must not read")
            original_read = Path.read_text

            def read_text(path, *args, **kwargs):
                if path == wiki:
                    raise AssertionError("must not read wiki")
                return original_read(path, *args, **kwargs)

            with patch.object(sys, "argv", ["s4_srt_fix.py", "--subtitle", str(srt),
                                            "--no-transcript", "--dry-run"]), \
                 patch.object(s4_srt_fix, "load_config", return_value=({"input": {"s4": {"transcript": "bad"}}}, {})), \
                 patch.object(s4_srt_fix, "load_custom_dict", return_value={}), \
                 patch.object(s4_srt_fix, "load_transcript", side_effect=AssertionError("must not load")), \
                 patch.object(Path, "read_text", read_text):
                s4_srt_fix.main()

    def test_s5_no_wiki_ignores_config_and_uses_noref_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = root / "wx_sample.qwen3_asr.srt"
            srt.write_text(SRT)
            (root / "wx_sample_wiki.md").write_text("must not read")
            argv = ["s5_addon.py", "--subtitle", str(srt), "--no-transcript", "--subtitle-only", "--no-prefer-fix"]
            config = {"input": {"s5": {"transcript": "wrong.md", "discussion": "wrong.xlsx"}}}
            item = {"chapter": "视频内容", "category": "观点", "time": "00:00:00",
                    "content": "先理解问题", "context": "分析之前", "correction": ""}
            client = MagicMock()
            client.chat.return_value = "# 摘要\n先理解问题"
            with patch.object(sys, "argv", argv), \
                 patch.object(s5_addon, "load_config", return_value=(config, {})), \
                 patch.object(s5_addon, "parse_transcript", side_effect=AssertionError("no wiki")), \
                 patch.object(s5_addon, "parse_discussion_xlsx", side_effect=AssertionError("no discussion")), \
                 patch.object(s5_addon, "LLMClient", return_value=client), \
                 patch.object(s5_addon, "request_json_items", return_value=[item]) as request:
                s5_addon.main()
            self.assertIn("没有 Wiki 文稿", request.call_args.args[1])
            self.assertIn("视频字幕", request.call_args.args[2])
            self.assertTrue((root / "wx_sample_信息拓展.md").exists())
            self.assertTrue((root / "wx_sample_精华摘要.md").exists())


if __name__ == "__main__":
    unittest.main()
