import importlib
import json
import urllib.error
from contextvars import Context
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO

import pytest

import deepseek_policy as policy


@pytest.fixture
def web(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGLISH_RECITER_DATA_DIR", str(tmp_path))
    module = importlib.import_module("simple_web_app")
    monkeypatch.setattr(module, "get_deepseek_api_key", lambda: "test-key")
    monkeypatch.setattr(module, "_ssl_context_for_https", lambda: None)
    monkeypatch.setattr(module, "DEEPSEEK_HTTP_RETRIES", 3)
    monkeypatch.setattr(module, "DEEPSEEK_RETRY_BACKOFF_SEC", 2)
    return module


def http_error(status, *, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://example.test/chat", status, "test error", headers,
        BytesIO(b'{"error":{"message":"test error"}}'),
    )


def response(content="[]"):
    return BytesIO(json.dumps({
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
    }).encode())


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422])
def test_permanent_http_failure_stops_after_one_request(web, monkeypatch, status):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise http_error(status)

    monkeypatch.setattr(web.urllib.request, "urlopen", fail)
    monkeypatch.setattr(web, "sleep", lambda _: pytest.fail("permanent errors must not retry"))
    with pytest.raises(policy.DeepSeekRequestError) as raised:
        web._deepseek_chat([], strict_errors=True)
    assert (raised.value.kind, raised.value.status_code, raised.value.retryable) == (
        "configuration", status, False,
    )
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [
    lambda: http_error(429),
    lambda: http_error(503),
    lambda: TimeoutError("timed out"),
    lambda: urllib.error.URLError("connection reset"),
])
def test_transient_failure_uses_bounded_http_retries(web, monkeypatch, failure):
    calls = []
    sleeps = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise failure()

    monkeypatch.setattr(web.urllib.request, "urlopen", fail)
    monkeypatch.setattr(web, "sleep", sleeps.append)
    with pytest.raises(policy.DeepSeekRequestError) as raised:
        web._deepseek_chat([], strict_errors=True)
    assert raised.value.kind == "transport"
    assert raised.value.retryable is True
    assert len(calls) == 3
    assert sleeps == [2, 4]


def test_missing_credentials_keep_legacy_return_and_strict_classification(web, monkeypatch):
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "")
    monkeypatch.setattr(web.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no credentials"))
    assert web._deepseek_chat([]) is None
    with pytest.raises(policy.DeepSeekRequestError, match="not configured") as raised:
        web._deepseek_chat([], strict_errors=True)
    assert raised.value.kind == "configuration"


@pytest.mark.parametrize("body", [
    b"{",
    b"\xff",
    b"[]",
    b'{"choices":[]}',
    b'{"choices":[{"message":{"content":""}}]}',
    b'{"choices":[{"message":{"content":null}}]}',
])
def test_malformed_api_envelope_is_response_format(web, monkeypatch, body):
    calls = []

    def reply(*args, **kwargs):
        calls.append(1)
        return BytesIO(body)

    monkeypatch.setattr(web.urllib.request, "urlopen", reply)
    with pytest.raises(policy.DeepSeekRequestError) as raised:
        web._deepseek_chat([], strict_errors=True)
    assert raised.value.kind == "response_format"
    assert len(calls) == 1


def test_retry_after_is_honored_before_next_http_attempt(web, monkeypatch):
    calls = []
    sleeps = []

    def reply(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise http_error(429, retry_after="5")
        return response()

    monkeypatch.setattr(web.urllib.request, "urlopen", reply)
    monkeypatch.setattr(web, "sleep", sleeps.append)
    result = web._deepseek_chat([], strict_errors=True)
    assert result == "[]"
    assert result.finish_reason == "stop"
    assert sleeps == [5]


def test_long_retry_after_defers_without_blocking(web, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise http_error(429, retry_after="120")

    monkeypatch.setattr(web.urllib.request, "urlopen", fail)
    monkeypatch.setattr(web, "sleep", lambda _: pytest.fail("long retry must defer"))
    with pytest.raises(policy.DeepSeekRequestError) as raised:
        web._deepseek_chat([], strict_errors=True)
    assert raised.value.retry_after_sec == 120
    assert raised.value.retryable is True
    assert len(calls) == 1


def test_peak_transition_blocks_http_retry_without_swallowing_pause(web, monkeypatch):
    moments = iter([
        datetime(2026, 9, 7, 0, 59, tzinfo=timezone.utc),
        datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc),
    ])
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("timed out")

    monkeypatch.setattr(web.urllib.request, "urlopen", fail)
    monkeypatch.setattr(web, "sleep", lambda _: None)
    with policy.off_peak_requests(clock=lambda: next(moments)), pytest.raises(policy.JobPaused):
        web._deepseek_chat([])
    assert len(calls) == 1
    policy.check_before_request()


def test_policy_is_context_local_and_nested_scope_cannot_disable_outer_guard():
    with policy.off_peak_requests(predicate=lambda: False):
        Context().run(policy.check_before_request)
        with policy.off_peak_requests(False), pytest.raises(policy.JobPaused):
            policy.check_before_request()
    policy.check_before_request()


def test_retry_after_parses_http_date_and_ignores_invalid_values():
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    assert policy.retry_after_seconds(
        {"Retry-After": "Mon, 07 Sep 2026 00:00:15 GMT"}, now=now,
    ) == 15
    for raw in ("invalid", "nan", "inf", "-inf"):
        assert policy.retry_after_seconds({"Retry-After": raw}) is None
    assert policy.retry_after_seconds({"Retry-After": "-1"}) == 0


def test_question_adapters_request_strict_errors(web, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "_deepseek_chat", lambda *args, **kwargs: calls.append(kwargs))
    web._gaokao_generation_chat([], 10)
    web._gaokao_audit_chat([], 10)
    assert all(call["strict_errors"] is True for call in calls)


@pytest.mark.parametrize("code,kind,attempts", [
    ("invalid_api_key", "configuration", 1),
    ("rate_limit_error", "transport", 3),
    ("unknown", "transport", 1),
])
def test_api_error_envelope_never_becomes_question_format_failure(web, monkeypatch, code, kind, attempts):
    calls = []

    def reply(*args, **kwargs):
        calls.append(1)
        return BytesIO(json.dumps({"error": {"type": code, "message": "test"}}).encode())

    monkeypatch.setattr(web.urllib.request, "urlopen", reply)
    monkeypatch.setattr(web, "sleep", lambda _: None)
    with pytest.raises(policy.DeepSeekRequestError) as raised:
        web._deepseek_chat([], strict_errors=True)
    assert raised.value.kind == kind
    assert len(calls) == attempts
