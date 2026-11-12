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


class ResolverTests(unittest.TestCase):
    def test_official_new_chain_and_complete_signed_url(self):
        page = MagicMock()
        page.evaluate.side_effect = [
            {"status": 200, "data": {"code": 0, "data": {"wx_export_id": "export", "desc": "标题"}}},
            {"status": 200, "data": {"videoUrl": SIGNED}},
        ]
        result = wx._resolve_on_page(page, SHARE, 30)
        self.assertEqual(result["url"], SIGNED)
        self.assertEqual(page.evaluate.call_args_list[1].args[1]["body"], {"exportId": "export"})
        self.assertEqual(page.evaluate.call_args_list[1].args[1]["path"], "/api/findergetobjecturl")

    def test_empty_success_stops_before_object_request(self):
        page = MagicMock()
        page.evaluate.return_value = {"status": 200, "data": {"code": 0, "data": {"wx_export_id": ""}}}
        with self.assertRaisesRegex(wx.ChannelsError, "wx_export_id"):
            wx._resolve_on_page(page, SHARE, 30)
        self.assertEqual(page.evaluate.call_count, 1)

    def test_anonymous_200_is_not_login_and_binding_not_required(self):
        self.assertFalse(wx._is_logged_in({"id": "guest", "anonUser": {"isAnon": True}}))
        self.assertTrue(wx._is_logged_in({"userId": "account", "wxIsBind": False}))

    def test_transport_errors_do_not_disclose_credentials(self):
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError(SIGNED)
        with self.assertRaises(wx.ChannelsError) as caught:
            wx._request(page, "/api/findergetobjecturl", {})
        self.assertNotIn("secret", str(caught.exception))

    def test_bad_urls_are_rejected(self):
        for url in ("https://weixin.qq.com.evil/sph/abc", "https://weixin.qq.com/sph/abc/../../x", "not a url"):
            with self.subTest(url=url), self.assertRaises(wx.ChannelsError):
                wx.extract_share_url(url)

    def test_incomplete_or_html_download_never_promoted(self):
        for body, declared in [(b"fake HTML response", 18), (b"\0\0\0\x18ftypisom", 999)]:
            with tempfile.TemporaryDirectory() as directory:
                response = MagicMock()
                response.__enter__.return_value = response
                response.status_code = 200
                response.headers = {"Content-Length": str(declared)}
                response.iter_content.return_value = [body]
                target = Path(directory) / "video.mp4"
                with patch.object(wx.requests, "get", return_value=response), self.assertRaises(wx.ChannelsError):
                    wx._download_mp4(SIGNED, target, False)
                self.assertFalse(target.exists())

    def test_failed_audio_does_not_publish_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, manifest = wx.artifact_paths(SHARE, Path(directory))
            with patch.object(wx, "resolve_video", return_value={"url": SIGNED}), \
                 patch.object(wx, "_download_mp4", return_value=10), \
                 patch.object(wx, "extract_audio", return_value=False), self.assertRaises(wx.ChannelsError):
                wx.process_wechat_channels({"source_huifang_url": SHARE}, {"path_wechat_dir": directory})
            self.assertFalse(manifest.exists())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires local ffmpeg/ffprobe")
    def test_download_audio_validation_and_resume_with_real_local_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.mp4"
            subprocess.run([wx.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=1",
                            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(fixture)],
                           check=True, capture_output=True, timeout=30)
            content = fixture.read_bytes()
            response = MagicMock()
            response.__enter__.return_value = response
            response.status_code = 200
            response.headers = {"Content-Length": str(len(content))}
            response.iter_content.return_value = [content]
            config = {"path_wechat_dir": str(root / "output")}
            with patch.object(wx, "resolve_video", return_value={"url": SIGNED, "title": "测试"}), \
                 patch.object(wx.requests, "get", return_value=response) as get:
                result = wx.process_wechat_channels({"source_huifang_url": SHARE}, config)
                self.assertEqual(get.call_args.args[0], SIGNED)
            self.assertTrue(Path(result["audio_path"]).is_file())
            manifest = wx.artifact_paths(SHARE, root / "output")[2]
            self.assertNotIn("secret", manifest.read_text())
            self.assertNotIn("videoUrl", manifest.read_text())
            with patch.object(wx, "resolve_video", side_effect=AssertionError("resume must be offline")):
                resumed = wx.process_wechat_channels({"source_huifang_url": SHARE}, config)
            self.assertEqual(resumed["audio_path"], result["audio_path"])


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
