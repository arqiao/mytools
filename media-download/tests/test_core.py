"""Offline tests: synthetic local media and fake transports only."""
import concurrent.futures
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from media_download import core as m
from media_download.cli import main

SHARE = "https://weixin.qq.com/sph/A3tC41vWaX"
URL = "https://finder.video.qq.com/media?token=SECRET&fileFormat=h264"
URL265 = "https://finder.video.qq.com/media?token=SECRET&fileFormat=h265"
PARSED = {"wx_export_id": "export", "desc": "test", "playable_url": "https://channels.weixin.qq.com/finder-preview/pages/feed?token=SECRET&eid=export"}
FEED = {"data": {"feedInfo": {"h264VideoInfo": {"videoUrl": URL}, "h265VideoInfo": {"videoUrl": URL265}}}}


def response(data=None, code=200, content=None, length=None):
    r = MagicMock()
    r.__enter__.return_value = r
    r.status_code = code
    r.json.return_value = data
    r.headers = {} if length is None else {"Content-Length": str(length)}
    data_stream = io.BytesIO(content or b"")
    r.raw.read.side_effect = lambda amount, **kw: data_stream.read(amount)
    return r


class ResolverTests(unittest.TestCase):
    def resolve(self, parsed=None, *, request=None, feed=None, **kw):
        return m._stream_from_parse(PARSED if parsed is None else parsed, SHARE,
            request or MagicMock(return_value={"videoUrl": URL}),
            feed or MagicMock(return_value=FEED), **kw)

    def test_share_and_media_source_strict(self):
        for url in ("https://weixin.qq.com.evil/sph/id", "https://weixin.qq.com/sph/id/../../x", "https://a@weixin.qq.com/sph/x", "https://weixin.qq.com:444/sph/x", "hello"):
            with self.subTest(url=url), self.assertRaises(m.ChannelsError):
                m.extract_share_url(url)
        for url in ("http://finder.video.qq.com/x", "https://finder.video.qq.com.evil/x", "https://a@finder.video.qq.com/x"):
            with self.subTest(url=url), self.assertRaises(m.ChannelsError):
                m._media_url(url)
        self.assertEqual(m.extract_share_url(SHARE + "?token=SECRET"), SHARE)

    def test_finder_success_preserves_signed_url(self):
        request, feed = MagicMock(return_value={"data": {"videoUrl": URL}}), MagicMock()
        result = self.resolve(request=request, feed=feed)
        self.assertEqual(result["url"], URL)
        request.assert_called_once_with("/api/findergetobjecturl", {"exportId": "export"})
        feed.assert_not_called()

    def test_feed_success_and_eid_export_fallback(self):
        parsed = {**PARSED, "playable_url": "https://channels.weixin.qq.com/finder-preview/pages/feed?token=SECRET"}
        self.assertEqual(m._token_eid(parsed), ("SECRET", "export"))
        request = MagicMock()
        result = self.resolve(parsed, route="feed", request=request)
        self.assertEqual(result["url"], URL)
        request.assert_not_called()

    def test_missing_finder_url_falls_back_once(self):
        request, feed = MagicMock(return_value={}), MagicMock(return_value=FEED)
        self.assertEqual(self.resolve(request=request, feed=feed)["url"], URL)
        request.assert_called_once(); feed.assert_called_once()

    def test_missing_export_can_use_feed(self):
        self.assertEqual(self.resolve({"playable_url": PARSED["playable_url"]})["url"], URL)

    def test_missing_token_and_missing_feed_fields(self):
        for parsed, route, feed in (({}, "feed", FEED), ({"wx_export_id": "x"}, "feed", FEED), (PARSED, "feed", {})):
            with self.subTest(parsed=parsed), self.assertRaises(m.ChannelsError):
                self.resolve(parsed, route=route, feed=MagicMock(return_value=feed))

    def test_changed_field_types_and_missing_auth(self):
        for payload in ({"data": {"wx_export_id": 12}}, {"data": {"playable_url": []}}):
            with self.assertRaises(m.ChannelsError) as caught:
                m._parsed_data(payload)
            self.assertEqual(caught.exception.code, "schema_changed")
        with self.assertRaises(m.ChannelsError) as caught:
            m._media_url({"secret": "SECRET"})
        self.assertEqual(caught.exception.code, "schema_changed")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertFalse(m._is_logged_in({"userId": {"unexpected": True}}))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(m.ChannelsError) as caught:
            m.resolve_video(SHARE, {"browser_profile": str(Path(tmp) / "missing")})
        self.assertEqual(caught.exception.code, "auth_missing")

    def test_parse_empty_differs_from_schema_changed(self):
        for payload, code in (({"data": {}}, "parse_empty"), ({}, "schema_changed"), ({"data": []}, "schema_changed")):
            with self.subTest(payload=payload), self.assertRaises(m.ChannelsError) as caught:
                m._parsed_data(payload)
            self.assertEqual(caught.exception.code, code)

    def test_codec_reaches_stream_selection(self):
        request = MagicMock()
        self.assertEqual(self.resolve(codec="h265", request=request)["url"], URL265)
        request.assert_not_called()
        with self.assertRaises(m.ChannelsError) as caught:
            self.resolve(codec="h265", feed=MagicMock(return_value={"data": {"feedInfo": {"h264VideoInfo": {"videoUrl": URL}}}}))
        self.assertEqual(caught.exception.code, "codec_unavailable")

    def test_auth_permission_rate_limit_no_route_switch(self):
        for code in ("auth_expired", "permission_denied", "rate_limited", "unsafe_media_url"):
            feed = MagicMock()
            with self.subTest(code=code), self.assertRaises(m.ChannelsError):
                self.resolve(request=MagicMock(side_effect=m.ChannelsError("safe", code)), feed=feed)
            feed.assert_not_called()

    def test_feed_auth_permission_terminal(self):
        for code in (401, 403):
            session = MagicMock()
            session.request.return_value = response({"message": "SECRET"}, code)
            with self.subTest(code=code), self.assertRaises(m.ChannelsError) as caught:
                m._http_json(session, "https://channels.weixin.qq.com/api", body={})
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertEqual(session.request.call_count, 1)

    def test_http_response_categories_no_secrets(self):
        for status, payload, code in ((401, {}, "auth_expired"), (403, {}, "permission_denied"), (200, {"code": 401}, "auth_expired"),
                (200, {"baseResp": {"ret": 403}}, "permission_denied"), (200, [], "schema_changed"), (200, {"code": 7, "message": URL}, "business_error")):
            with self.subTest(code=code), self.assertRaises(m.ChannelsError) as caught:
                m._response(status, payload)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_feed_accepts_201_and_sends_no_yuanbao_cookie(self):
        session = MagicMock()
        session.request.return_value = response(FEED, 201)
        session.__enter__.return_value = session
        with patch.object(m.requests, "Session", return_value=session):
            data = m._feed_request("SECRET", "export", m.normalized_options(), m.new_stats())
        self.assertEqual(data, FEED)
        self.assertNotIn("Cookie", session.request.call_args.kwargs["headers"])
        session.cookies.set.assert_not_called()
        with self.assertRaises(m.ChannelsError):
            m._response(201, {"userId": "a"})

    def test_network_and_server_retries_bounded(self):
        for failure in (m.requests.Timeout(URL), response({}, 500)):
            session = MagicMock()
            if isinstance(failure, Exception):
                session.request.side_effect = failure
            else:
                session.request.return_value = failure
            stats = m.new_stats()
            with self.subTest(failure=type(failure).__name__), self.assertRaises(m.ChannelsError) as caught:
                m._http_json(session, "https://example.test", retries=1, stats=stats)
            self.assertEqual(stats["api_requests"], 2)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_anonymous_200_not_authenticated(self):
        self.assertFalse(m._is_logged_in({"id": "guest", "anonUser": {"isAnon": True}}))
        self.assertTrue(m._is_logged_in({"userId": "a", "wxIsBind": False}))

    def test_memory_cookie_domains_http_first(self):
        context = MagicMock()
        context.cookies.return_value = [dict(name="hy_token", value="SECRET", domain=".tencent.com", path="/"),
            dict(name="evil", value="SECRET", domain="evil.tencent.com", path="/")]
        with m.requests.Session() as session, patch.object(m, "_http_json", return_value={"userId": "a"}):
            self.assertEqual(m._http_user(session, context, m.normalized_options(), m.new_stats()), "a")
            self.assertEqual([c.name for c in session.cookies], ["hy_token"])
        context.new_page.assert_not_called()

    def test_browser_fallback_once_only_on_recoverable_failure(self):
        for code, fallback in (("schema_changed", True), ("network_error", True), ("auth_expired", False), ("permission_denied", False), ("parse_empty", False)):
            stats = m.new_stats()
            page = MagicMock()
            with patch.object(m, "_http_user", side_effect=m.ChannelsError("safe", code)), \
                 patch.object(m, "_browser_user", return_value=page) as browser, \
                 patch.object(m, "_request", side_effect=[{"data": PARSED}, {"videoUrl": URL}]):
                if fallback:
                    result = m._resolve_context(MagicMock(), SHARE, m.normalized_options(), stats)
                    self.assertIn("browser", result["resolved_by"])
                    browser.assert_called_once()
                else:
                    with self.assertRaises(m.ChannelsError):
                        m._resolve_context(MagicMock(), SHARE, m.normalized_options(), stats)
                    browser.assert_not_called()

    def test_browser_failure_no_recursion(self):
        with patch.object(m, "_http_user", side_effect=m.ChannelsError("safe", "schema_changed")), \
             patch.object(m, "_browser_user", side_effect=m.ChannelsError("safe", "network_error")) as browser:
            with self.assertRaises(m.ChannelsError):
                m._resolve_context(MagicMock(), SHARE, m.normalized_options(), m.new_stats())
            browser.assert_called_once()

    def test_browser_errors_sanitized(self):
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError(URL)
        with self.assertRaises(m.ChannelsError) as caught:
            m._request(page, "/api/findergetobjecturl", {})
        self.assertNotIn("SECRET", str(caught.exception))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.temp.name) / "fixture.mp4"
        cls.silent = Path(cls.temp.name) / "silent.mp4"
        base = [m.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=10:d=3"]
        subprocess.run(base + ["-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(cls.fixture)], check=True, capture_output=True)
        subprocess.run(base + ["-c:v", "libx264", "-movflags", "+faststart", str(cls.silent)], check=True, capture_output=True)
        cls.content = cls.fixture.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.output = self.root / "output"
        self.options = {"state_dir": str(self.root / "state"), "extract_audio": True}

    def tearDown(self):
        self.tempdir.cleanup()

    def run_download(self, content=None, **options):
        content = self.content if content is None else content
        with patch.object(m, "resolve_video", return_value={"url": URL, "resolved_by": "test/finder"}), \
             patch.object(m.requests, "get", return_value=response(content=content, length=len(content))) as get:
            result = m.download(SHARE, self.output, {**self.options, **options})
        return result, get

    def test_download_extract_repeat_status_no_new_download(self):
        result, get = self.run_download()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["sha256"], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(result["codec"], "h264")
        self.assertTrue(result["verification"]["full_decode"])
        self.assertEqual(get.call_count, 1)
        with patch.object(m, "resolve_video", side_effect=AssertionError("no network")):
            again = m.download(SHARE, self.output, self.options)
            status = m.status(SHARE, self.output, self.options)
        self.assertEqual(again["status"], "success")
        self.assertTrue(again["reused"])
        self.assertEqual(again["activity"]["download_attempts"], 0)
        self.assertEqual(status["status"], "success")
        self.assertNotIn("SECRET", json.dumps(result))
        for path in self.root.rglob("*.json"):
            self.assertNotIn("SECRET", path.read_text())

    def test_truncated_faststart_rejected_no_publish(self):
        result, _ = self.run_download(self.content[:len(self.content)//2])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "truncated_media")
        self.assertFalse(m.artifact_paths(SHARE, self.output)[0].exists())
        self.assertFalse(m.artifact_paths(SHARE, self.output)[2].exists())

    def test_missing_audio_optional_or_required(self):
        result, _ = self.run_download(self.silent.read_bytes())
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["audio_status"], "absent")
        self.assertIsNone(result["audio_path"])
        required = m.download(SHARE, self.output, {**self.options, "require_audio": True})
        self.assertEqual(required["error_code"], "audio_absent")

    def test_wrong_actual_codec_not_published(self):
        result, _ = self.run_download(codec="h265")
        self.assertEqual(result["error_code"], "codec_mismatch")
        self.assertFalse(m.artifact_paths(SHARE, self.output)[0].exists())

    def test_html_length_limit_redirect_rejected(self):
        cases = [(response(content=b"<html>fake response</html>"), "invalid_media"),
                 (response(content=self.content, length=len(self.content)+1), "length_mismatch"),
                 (response(content=self.content, length=100000000), "byte_limit"),
                 (response(content=self.content, code=302), "media_redirect")]
        for r, code in cases:
            with self.subTest(code=code), patch.object(m.requests, "get", return_value=r):
                with self.assertRaises(m.ChannelsError) as caught:
                    m._download_mp4(URL, self.root / (code+".mp4"))
                self.assertEqual(caught.exception.code, code)
                self.assertFalse((self.root / (code+".mp4")).exists())

    def test_unknown_length_cap_and_failure_recovery(self):
        with patch.object(m, "resolve_video", return_value={"url": URL}), patch.object(m.requests, "get", return_value=response(content=self.content)):
            failed = m.download(SHARE, self.output, {**self.options, "max_bytes": 100})
        self.assertEqual(failed["error_code"], "byte_limit")
        self.assertEqual(m.status(SHARE, self.output)["status"], "incomplete")
        good, _ = self.run_download()
        self.assertEqual(good["status"], "success", good)

    def test_file_and_manifest_conflicts_never_overwrite(self):
        self.output.mkdir()
        video, _, manifest = m.artifact_paths(SHARE, self.output)
        video.write_bytes(b"bad historical file")
        with patch.object(m, "resolve_video", side_effect=AssertionError("must not fetch")):
            self.assertEqual(m.download(SHARE, self.output, self.options)["status"], "failed")
        self.assertEqual(video.read_bytes(), b"bad historical file")
        video.write_bytes(self.content)
        manifest.write_text(json.dumps({"status": "success", "source_url": "https://weixin.qq.com/sph/OTHER", "video_path": str(video)}))
        self.assertEqual(m.download(SHARE, self.output, self.options)["error_code"], "manifest_conflict")
        self.assertEqual(video.read_bytes(), self.content)

    def test_legacy_file_without_manifest_reused(self):
        self.output.mkdir()
        video, _, _ = m.artifact_paths(SHARE, self.output)
        video.write_bytes(self.content)
        before = video.stat().st_mtime_ns
        with patch.object(m, "resolve_video", side_effect=AssertionError("no fetch")):
            result = m.download(SHARE, self.output, self.options)
        self.assertEqual(result["status"], "success", result)
        self.assertTrue(result["reused"])
        self.assertEqual(video.stat().st_mtime_ns, before)

    def test_concurrent_same_asset_only_one_network_download(self):
        def fake_get(*args, **kw):
            time.sleep(.2)
            return response(content=self.content, length=len(self.content))
        with patch.object(m, "resolve_video", return_value={"url": URL}), patch.object(m.requests, "get", side_effect=fake_get) as get:
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(m.download, SHARE, self.output, self.options) for _ in range(2)]
                results = [f.result() for f in futures]
        self.assertTrue(all(r["status"] == "success" for r in results), results)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(sorted(r["reused"] for r in results), [False, True])

    def test_lock_timeout_and_no_overwrite_publish(self):
        with m.asset_lock(SHARE, self.output, state_dir=self.root / "state"):
            result = m.download(SHARE, self.output, {**self.options, "lock_timeout": .1})
        self.assertEqual(result["error_code"], "asset_busy")
        target, partial = self.root / "target", self.root / "partial"
        target.write_bytes(b"original"); partial.write_bytes(b"new")
        with self.assertRaises(m.ChannelsError):
            m._publish(partial, target)
        self.assertEqual(target.read_bytes(), b"original")

    def test_audio_failure_and_recovery_without_redownload(self):
        with patch.object(m, "extract_audio", side_effect=m.ChannelsError("safe", "audio_extract_failed")):
            failed, _ = self.run_download()
        self.assertEqual(failed["error_code"], "audio_extract_failed")
        self.assertFalse(m.artifact_paths(SHARE, self.output)[2].exists())
        with patch.object(m, "resolve_video", side_effect=AssertionError("no network")):
            result = m.download(SHARE, self.output, self.options)
        self.assertEqual(result["status"], "success", result)
        self.assertTrue(result["reused"])

    def test_status_read_only_and_missing(self):
        before = list(self.root.rglob("*"))
        with patch.object(m, "resolve_video", side_effect=AssertionError("no network")):
            result = m.status(SHARE, self.output, self.options)
        self.assertEqual(result["status"], "missing")
        self.assertEqual(list(self.root.rglob("*")), before)

    def test_same_size_damaged_payload_is_rejected(self):
        damaged = bytearray(self.content)
        pos = damaged.index(b"mdat") + 4
        damaged[pos:pos+2000] = b"\0" * 2000
        result, _ = self.run_download(bytes(damaged))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(m.artifact_paths(SHARE, self.output)[0].exists())

    def test_actual_h265_positive(self):
        fixture = self.root / "hevc.mp4"
        subprocess.run([m.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=1",
            "-c:v", "libx265", "-x265-params", "log-level=error", str(fixture)], check=True, capture_output=True)
        result, _ = self.run_download(fixture.read_bytes(), codec="h265")
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["codec"], "h265")
        self.assertEqual(result["audio_status"], "absent")

    def test_audio_missing_status_and_repair(self):
        result, _ = self.run_download()
        Path(result["audio_path"]).unlink()
        snapshot = m.status(SHARE, self.output, self.options)
        self.assertEqual(snapshot["status"], "incomplete")
        self.assertEqual(snapshot["audio_status"], "missing")
        with patch.object(m, "resolve_video", side_effect=AssertionError("no fetch")):
            restored = m.download(SHARE, self.output, self.options)
        self.assertEqual(restored["status"], "success", restored)
        self.assertEqual(restored["activity"]["download_attempts"], 0)

    def test_audio_duration_mismatch_blocks_success(self):
        self.output.mkdir()
        video, audio, _ = m.artifact_paths(SHARE, self.output)
        video.write_bytes(self.content)
        subprocess.run([m.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=duration=10",
            "-c:a", "aac", str(audio)], check=True, capture_output=True)
        result = m.download(SHARE, self.output, self.options)
        self.assertEqual(result["error_code"], "duration_mismatch")

    def test_receiving_stops_at_cap_and_declared_size_prevents_body_read(self):
        stats = m.new_stats()
        r = response(content=self.content)
        with patch.object(m.requests, "get", return_value=r), self.assertRaises(m.ChannelsError):
            m._download_mp4(URL, self.root / "capped.mp4", options={"max_bytes": 100}, stats=stats)
        self.assertEqual(stats["received_bytes"], 100)
        self.assertEqual(stats["written_bytes"], 100)
        r = response(content=self.content, length=len(self.content))
        with patch.object(m.requests, "get", return_value=r), self.assertRaises(m.ChannelsError):
            m._download_mp4(URL, self.root / "refused.mp4", options={"max_bytes": 100})
        r.raw.read.assert_not_called()

    def test_interrupted_stream_receipt_is_safe_and_recoverable(self):
        r = response(content=self.content)
        r.raw.read.side_effect = m.HTTPStreamError(URL)
        with patch.object(m, "resolve_video", return_value={"url": URL}), patch.object(m.requests, "get", return_value=r):
            result = m.download(SHARE, self.output, self.options)
        self.assertEqual(result["error_code"], "download_interrupted")
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertFalse(m.artifact_paths(SHARE, self.output)[2].exists())

    def test_hash_conflict_detected(self):
        result, _ = self.run_download()
        manifest = m.artifact_paths(SHARE, self.output)[2]
        data = json.loads(manifest.read_text()); data["sha256"] = "wrong"
        manifest.write_text(json.dumps(data))
        self.assertEqual(m.download(SHARE, self.output, self.options)["error_code"], "file_conflict")

    def test_invalid_options_and_url_safe_receipt(self):
        self.assertEqual(m.download("https://evil/SECRET", self.output)["source_url"], None)
        self.assertEqual(m.download(SHARE, self.output, {"max_bytes": 0})["error_code"], "invalid_options")


if __name__ == "__main__":
    unittest.main()
