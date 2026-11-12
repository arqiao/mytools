"""S4/S5 结构化 LLM 调用的离线回归测试。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import s4_srt_fix
import s5_addon


def build_config(step):
    provider = f"agentplan_seed21_{'lite' if step == 's4' else 'pro'}"
    return {
        "llm_plan": {
            f"current_{step}": {"name": provider, "temperature": 0.0},
            provider: {
                "model": f"doubao-seed-2.1-{'lite' if step == 's4' else 'pro'}",
                "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
                "max_tokens": 16384,
                "thinking": "disabled",
                "structured_output": True,
                "credential_source": "volcengine-agentplan",
            },
        }
    }


class StructuredOutputTest(unittest.TestCase):
    def test_parsers_accept_wrapper_and_legacy_array(self):
        for module in (s4_srt_fix, s5_addon):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.parse_llm_json('{"items": [{"x": 1}]}'), [{"x": 1}])
                self.assertEqual(module.parse_llm_json('[{"x": 1}]'), [{"x": 1}])

    def test_parsers_reject_empty_or_truncated_response(self):
        with tempfile.TemporaryDirectory() as directory:
            for module in (s4_srt_fix, s5_addon):
                with self.subTest(module=module.__name__), patch.object(
                    module, "LOG_DIR", Path(directory)
                ):
                    with self.assertRaises(module.LLMResponseError):
                        module.parse_llm_json("")
                    with self.assertRaises(module.LLMResponseError):
                        module.parse_llm_json('{"items": [')

    def test_clients_send_thinking_and_json_schema(self):
        for module, step, schema in (
            (s4_srt_fix, "s4", s4_srt_fix.S4_RESPONSE_SCHEMA),
            (s5_addon, "s5", s5_addon.S5_SUBTITLE_RESPONSE_SCHEMA),
        ):
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "choices": [{"finish_reason": "stop", "message": {"content": '{"items": []}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
            with self.subTest(module=module.__name__), patch.object(
                module.requests, "post", return_value=response
            ) as post:
                client = module.LLMClient(
                    build_config(step),
                    {"volcengine-agentplan": {"api_key": "test-key"}},
                )
                self.assertEqual(
                    client.chat("system", "user", response_schema=schema),
                    '{"items": []}',
                )
                payload = post.call_args.kwargs["json"]
                self.assertEqual(payload["thinking"], {"type": "disabled"})
                self.assertEqual(payload["response_format"]["type"], "json_schema")
                self.assertEqual(payload["temperature"], 0.0)

    def test_client_rejects_length_limited_response(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
            "usage": {},
        }
        with patch.object(s5_addon.requests, "post", return_value=response):
            client = s5_addon.LLMClient(
                build_config("s5"),
                {"volcengine-agentplan": {"api_key": "test-key"}},
            )
            with self.assertRaises(s5_addon.LLMResponseError):
                client.chat("system", "user")

    def test_empty_success_report_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.md"
            content = s5_addon.render_full_report(
                [{"subtitle": [], "discussion": []}],
                {
                    "total_subtitle": 0,
                    "total_discussion": 0,
                    "subtitle_categories": {},
                    "discussion_categories": {},
                    "top_chapters": [],
                },
                [("章节", "正文")],
                "课程",
                output,
            )
            self.assertIn("未发现可提取的增量信息", content)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
