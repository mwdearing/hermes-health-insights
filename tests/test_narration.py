"""Tests for health_insights.narration. Synthetic findings only; the local-model
HTTP call is faked with a tiny in-process server so these tests never depend on
the real model being up (a separate live check does that, outside pytest)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from health_insights import narration
from health_insights.concerns import Finding


def _f(id_, level, title="T", evidence="E", source="S", advice="A"):
    return Finding(id=id_, level=level, title=title, evidence=evidence, source=source, advice=advice)


class _FakeServer:
    """A throwaway HTTP server standing in for the local model's /v1/chat/completions."""

    def __init__(self, reply_text=None, status=200, malformed=False, hang_s=None):
        self.reply_text, self.status, self.malformed, self.hang_s = reply_text, status, malformed, hang_s
        self.received = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.received = json.loads(self.rfile.read(length))
                if outer.hang_s:
                    import time
                    time.sleep(outer.hang_s)
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if outer.malformed:
                    self.wfile.write(b"{not json")
                    return
                body = {"choices": [{"message": {"content": outer.reply_text or ""}}]}
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(autouse=True)
def _point_at_fake_server(monkeypatch, request):
    """Every test in this module gets a URL pointing at whatever fake server it
    starts, via the `server_url` fixture below; tests that don't use a server
    (deterministic_summary tests) don't need this at all."""
    yield


@pytest.fixture
def fake_model(monkeypatch):
    def _make(**kwargs):
        srv = _FakeServer(**kwargs)
        srv.__enter__()
        monkeypatch.setattr(narration, "LOCAL_MODEL_URL", f"http://127.0.0.1:{srv.port}/v1/chat/completions")
        request_cleanup.append(srv)
        return srv
    request_cleanup: list = []
    yield _make
    for srv in request_cleanup:
        srv.__exit__()


def test_deterministic_summary_no_findings():
    assert narration.deterministic_summary([]) == "Nothing stood out this period. Informational, not medical advice."


def test_deterministic_summary_includes_every_finding_and_ends_with_disclaimer():
    fs = [_f("a", 1, "Title A", "Evidence A"), _f("b", 3, "Title B", "Evidence B")]
    s = narration.deterministic_summary(fs)
    assert "Title A" in s and "Evidence A" in s and "Title B" in s and "Evidence B" in s
    assert s.endswith("Informational, not medical advice.")


def test_deterministic_summary_orders_by_level_then_id():
    fs = [_f("z_low", 1, title="Title Z"), _f("a_low", 1, title="Title A"), _f("m_hi", 3, title="Title M")]
    s = narration.deterministic_summary(fs)
    assert s.index("Title M") < s.index("Title A") < s.index("Title Z")


def test_narrate_uses_the_model_reply_when_it_succeeds(fake_model):
    fake_model(reply_text="Your resting heart rate has been a bit high. Informational, not medical advice.")
    fs = [_f("rhr_elevated", 1, "Elevated resting heart rate", "9 bpm above normal for 3 days")]
    out = narration.narrate(fs)
    assert "resting heart rate" in out.lower()
    assert out.count("Informational, not medical advice.") == 1


def test_narrate_appends_disclaimer_if_the_model_forgets_it(fake_model):
    fake_model(reply_text="Your resting heart rate has been a bit high.")
    out = narration.narrate([_f("x", 1)])
    assert out.endswith("Informational, not medical advice.")


def test_narrate_falls_back_on_http_error(fake_model):
    fake_model(status=500)
    fs = [_f("x", 2, "Title X", "Evidence X")]
    out = narration.narrate(fs)
    assert out == narration.deterministic_summary(fs)


def test_narrate_falls_back_on_malformed_json(fake_model):
    fake_model(malformed=True)
    fs = [_f("x", 1, "Title X", "Evidence X")]
    assert narration.narrate(fs) == narration.deterministic_summary(fs)


def test_narrate_falls_back_on_empty_completion(fake_model):
    fake_model(reply_text="")
    fs = [_f("x", 1, "Title X", "Evidence X")]
    assert narration.narrate(fs) == narration.deterministic_summary(fs)


def test_narrate_falls_back_on_timeout(fake_model):
    fake_model(hang_s=2)
    fs = [_f("x", 1, "Title X", "Evidence X")]
    assert narration.narrate(fs, timeout_s=0.2) == narration.deterministic_summary(fs)


def test_narrate_unreachable_server_falls_back(monkeypatch):
    monkeypatch.setattr(narration, "LOCAL_MODEL_URL", "http://127.0.0.1:1/v1/chat/completions")
    fs = [_f("x", 1, "Title X", "Evidence X")]
    assert narration.narrate(fs, timeout_s=2) == narration.deterministic_summary(fs)


def test_narrate_sends_only_finding_fields_never_extra_data(fake_model):
    srv = fake_model(reply_text="ok. Informational, not medical advice.")
    fs = [_f("x", 1, "Title X", "Evidence X", "Source X", "Advice X")]
    narration.narrate(fs)
    sent = json.loads(srv.received["messages"][1]["content"])
    assert sent == [{"id": "x", "level": 1, "title": "Title X", "evidence": "Evidence X", "source": "Source X", "advice": "Advice X"}]


def test_narrate_never_raises_on_empty_findings(fake_model):
    fake_model(reply_text="Nothing stood out. Informational, not medical advice.")
    assert narration.narrate([]) != ""


# --- loopback enforcement -------------------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/v1/chat/completions", "http://localhost:8080/v1", "http://[::1]:8080/v1", "http://127.1.2.3/v1",
])
def test_loopback_urls_are_accepted(url):
    narration._require_loopback(url)


@pytest.mark.parametrize("url", [
    "https://api.example.com/v1", "http://192.168.1.20:8080/v1", "http://10.0.0.5/v1", "http://127.0.0.1.evil.example/v1",
    "http://127.0.0.1@evil.example/v1", "http://localhost.evil.example/v1", "ftp://127.0.0.1/v1", "file:///etc/passwd", "127.0.0.1:8080", "",
])
def test_non_loopback_urls_are_refused_with_a_clear_error(url):
    with pytest.raises(ValueError, match="loopback"):
        narration._require_loopback(url)


def test_narrate_with_a_remote_url_never_makes_a_request_and_falls_back(monkeypatch):
    monkeypatch.setattr(narration, "LOCAL_MODEL_URL", "https://api.example.com/v1")
    monkeypatch.setattr(narration.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network used"))
    monkeypatch.setattr(narration, "_OPENER", type("O", (), {"open": lambda *a, **k: pytest.fail("network used")})())
    assert "Nothing stood out" in narration.narrate([])


def test_redirect_to_another_host_is_not_followed():
    import urllib.request
    handler = narration._NoRedirect()
    req = urllib.request.Request("http://127.0.0.1:1/v1")
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://evil.example/steal")
