"""集中 Hugging Face 凭据回归；仅用临时配置，不联网、不读真实 Token。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from modules import config_utils
import secrets_loader
import s3_speaker


class HuggingFaceAuthTest(unittest.TestCase):
    def test_saved_token_reaches_speaker_with_only_gtokens_loaded(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            Path(directory, "gtokens.yaml").write_text(
                "huggingface:\n  token: synthetic-saved-token\n", encoding="utf-8")
            with patch.object(secrets_loader, "SECRETS_DIR", Path(directory)), \
                 patch.object(config_utils, "_secrets_load", wraps=secrets_loader.load) as load:
                self.assertEqual(s3_speaker.resolve_token(None), "synthetic-saved-token")
                load.assert_called_once_with("gtokens")

    def test_command_line_and_environment_take_precedence(self):
        with patch.object(config_utils, "_secrets_load") as load:
            with patch.dict(os.environ, {"HF_TOKEN": "hf", "HUGGINGFACE_TOKEN": "huggingface",
                                         "PYANNOTE_AUTH_TOKEN": "pyannote"}, clear=True):
                self.assertEqual(s3_speaker.resolve_token("cli"), "cli")
                self.assertEqual(s3_speaker.resolve_token(None), "hf")
                del os.environ["HF_TOKEN"]
                self.assertEqual(s3_speaker.resolve_token(None), "huggingface")
                del os.environ["HUGGINGFACE_TOKEN"]
                self.assertEqual(s3_speaker.resolve_token(None), "pyannote")
            load.assert_not_called()

    def test_missing_or_empty_saved_token_stays_missing(self):
        for data in ({}, {"huggingface": "invalid"}, {"huggingface": {}},
                     {"huggingface": {"token": " "}}):
            with self.subTest(data=data), patch.dict(os.environ, {}, clear=True), \
                 patch.object(config_utils, "_secrets_load", return_value=data):
                self.assertIsNone(s3_speaker.resolve_token(None))


if __name__ == "__main__":
    unittest.main()
