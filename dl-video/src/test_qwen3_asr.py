"""Qwen3-ASR 参数分派与字幕时间轴测试，不下载模型。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import s3_subtitle


class Qwen3AsrTest(unittest.TestCase):
    def test_flag_selects_qwen_without_whisper_or_cloud_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "lesson.wav"
            audio.touch()
            with patch.object(sys, "argv", ["s3_subtitle.py", str(audio), "--qwen3-asr"]), \
                 patch.object(s3_subtitle, "transcribe_qwen3_asr", create=True,
                              return_value=audio.with_suffix(".srt")) as qwen, \
                 patch.object(s3_subtitle, "transcribe_whisper") as whisper, \
                 patch.object(s3_subtitle, "load_config") as credentials:
                s3_subtitle.main()
            qwen.assert_called_once_with(audio, language=None, context="", diarize=False)
            whisper.assert_not_called()
            credentials.assert_not_called()

    def test_default_still_selects_whisper(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "lesson.wav"
            audio.touch()
            with patch.object(sys, "argv", ["s3_subtitle.py", str(audio)]), \
                 patch.object(s3_subtitle, "transcribe_qwen3_asr", create=True) as qwen, \
                 patch.object(s3_subtitle, "transcribe_whisper",
                              return_value=audio.with_suffix(".srt")) as whisper:
                s3_subtitle.main()
            whisper.assert_called_once_with(audio, None, "auto", False)
            qwen.assert_not_called()

    def test_qwen_options_reach_transcriber(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "lesson.wav"
            audio.touch()
            with patch.object(sys, "argv", ["s3_subtitle.py", str(audio), "--qwen3-asr",
                                            "--qwen3-language", "Chinese",
                                            "--qwen3-context", "剪映 keyframe",
                                            "--qwen3-diarize"]), \
                 patch.object(s3_subtitle, "transcribe_qwen3_asr",
                              return_value=audio.with_suffix(".srt")) as qwen:
                s3_subtitle.main()
            qwen.assert_called_once_with(audio, language="Chinese",
                                         context="剪映 keyframe", diarize=True)

    def _run_transcription(self, directory, result, *, language=None, context="",
                           diarize=False, grouped=None, recognize_effect=None):
        video = Path(directory) / "lesson.mp4"
        video.touch()
        prepared = Path(directory) / "prepared.wav"
        prepared.touch()
        recognize = Mock(return_value=result, side_effect=recognize_effect)
        group = Mock(return_value=grouped if grouped is not None else [
            {"start": 65.25, "end": 68.5, "text": "你好，世界。"}
        ])
        restore = Mock(side_effect=lambda words, text: words)
        fake_modules = {
            "mlx_qwen3_asr": SimpleNamespace(transcribe=recognize),
            "mlx_qwen3_asr.writers": SimpleNamespace(
                group_subtitle_segments=group, restore_punctuation=restore,
            ),
        }
        with patch.dict(sys.modules, fake_modules), \
             patch("modules.config_utils._secrets_load", return_value={
                 "huggingface": {"token": "synthetic-token"},
             }), \
             patch.object(s3_subtitle, "_resolve_qwen3_model", create=True,
                          side_effect=lambda repo, weights: repo), \
             patch.object(s3_subtitle, "_is_apple_silicon_mac", return_value=True), \
             patch.object(s3_subtitle, "_prepare_audio_for_whisper",
                          return_value=prepared) as prepare, \
             patch.object(s3_subtitle, "_get_duration", return_value=90), \
             patch.object(s3_subtitle, "get_engine_suffix", return_value=".qwen3_asr"):
            output = s3_subtitle.transcribe_qwen3_asr(
                video, language=language, context=context, diarize=diarize,
            )
        prepare.assert_called_once_with(video)
        self.assertFalse(prepared.exists(), "预处理音频应在结束后清理")
        return output, recognize, group, restore

    def test_video_requests_alignment_and_writes_grouped_srt(self):
        words = [{"start": 65.25, "end": 66.5, "text": "你好"},
                 {"start": 67, "end": 68.5, "text": "世界"}]
        result = SimpleNamespace(text="你好，世界。", language="Chinese",
                                 segments=words, truncated=False)
        with tempfile.TemporaryDirectory() as directory:
            output, recognize, group, restore = self._run_transcription(
                directory, result, language="Chinese", context="剪映 keyframe",
            )
            self.assertEqual(output.name, "lesson.qwen3_asr.srt")
            self.assertEqual(output.read_text(encoding="utf-8"),
                             "1\n00:01:05,250 --> 00:01:08,500\n你好，世界。\n\n")
            self.assertTrue(recognize.call_args.kwargs["return_timestamps"])
            self.assertTrue(recognize.call_args.kwargs["return_chunks"])
            self.assertIn("1.7", recognize.call_args.kwargs["model"])
            self.assertIn("ForcedAligner", recognize.call_args.kwargs["forced_aligner"])
            self.assertEqual(recognize.call_args.kwargs["language"], "Chinese")
            self.assertEqual(recognize.call_args.kwargs["context"], "剪映 keyframe")
            self.assertFalse(recognize.call_args.kwargs["diarize"])
            restore.assert_called_once_with(words, "你好，世界。")
            group.assert_called_once_with(
                words, language="Chinese", max_words=sys.maxsize,
                max_chars=sys.maxsize, max_duration_sec=float("inf"),
                max_gap_sec=5.0,
            )

    def test_diarized_srt_labels_each_speaker(self):
        words = [
            {"start": 0.0, "end": 0.8, "text": "你好", "speaker": "SPEAKER_00"},
            {"start": 1.0, "end": 1.8, "text": "世界", "speaker": "SPEAKER_01"},
        ]
        result = SimpleNamespace(text="你好世界", language="Chinese",
                                 segments=words, truncated=False)
        grouped = [
            {"start": 0.0, "end": 0.8, "text": "你好"},
            {"start": 1.0, "end": 1.8, "text": "世界"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output, recognize, _, _ = self._run_transcription(
                directory, result, diarize=True, grouped=grouped,
            )
            self.assertEqual(output.read_text(encoding="utf-8"),
                             "1\n00:00:00,000 --> 00:00:00,800\n说话人1：你好\n\n"
                             "2\n00:00:01,000 --> 00:00:01,800\n说话人2：世界\n\n")
            self.assertTrue(recognize.call_args.kwargs["diarize"])

    def test_missing_timestamps_does_not_create_fake_subtitles(self):
        result = SimpleNamespace(text="没有时间轴", language="Chinese",
                                 segments=None, truncated=False)
        with tempfile.TemporaryDirectory() as directory:
            output, _, group, _ = self._run_transcription(directory, result)
            self.assertIsNone(output)
            group.assert_not_called()
            self.assertFalse((Path(directory) / "lesson.qwen3_asr.srt").exists())

    def test_diarization_reads_saved_token_and_restores_environment_on_failure(self):
        def fail(*args, **kwargs):
            self.assertEqual(os.environ.get("PYANNOTE_AUTH_TOKEN"), "synthetic-token")
            raise RuntimeError("synthetic recognition failure")

        for initial in ({}, {"PYANNOTE_AUTH_TOKEN": ""}):
            with self.subTest(initial=initial), patch.dict(os.environ, initial, clear=True), \
                 tempfile.TemporaryDirectory() as directory:
                output, _, _, _ = self._run_transcription(
                    directory, None, diarize=True, recognize_effect=fail,
                )
                self.assertIsNone(output)
                self.assertEqual(os.environ.get("PYANNOTE_AUTH_TOKEN"),
                                 initial.get("PYANNOTE_AUTH_TOKEN"))

    def test_explicit_environment_skips_secret_file(self):
        for name in ("PYANNOTE_AUTH_TOKEN", "HUGGINGFACE_TOKEN", "HF_TOKEN"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "explicit"}, clear=True), \
                 patch.object(s3_subtitle, "load_huggingface_token") as saved, \
                 tempfile.TemporaryDirectory() as directory:
                self._run_transcription(directory, None, diarize=True,
                                        recognize_effect=RuntimeError("synthetic failure"))
                saved.assert_not_called()
                self.assertEqual(os.environ[name], "explicit")

    def test_plain_transcription_does_not_read_diarization_secret(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(s3_subtitle, "load_huggingface_token") as saved, \
             tempfile.TemporaryDirectory() as directory:
            self._run_transcription(directory, None, diarize=False,
                                    recognize_effect=RuntimeError("synthetic failure"))
            saved.assert_not_called()

    def test_truncated_recognition_is_not_reported_as_complete(self):
        result = SimpleNamespace(text="被截断", language="Chinese",
                                 segments=[{"start": 0, "end": 1, "text": "被截断"}],
                                 truncated=True)
        with tempfile.TemporaryDirectory() as directory:
            output, _, group, _ = self._run_transcription(directory, result)
            self.assertIsNone(output)
            group.assert_not_called()

    def test_punctuation_failure_in_one_chunk_keeps_other_chunks(self):
        result = SimpleNamespace(
            text="你好。世界真好。", language="Chinese",
            chunks=[{"start": 0, "end": 2, "text": "你好。"},
                    {"start": 2, "end": 6, "text": "世界真好。"}],
            segments=[{"start": 0, "end": 0.5, "text": "你"},
                      {"start": 0.5, "end": 1, "text": "好"},
                      {"start": 2, "end": 2.5, "text": "世"},
                      {"start": 2.5, "end": 3, "text": "错"},
                      {"start": 3, "end": 3.5, "text": "真"},
                      {"start": 3.5, "end": 4, "text": "好"}],
        )
        cues = s3_subtitle._group_qwen3_subtitles(result)
        self.assertEqual([cue["text"] for cue in cues], ["你好。", "世错真好。"])

    def test_punctuation_survives_a_wrong_aligned_character_inside_sentence(self):
        result = SimpleNamespace(
            text="今天，我们开始。", language="Chinese",
            chunks=[{"start": 0, "end": 7, "text": "今天，我们开始。"}],
            segments=[{"start": i, "end": i + 1, "text": c}
                      for i, c in enumerate("今天我们开错始")],
        )
        cues = s3_subtitle._group_qwen3_subtitles(result)
        self.assertEqual([cue["text"] for cue in cues], ["今天，我们开错始。"])

    def test_long_sentence_spanning_model_chunks_stays_one_cue(self):
        words = [{"start": i, "end": i + 1, "text": "我"} for i in range(61)]
        result = SimpleNamespace(
            text="我" * 61 + "。", language="Chinese", segments=words,
            chunks=[{"start": 0, "end": 30, "text": "我" * 30},
                    {"start": 30, "end": 61, "text": "我" * 31 + "。"}],
        )
        cues = s3_subtitle._group_qwen3_subtitles(result)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0], {"text": "我" * 61 + "。", "start": 0.0, "end": 61.0})


class Qwen3ModelCacheTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.check_dir = Path(directory.name)
        self.now = 2_000_000_000
        state_patch = patch.object(s3_subtitle, "QWEN3_UPDATE_CHECK_DIR",
                                   self.check_dir, create=True)
        clock_patch = patch.object(s3_subtitle.time, "time", return_value=self.now)
        state_patch.start()
        clock_patch.start()
        self.addCleanup(state_patch.stop)
        self.addCleanup(clock_patch.stop)

    def _check_file(self, repo):
        return self.check_dir / (repo.replace("/", "__") + ".json")

    def _record_check(self, repo, elapsed=0, revision="a" * 40):
        self._check_file(repo).write_text(json.dumps({
            "checked_at": self.now - elapsed, "revision": revision,
        }))

    def _cached_snapshot(self, cache, repo, weights=None, revision="a" * 40,
                         update_main=True):
        root = Path(cache) / ("models--" + repo.replace("/", "--"))
        snapshot = root / "snapshots" / revision
        snapshot.mkdir(parents=True, exist_ok=True)
        (root / "refs").mkdir(exist_ok=True)
        if update_main:
            (root / "refs" / "main").write_text(revision)
        (snapshot / "config.json").write_text("{}")
        if weights:
            for name in [weights, "tokenizer_config.json", "vocab.json", "merges.txt"]:
                (snapshot / name).write_text("fixture")
        return snapshot

    def test_complete_cache_is_reused_with_network_disabled(self):
        from huggingface_hub import constants

        with tempfile.TemporaryDirectory() as cache, \
             patch.object(constants, "HF_HUB_CACHE", cache), \
             patch("httpx.Client.send", side_effect=AssertionError("禁止联网")):
            for repo, weights in [
                (s3_subtitle.QWEN3_ASR_MODEL, "weights.safetensors"),
                (s3_subtitle.QWEN3_ALIGNER_MODEL, "model.safetensors"),
            ]:
                expected = self._cached_snapshot(cache, repo, weights)
                self._record_check(repo)
                resolved = s3_subtitle._resolve_qwen3_model(repo, weights)
                self.assertEqual(Path(resolved), expected)

    def test_missing_or_partial_cache_is_completed_before_use(self):
        from huggingface_hub import snapshot_download

        for partial in [False, True]:
            with self.subTest(partial=partial), tempfile.TemporaryDirectory() as cache:
                repo = s3_subtitle.QWEN3_ASR_MODEL
                if partial:
                    self._cached_snapshot(cache, repo)

                def download(repo_id, **kwargs):
                    if kwargs.get("local_files_only"):
                        return snapshot_download(repo_id, cache_dir=cache, local_files_only=True)
                    return str(self._cached_snapshot(cache, repo_id, "weights.safetensors"))

                with patch("huggingface_hub.snapshot_download", side_effect=download):
                    resolved = Path(s3_subtitle._resolve_qwen3_model(repo, "weights.safetensors"))
                self.assertTrue((resolved / "weights.safetensors").is_file())
                self.assertTrue((resolved / "tokenizer_config.json").is_file())
                self.assertEqual(json.loads(self._check_file(repo).read_text())["checked_at"],
                                 self.now)

    def test_incomplete_download_is_rejected(self):
        from huggingface_hub import snapshot_download

        with tempfile.TemporaryDirectory() as cache:
            def download(repo_id, **kwargs):
                if kwargs.get("local_files_only"):
                    return snapshot_download(repo_id, cache_dir=cache, local_files_only=True)
                return str(self._cached_snapshot(cache, repo_id))

            with patch("huggingface_hub.snapshot_download", side_effect=download):
                with self.assertRaisesRegex(RuntimeError, "不完整"):
                    s3_subtitle._resolve_qwen3_model(
                        s3_subtitle.QWEN3_ASR_MODEL, "weights.safetensors",
                    )

    def test_no_update_check_until_more_than_fourteen_days(self):
        from huggingface_hub import constants

        with tempfile.TemporaryDirectory() as cache, \
             patch.object(constants, "HF_HUB_CACHE", cache), \
             patch("huggingface_hub.HfApi.model_info") as api:
            repo = s3_subtitle.QWEN3_ASR_MODEL
            expected = self._cached_snapshot(cache, repo, "weights.safetensors")
            for elapsed in [13 * 86400, 14 * 86400]:
                with self.subTest(elapsed=elapsed):
                    self._record_check(repo, elapsed)
                    resolved = s3_subtitle._resolve_qwen3_model(repo, "weights.safetensors")
                    self.assertEqual(Path(resolved), expected)
            api.assert_not_called()

    def test_stale_unchanged_version_refreshes_check_time_without_downloading(self):
        from huggingface_hub import constants, snapshot_download

        with tempfile.TemporaryDirectory() as cache, \
             patch.object(constants, "HF_HUB_CACHE", cache):
            repo = s3_subtitle.QWEN3_ASR_MODEL
            expected = self._cached_snapshot(cache, repo, "weights.safetensors")
            self._record_check(repo, 14 * 86400 + 1)

            def offline_download(repo_id, **kwargs):
                self.assertTrue(kwargs.get("local_files_only"), "版本相同不应启动下载")
                return snapshot_download(repo_id, **kwargs)

            with patch("huggingface_hub.snapshot_download", side_effect=offline_download), \
                 patch("huggingface_hub.HfApi.model_info",
                       return_value=SimpleNamespace(sha="a" * 40)):
                resolved = s3_subtitle._resolve_qwen3_model(repo, "weights.safetensors")
            self.assertEqual(Path(resolved), expected)
            self.assertEqual(json.loads(self._check_file(repo).read_text()),
                             {"checked_at": self.now, "revision": "a" * 40})

    def test_new_revision_is_used_on_subsequent_offline_runs(self):
        from huggingface_hub import constants, snapshot_download

        with tempfile.TemporaryDirectory() as cache, \
             patch.object(constants, "HF_HUB_CACHE", cache):
            repo = s3_subtitle.QWEN3_ASR_MODEL
            self._cached_snapshot(cache, repo, "weights.safetensors")
            self._record_check(repo, 15 * 86400)

            def download(repo_id, **kwargs):
                if kwargs.get("local_files_only"):
                    return snapshot_download(repo_id, **kwargs)
                self.assertEqual(kwargs.get("revision"), "b" * 40)
                return str(self._cached_snapshot(
                    cache, repo_id, "weights.safetensors", "b" * 40, update_main=False,
                ))

            with patch("huggingface_hub.snapshot_download", side_effect=download), \
                 patch("huggingface_hub.HfApi.model_info",
                       return_value=SimpleNamespace(sha="b" * 40)):
                resolved = Path(s3_subtitle._resolve_qwen3_model(repo, "weights.safetensors"))
            self.assertEqual(resolved.name, "b" * 40)
            with patch("huggingface_hub.HfApi.model_info") as api, \
                 patch("httpx.Client.send", side_effect=AssertionError("禁止联网")):
                next_run = s3_subtitle._resolve_qwen3_model(repo, "weights.safetensors")
            self.assertEqual(Path(next_run), resolved)
            api.assert_not_called()

    def test_check_failure_uses_cache_and_preserves_last_success_time(self):
        from huggingface_hub import constants

        with tempfile.TemporaryDirectory() as cache, \
             patch.object(constants, "HF_HUB_CACHE", cache):
            repo = s3_subtitle.QWEN3_ASR_MODEL
            expected = self._cached_snapshot(cache, repo, "weights.safetensors")
            self._record_check(repo, 15 * 86400)
            original = self._check_file(repo).read_text()
            with patch("huggingface_hub.HfApi.model_info",
                       side_effect=ConnectionError("断网")) as api:
                resolved = s3_subtitle._resolve_qwen3_model(repo, "weights.safetensors")
            api.assert_called_once_with(repo, timeout=10)
            self.assertEqual(Path(resolved), expected)
            self.assertEqual(self._check_file(repo).read_text(), original)

    def test_first_use_of_existing_cache_checks_and_creates_record(self):
        from huggingface_hub import constants

        with tempfile.TemporaryDirectory() as cache, \
             patch.object(constants, "HF_HUB_CACHE", cache):
            repo = s3_subtitle.QWEN3_ASR_MODEL
            expected = self._cached_snapshot(cache, repo, "weights.safetensors")
            with patch("huggingface_hub.HfApi.model_info",
                       return_value=SimpleNamespace(sha="a" * 40)):
                resolved = s3_subtitle._resolve_qwen3_model(repo, "weights.safetensors")
            self.assertEqual(Path(resolved), expected)
            self.assertEqual(json.loads(self._check_file(repo).read_text()),
                             {"checked_at": self.now, "revision": "a" * 40})


if __name__ == "__main__":
    unittest.main()
