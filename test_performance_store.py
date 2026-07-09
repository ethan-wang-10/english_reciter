"""performance_store：JSONL 性能采集写入与脱敏测试。"""

import json
from pathlib import Path

from performance_store import (
    is_valid_performance_log_name,
    list_performance_logs,
    performance_log_dir,
    sanitize_value,
    sign_performance_log_name,
    verify_performance_share_signature,
    write_performance_events,
)


def test_write_performance_events_jsonl(tmp_path: Path) -> None:
    stored = write_performance_events(
        tmp_path,
        [
            {
                "type": "api",
                "path": "/api/words/review",
                "duration_ms": 1234.5678,
                "token": "secret-token",
            }
        ],
    )

    assert stored == 1
    logs = list(performance_log_dir(tmp_path).glob("perf-*.jsonl"))
    assert len(logs) == 1
    row = json.loads(logs[0].read_text(encoding="utf-8").strip())
    assert row["schema"] == "english_reciter.performance/v1"
    assert row["event"]["type"] == "api"
    assert row["event"]["duration_ms"] == 1234.568
    assert row["event"]["token"] == "[redacted]"


def test_performance_log_name_validation() -> None:
    assert is_valid_performance_log_name("perf-2026-06-07.jsonl")
    assert not is_valid_performance_log_name("../perf-2026-06-07.jsonl")
    assert not is_valid_performance_log_name("perf-latest.jsonl")


def test_sanitize_nested_sensitive_keys() -> None:
    clean = sanitize_value(
        {
            "headers": {"Authorization": "Bearer x", "X-Request-ID": "abc"},
            "payload": {"answer": "word"},
            "encoded_body_size": 42,
            "session_id": "perf-session",
            "message": "ok",
        }
    )

    assert clean["headers"]["Authorization"] == "[redacted]"
    assert clean["headers"]["X-Request-ID"] == "abc"
    assert clean["payload"]["answer"] == "[redacted]"
    assert clean["encoded_body_size"] == 42
    assert clean["session_id"] == "perf-session"
    assert clean["message"] == "ok"


def test_list_performance_logs(tmp_path: Path) -> None:
    write_performance_events(tmp_path, [{"type": "api"}])
    logs = list_performance_logs(tmp_path)
    assert len(logs) == 1
    assert logs[0]["name"].startswith("perf-")
    assert logs[0]["size_bytes"] > 0


def test_performance_share_signature() -> None:
    name = "perf-2026-06-07.jsonl"
    expires_at = 1_780_000_000
    sig = sign_performance_log_name(name, expires_at, "app-secret")

    assert verify_performance_share_signature(name, expires_at, sig, "app-secret", expires_at - 60)
    assert not verify_performance_share_signature(name, expires_at, sig, "wrong-secret", expires_at - 60)
    assert not verify_performance_share_signature(name, expires_at, sig, "app-secret", expires_at + 1)
    assert not verify_performance_share_signature("../perf-2026-06-07.jsonl", expires_at, sig, "app-secret", expires_at - 60)
