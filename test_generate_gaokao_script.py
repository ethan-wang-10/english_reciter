"""Isolated checks for refresh selection and request interruption."""

from __future__ import annotations

import os
import sys
import tempfile

import pytest


_WEB_DATA_DIR = None
if "simple_web_app" not in sys.modules:
    _WEB_DATA_DIR = tempfile.TemporaryDirectory(prefix="english-reciter-refresh-test-")
    os.environ["ENGLISH_RECITER_DATA_DIR"] = _WEB_DATA_DIR.name

import deepseek_policy
import gaokao_questions as questions
import simple_web_app as web
from scripts import generate_gaokao_questions as script


@pytest.fixture
def isolated_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(questions, "QUESTION_BANK_FILE", tmp_path / "questions.json")
    monkeypatch.setattr(questions, "QUESTION_BANK_LOCK_FILE", tmp_path / ".questions.lock")
    monkeypatch.setattr(questions, "_cache", None)
    monkeypatch.setattr(questions, "_cache_mtime_ns", -1)
    monkeypatch.setattr(web.gaokao_backfill, "GENERATION_LOCK_FILE", tmp_path / ".job.lock")
    monkeypatch.setattr(web, "get_deepseek_api_key", lambda: "test-key")
    monkeypatch.setattr(web, "_deepseek_chat", lambda *args, **kwargs: pytest.fail("unexpected API request"))
    return tmp_path


def _source(key: str) -> dict:
    return {
        "english": key,
        "chinese": "n. 益处",
        "level": "高中",
        "phonetic": f"/{key}/",
        "pos": "n",
        "context_sentence": "Regular exercise provides a ____ for every person's health.",
        "context_answer": key,
        "context_cn": "定期锻炼对每个人的健康都有益处。",
        "source_hash": f"hash-{key}",
    }


def test_force_refresh_never_plans_generation_for_unusable_history(
    isolated_refresh, monkeypatch, capsys,
):
    sources = [_source("legacy"), _source("candidate"), _source("rejected"), _source("new")]
    bank = questions.empty_bank()
    bank["questions"]["legacy"] = {"word_key": "legacy", "source_hash": "old-source"}
    bank["candidates"]["candidate"] = {"source": {"english": "candidate", "source_hash": "old-source"}}
    bank["rejections"]["rejected"] = {"error": "incomplete generation response"}
    questions._write_bank_unlocked(bank)
    revision = questions._question_store().revision()
    monkeypatch.setattr(script, "_sources", lambda level: sources)
    monkeypatch.setattr(sys, "argv", ["generate", "--force", "--retry-failed", "--dry-run", "--limit", "0"])

    assert script.main() == 0

    output = capsys.readouterr().out
    assert "需要生成=1" in output
    assert "generate new" in output
    assert "generate legacy" not in output
    assert "generate candidate" not in output
    assert "generate rejected" not in output
    assert questions._question_store().revision() == revision


def test_retry_failed_is_explicitly_forwarded_to_selection_and_execution(
    isolated_refresh, monkeypatch,
):
    source = _source("benefit")
    monkeypatch.setattr(script, "_sources", lambda level: [source])
    calls = []

    def select(sources, limit=0, retry_failed=False, **kwargs):
        calls.append(("select", retry_failed))
        return list(sources)

    def generate(sources, **kwargs):
        calls.append(("generate", kwargs["retry_failed"]))
        return {"generated": 1, "failed": 0}

    monkeypatch.setattr(questions, "sources_needing_prompt_refresh", select)
    monkeypatch.setattr(web, "generate_gaokao_question_batches", generate)
    monkeypatch.setattr(sys, "argv", ["generate", "--retry-failed"])

    assert script.main() == 0
    assert calls == [("select", True), ("generate", True)]


def test_all_deduplicates_sources_and_shares_limit_without_repeating_attempts(
    isolated_refresh, monkeypatch,
):
    sources = [_source("first"), _source("first"), _source("second"), _source("third")]
    monkeypatch.setattr(script, "_sources", lambda level: sources)
    selected = []

    def generate(sources, **kwargs):
        selected.extend(source["english"] for source in sources)
        return 0, len(sources)

    monkeypatch.setattr(script, "_run_generation", generate)
    monkeypatch.setattr(script, "_run_audit", lambda *args, **kwargs: pytest.fail("shared limit exhausted"))
    monkeypatch.setattr(sys, "argv", ["generate", "--stage", "all", "--limit", "2"])

    assert script.main() == 2
    assert selected == ["first", "second"]


@pytest.mark.parametrize(
    "error, expected_code, expected_message",
    [
        (deepseek_policy.JobPaused("off-peak period ended"), 4, "已进入高峰时段"),
        (deepseek_policy.DeepSeekRequestError("configuration", "HTTP 401: check API key"), 2, "HTTP 401"),
        (deepseek_policy.DeepSeekRequestError("transport", "network timeout", retryable=True), 2, "network timeout"),
    ],
)
def test_service_errors_stop_once_without_marking_words_failed(
    isolated_refresh, monkeypatch, capsys, error, expected_code, expected_message,
):
    source = _source("benefit")
    monkeypatch.setattr(script, "_sources", lambda level: [source])
    attempts = []

    def generate(sources, **kwargs):
        attempts.append([source["english"] for source in sources])
        kwargs["diagnostic"]({"event": "request"})
        raise error

    monkeypatch.setattr(web, "generate_gaokao_question_batches", generate)
    monkeypatch.setattr(sys, "argv", ["generate", "--stage", "all"])

    assert script.main() == expected_code
    assert attempts == [["benefit"]]
    output = capsys.readouterr()
    assert expected_message in output.err
    assert "[requests] generation=1 repair=0 audit=0" in output.out
    assert "生成或审计失败" not in output.out
    assert questions._question_store().get("rejections", "benefit") is None


def test_off_peak_policy_does_not_replace_global_chat_functions(
    isolated_refresh, monkeypatch,
):
    source = _source("benefit")
    monkeypatch.setattr(script, "_sources", lambda level: [source])
    monkeypatch.setattr(web.gaokao_backfill, "is_deepseek_off_peak", lambda: True)
    generation_chat = web._gaokao_generation_chat
    audit_chat = web._gaokao_audit_chat

    def generate(sources, **kwargs):
        assert web._gaokao_generation_chat is generation_chat
        assert web._gaokao_audit_chat is audit_chat
        return 0, 0

    monkeypatch.setattr(script, "_run_generation", generate)
    monkeypatch.setattr(sys, "argv", ["generate", "--off-peak-only"])

    assert script.main() == 0
    assert web._gaokao_generation_chat is generation_chat
    assert web._gaokao_audit_chat is audit_chat


def test_request_log_separates_field_repairs_from_full_generation(capsys):
    diagnostic = script._Diagnostics()
    diagnostic({"event": "field_repair_request"})
    diagnostic({"event": "audit_request"})
    diagnostic.report()
    assert "generation=0 repair=1 audit=1" in capsys.readouterr().out
