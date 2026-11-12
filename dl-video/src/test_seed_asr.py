"""豆包 Seed-ASR 2.0 Agent Plan WebSocket 与 SRT 输出测试。"""

import gzip
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from s3_subtitle import (
    _build_seed_asr_audio_request,
    _build_seed_asr_full_request,
    _build_seed_asr_headers,
    _parse_seed_asr_response,
    _seed_asr_api_key,
    _write_seed_asr_srt,
    transcribe_doubao_seed_asr,
)


def _server_response(payload: dict, sequence: int, is_last: bool = False) -> bytes:
    """按官方协议构造服务端响应；期望值不依赖生产代码。"""
    compressed = gzip.compress(json.dumps(payload).encode("utf-8"))
    flags = 0b0011 if is_last else 0b0001
    return (
        bytes([0x11, (0b1001 << 4) | flags, 0x11, 0x00])
        + struct.pack(">i", sequence)
        + struct.pack(">I", len(compressed))
        + compressed
    )


class _FakeWebSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.response = type("Handshake", (), {"headers": {"X-Tt-Logid": "log-123"}})()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        return self.responses.pop(0)


class _FakeProcess:
    def __init__(self, content: bytes):
        self.stdout = io.BytesIO(content)
        self.stderr = io.BytesIO()
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class SeedAsrTest(unittest.TestCase):
    def test_agent_plan_key_is_used_before_legacy_seed_asr_key(self):
        """防止 Agent Plan 调用继续读取普通语音服务 Key。"""
        key = _seed_asr_api_key({
            "volcengine-agentplan": {"api_key": "plan-key"},
            "doubao-seed-asr": {"api_key": "legacy-key"},
        })

        self.assertEqual(key, "plan-key")

    def test_headers_target_agent_plan_streaming_resource(self):
        """防止专属 Key 被发送到普通 AUC 资源。"""
        headers = _build_seed_asr_headers("plan-key", "request-123")

        self.assertEqual(headers, {
            "X-Api-Key": "plan-key",
            "X-Api-Resource-Id": "volc.seedasr.sauc.duration",
            "X-Api-Request-Id": "request-123",
            "X-Api-Connect-Id": "request-123",
            "X-Api-Sequence": "-1",
        })

    def test_full_request_declares_pcm_and_utterance_timestamps(self):
        """防止首包声明错误音频格式或漏掉 SRT 所需时间轴。"""
        packet = _build_seed_asr_full_request(1)

        self.assertEqual(packet[:4], bytes([0x11, 0x11, 0x11, 0x00]))
        self.assertEqual(struct.unpack(">i", packet[4:8])[0], 1)
        payload_size = struct.unpack(">I", packet[8:12])[0]
        payload = json.loads(gzip.decompress(packet[12:12 + payload_size]))
        self.assertEqual(payload["audio"], {
            "format": "pcm", "codec": "raw", "rate": 16000,
            "bits": 16, "channel": 1, "language": "zh-CN",
        })
        self.assertEqual(payload["request"]["model_name"], "bigmodel")
        self.assertTrue(payload["request"]["show_utterances"])

    def test_last_audio_request_has_negative_sequence_and_pcm_payload(self):
        """防止最后一包未正确结束流，导致服务端一直等待。"""
        packet = _build_seed_asr_audio_request(7, b"pcm-data", is_last=True)

        self.assertEqual(packet[:4], bytes([0x11, 0x23, 0x01, 0x00]))
        self.assertEqual(struct.unpack(">i", packet[4:8])[0], -7)
        payload_size = struct.unpack(">I", packet[8:12])[0]
        self.assertEqual(gzip.decompress(packet[12:12 + payload_size]), b"pcm-data")

    def test_final_server_response_is_parsed(self):
        """防止最终识别结果或结束标记在二进制解析中丢失。"""
        payload = {"result": {"utterances": [
            {"start_time": 0, "end_time": 1000, "text": "字幕"}
        ]}}

        parsed = _parse_seed_asr_response(_server_response(payload, -2, is_last=True))

        self.assertEqual(parsed["code"], 0)
        self.assertEqual(parsed["sequence"], -2)
        self.assertTrue(parsed["is_last"])
        self.assertEqual(parsed["payload"], payload)

    def test_video_streams_pcm_to_agent_plan_and_writes_srt(self):
        """防止视频路径回退到 Base64 录音文件接口。"""
        final_payload = {"result": {"utterances": [
            {"start_time": 0, "end_time": 1000, "text": "字幕"}
        ]}}
        websocket = _FakeWebSocket([
            _server_response({}, 1),
            _server_response(final_payload, -2, is_last=True),
        ])
        process = _FakeProcess(b"\x01\x02\x03\x04")
        popen_command = []

        def fake_popen(command, **kwargs):
            popen_command.extend(command)
            return process

        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "lesson.ts"
            video_path.write_bytes(b"video")
            with patch("s3_subtitle.websocket_connect", return_value=websocket) as connect, \
                 patch("s3_subtitle.subprocess.Popen", side_effect=fake_popen), \
                 patch("s3_subtitle._get_duration", return_value=0):
                output_path = transcribe_doubao_seed_asr(video_path, {
                    "volcengine-agentplan": {"api_key": "plan-key"}
                })

            self.assertEqual(output_path.name, "lesson.seed_asr.srt")
            self.assertIn("字幕", output_path.read_text(encoding="utf-8"))
            self.assertIn(str(video_path), popen_command)
            self.assertEqual(popen_command[-3:], ["-f", "s16le", "pipe:1"])
            self.assertEqual(connect.call_args.args[0],
                             "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_nostream")
            self.assertEqual(connect.call_args.kwargs["additional_headers"]["X-Api-Key"],
                             "plan-key")
            self.assertEqual(len(websocket.sent), 2)

    def test_completed_utterances_are_written_as_timed_srt(self):
        """防止成功响应仅保存全文，丢失字幕时间轴。"""
        result = {
            "result": {
                "utterances": [
                    {"start_time": 1250, "end_time": 3500, "text": "第一句"},
                    {"start_time": 4000, "end_time": 6200, "text": "第二句"},
                ]
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "lesson.m4a"
            output_path = _write_seed_asr_srt(audio_path, result)

            self.assertEqual(output_path.name, "lesson.seed_asr.srt")
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "1\n00:00:01,250 --> 00:00:03,500\n第一句\n\n"
                "2\n00:00:04,000 --> 00:00:06,200\n第二句\n\n",
            )


if __name__ == "__main__":
    unittest.main()
