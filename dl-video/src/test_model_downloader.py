"""Whisper 的两周检查、旧缓存兼容和更新后加载测试，不下载模型。"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from tools import model_downloader as downloader
import s3_subtitle


class WhisperModelCacheTest(unittest.TestCase):
    def setUp(self):
        from huggingface_hub import constants

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.checks = self.root / "checks"
        self.checks.mkdir()
        self.now = 2_000_000_000
        for target, value in [
            ("tools.model_downloader.LOCAL_MODEL_ROOT", self.root / "local"),
            ("tools.model_downloader.MODEL_CHECK_DIR", self.checks),
            ("time.time", Mock(return_value=self.now)),
        ]:
            p = patch(target, value, create=True)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(constants, "HF_HUB_CACHE", str(self.root / "hub"))
        p.start()
        self.addCleanup(p.stop)

    def _model(self, path, backend):
        path.mkdir(parents=True, exist_ok=True)
        names = ["config.json", "weights.npz"] if backend == "mlx" else [
            "config.json", "tokenizer.json", "model.bin",
        ]
        for name in names:
            (path / name).write_bytes(b"{}" if name.endswith(".json") else b"weights")
        return path

    def _snapshot(self, repo, backend, revision="a" * 40):
        root = self.root / "hub" / ("models--" + repo.replace("/", "--"))
        path = self._model(root / "snapshots" / revision, backend)
        (root / "refs").mkdir(exist_ok=True)
        if not (root / "refs/main").exists():
            (root / "refs/main").write_text(revision)
        return path

    def _check_file(self, repo):
        return self.checks / (repo.replace("/", "__") + ".json")

    def _record(self, repo, elapsed, path=None):
        record = {"checked_at": self.now - elapsed, "revision": "a" * 40}
        if path:
            record["path"] = str(path)
        self._check_file(repo).write_text(json.dumps(record))

    def test_existing_models_are_checked_after_two_weeks_by_both_entrypoints(self):
        for backend, entrypoint in [("mlx", downloader.ensure_model),
                                    ("faster-whisper", downloader.download_model)]:
            with self.subTest(backend=backend):
                repo = downloader.get_repo_id("tiny", backend)
                expected = self._snapshot(repo, backend)
                self._record(repo, 14 * 86400 + 1)
                with patch("huggingface_hub.HfApi.model_info",
                           return_value=SimpleNamespace(sha="a" * 40)), \
                     patch.object(downloader, "snapshot_download",
                                  side_effect=AssertionError("版本相同不应下载")):
                    self.assertTrue(entrypoint("tiny", backend))
                self.assertEqual(json.loads(self._check_file(repo).read_text())["checked_at"],
                                 self.now)
                self.assertEqual(Path(downloader.get_model_path("tiny", backend)), expected)

    def test_recent_models_skip_network_including_fourteen_day_boundary(self):
        for backend in ["mlx", "faster-whisper"]:
            repo = downloader.get_repo_id("tiny", backend)
            self._snapshot(repo, backend)
            for elapsed in [13 * 86400, 14 * 86400]:
                with self.subTest(backend=backend, elapsed=elapsed):
                    self._record(repo, elapsed)
                    with patch("huggingface_hub.HfApi.model_info") as api, \
                         patch("httpx.Client.send", side_effect=AssertionError("禁止联网")):
                        self.assertTrue(downloader.download_model("tiny", backend))
                    api.assert_not_called()

    def test_new_revision_is_preferred_over_legacy_directory(self):
        repo = downloader.get_repo_id("tiny", "mlx")
        old = self._model(downloader._local_model_dir(repo), "mlx")
        self._record(repo, 15 * 86400, old)

        def download(repo_id, **kwargs):
            self.assertEqual(kwargs.get("revision"), "b" * 40)
            return str(self._snapshot(repo_id, "mlx", "b" * 40))

        with patch("huggingface_hub.HfApi.model_info",
                   return_value=SimpleNamespace(sha="b" * 40)), \
             patch.object(downloader, "snapshot_download", side_effect=download):
            self.assertTrue(downloader.ensure_model("tiny", "mlx"))
        self.assertEqual(Path(downloader.get_model_path("tiny", "mlx")).name, "b" * 40)
        self.assertEqual((old / "weights.npz").read_bytes(), b"weights")

    def test_old_unversioned_models_are_verified_without_redownloading(self):
        for backend in ["mlx", "faster-whisper"]:
            with self.subTest(backend=backend):
                repo = downloader.get_repo_id("tiny", backend)
                old = self._model(downloader._local_model_dir(repo), backend)
                siblings = []
                for file in old.iterdir():
                    data = file.read_bytes()
                    siblings.append(SimpleNamespace(
                        rfilename=file.name, size=len(data),
                        lfs=SimpleNamespace(sha256=hashlib.sha256(data).hexdigest())
                            if not file.name.endswith(".json") else None,
                        blob_id=hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest(),
                    ))
                with patch("huggingface_hub.HfApi.model_info",
                           return_value=SimpleNamespace(sha="a" * 40, siblings=siblings)), \
                     patch.object(downloader, "snapshot_download",
                                  side_effect=AssertionError("相同文件不应下载")):
                    self.assertTrue(downloader.download_model("tiny", backend))
                self.assertEqual(json.loads(self._check_file(repo).read_text())["revision"],
                                 "a" * 40)
                self.assertEqual(Path(downloader.get_model_path("tiny", backend)), old)

    def test_check_failure_preserves_local_model_and_last_success_time(self):
        repo = downloader.get_repo_id("tiny", "mlx")
        old = self._model(downloader._local_model_dir(repo), "mlx")
        self._record(repo, 15 * 86400, old)
        original = self._check_file(repo).read_text()
        with patch("huggingface_hub.HfApi.model_info", side_effect=ConnectionError("断网")) as api:
            self.assertTrue(downloader.ensure_model("tiny", "mlx"))
        api.assert_called_once_with(repo, timeout=10)
        self.assertEqual(self._check_file(repo).read_text(), original)
        self.assertEqual(Path(downloader.get_model_path("tiny", "mlx")), old)

    def test_http_fallback_downloads_pinned_revision_without_overwriting_old_model(self):
        repo = downloader.get_repo_id("tiny", "mlx")
        old = self._model(downloader._local_model_dir(repo), "mlx")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b"new weights"]
        info = SimpleNamespace(sha="b" * 40, siblings=[
            SimpleNamespace(rfilename="config.json"),
            SimpleNamespace(rfilename="weights.npz"),
        ])
        with patch("huggingface_hub.HfApi.model_info", return_value=info), \
             patch.object(downloader.requests, "get", return_value=response) as http:
            new = Path(downloader._download_with_requests(
                repo, "mlx", "https://example.test", revision="b" * 40,
            ))
        self.assertEqual(new.name, "b" * 40)
        self.assertEqual((new / "weights.npz").read_bytes(), b"new weights")
        self.assertEqual((old / "weights.npz").read_bytes(), b"weights")
        for call in http.call_args_list:
            self.assertIn("/resolve/" + "b" * 40 + "/", call.args[0])


class WhisperLoadingTest(unittest.TestCase):
    def test_both_transcribers_load_the_model_selected_by_update_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "lesson.mp3"
            audio.touch()
            selected = str(Path(directory) / "new-model")

            def mlx_transcribe(audio, **kwargs):
                self.assertEqual(kwargs["path_or_hf_repo"], selected)
                return {"segments": [{"start": 0, "end": 1, "text": "识别成功"}]}

            def faster_model(model, **kwargs):
                self.assertEqual(model, selected)
                return SimpleNamespace(transcribe=lambda *args, **kwargs: (
                    [SimpleNamespace(start=0, end=1, text="识别成功")], SimpleNamespace(),
                ))

            modules = {
                "mlx_whisper": SimpleNamespace(transcribe=mlx_transcribe),
                "faster_whisper": SimpleNamespace(WhisperModel=faster_model),
            }
            with patch.dict(sys.modules, modules), \
                 patch.object(downloader, "resolve_model_path", create=True, return_value=selected), \
                 patch.object(downloader, "get_model_path", return_value="old-model"), \
                 patch.object(downloader, "ensure_model", return_value=True), \
                 patch.object(s3_subtitle, "_get_duration", return_value=1), \
                 patch.object(s3_subtitle, "_should_prepare_for_whisper", return_value=False), \
                 patch.object(s3_subtitle, "_update_whisper_estimated_speed"):
                for transcribe in [s3_subtitle.transcribe_mlx_whisper,
                                   lambda audio, model: s3_subtitle.transcribe_faster_whisper(
                                       audio, model, force_cpu=True)]:
                    output = transcribe(audio, "tiny")
                    self.assertIsNotNone(output)
                    self.assertIn("识别成功", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
