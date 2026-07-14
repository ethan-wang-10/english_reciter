"""Flask 入口的轻量契约与静态资源测试。"""

import os
import tempfile
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest


_WEB_DATA_DIR = tempfile.TemporaryDirectory(prefix="english-reciter-web-test-")
os.environ["ENGLISH_RECITER_DATA_DIR"] = _WEB_DATA_DIR.name

import simple_web_app as web  # noqa: E402


class _SemanticReciter:
    def __init__(self, exercise_type='context'):
        self.config = SimpleNamespace(MAX_SUCCESS_COUNT=8)
        self.word = SimpleNamespace(
            english='benefit',
            chinese='n. 益处；好处',
            example='Exercise brings many benefits._锻炼带来许多好处。',
            success_count=0,
            review_count=0,
            next_review_date=date.today(),
        )
        self.task_item = {
            'item_id': 'item-1',
            'word_key': 'benefit',
            'exercise_type': exercise_type,
            'status': 'pending',
            'phase': 'main',
            'attempts': 0,
        }
        self.mastered_words = []
        self.saved = 0
        self.last_apply = None

    def resolve_daily_task_item(self, task_id, item_id, word_id, event_id=''):
        if task_id != 'task-1' or item_id != 'item-1':
            return None
        if word_id and self.word_state_key(word_id) != self.task_item['word_key']:
            return None
        return self.task_item

    def find_word(self, word_id, include_mastered=True):
        return self.word if self.word_state_key(word_id) == 'benefit' else None

    def word_state_key(self, value):
        return str(getattr(value, 'english', value) or '').strip().casefold()

    def save_learning_data(self, backup=False):
        self.saved += 1

    def task_attempt_count(self, item):
        return int((item or {}).get('attempts') or 0)

    def apply_scored_review_attempt(self, word, **kwargs):
        self.last_apply = kwargs
        attempt_limit = web.exercise_attempt_limit(kwargs.get('exercise_type', 'spelling'))
        attempt_number = self.task_attempt_count(self.task_item) + 1
        self.task_item['attempts'] = attempt_number
        final_attempt = bool(
            not kwargs.get('correct') and attempt_number % attempt_limit == 0
        )
        if final_attempt:
            self.task_item['phase'] = 'remedial'
        return {
            'recorded': True,
            'message': '已判分',
            'attempt_number': attempt_number,
            'attempt_limit': attempt_limit,
            'final_attempt': final_attempt,
            'mastered_now': False,
            'remedial': False,
            'old_success_count': 0,
            'new_success_count': 0,
        }

    def daily_task_progress(self):
        return {'total': 1, 'completed': 0, 'remaining': 1}

    def review_state_payload(self, word):
        empty = {
            key: {'score': 0, 'percent': 0, 'attempts': 0, 'correct': 0, 'streak': 0}
            for key in ('recognition', 'context', 'spelling', 'listening')
        }
        return {
            'exercise_type': self.task_item['exercise_type'],
            'mastery': {'overall': 0, 'overall_percent': 0, 'by_type': empty},
            'scheduler': {},
        }


def _semantic_source():
    return {
        'english': 'benefit',
        'chinese': 'n. 益处；好处',
        'level': '高中',
        'phonetic': '/benefit/',
        'pos': 'n',
        'context_sentence': 'Exercise brings many ____ to our health.',
        'context_answer': 'benefits',
        'context_cn': '锻炼给健康带来很多益处。',
        'source_hash': 'benefit-source',
    }


def _semantic_record():
    record, error = web.gaokao_questions.finalize_generated_questions(
        _semantic_source(),
        {
            'english': 'benefit',
            'recognition_distractors': ['负担', '挑战', '限制'],
            'recognition_explanation_zh': 'benefit 表示益处或好处。',
            'context_distractors': ['burdens', 'limits', 'risks'],
            'context_explanation_zh': 'many 后接复数名词，语义需要“益处”。',
        },
    )
    assert error == ''
    return record


def _mock_student_session(monkeypatch, reciter):
    monkeypatch.setattr(web, 'verify_token', lambda token: 'alice')
    monkeypatch.setattr(
        web,
        'get_user',
        lambda username: {'password_hash': 'unused', 'enabled': True},
    )

    @contextmanager
    def session(username):
        yield reciter

    monkeypatch.setattr(web, 'user_reciter_session', session)


def _use_private_question_bank(monkeypatch, tmp_path):
    questions = web.gaokao_questions
    monkeypatch.setattr(questions, 'QUESTION_BANK_FILE', tmp_path / 'questions.json')
    monkeypatch.setattr(questions, 'QUESTION_BANK_LOCK_FILE', tmp_path / '.questions.lock')
    monkeypatch.setattr(questions, '_cache', None)
    monkeypatch.setattr(questions, '_cache_mtime_ns', -1)


@pytest.fixture
def client():
    web.app.config.update(TESTING=True)
    with web.app.test_client() as test_client:
        yield test_client


def test_password_reset_policy_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("PASSWORD_RESET_TTL_MINUTES", "45")
    monkeypatch.setenv("PASSWORD_RESET_COOLDOWN_SECONDS", "90")
    assert web._password_reset_policy() == (45, 90)


def test_smtp_from_email_accepts_display_name(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_TIMEOUT", "15")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "English Reciter <sender@example.com>")
    monkeypatch.delenv("SMTP_FROM_NAME", raising=False)
    config = web._smtp_config()
    assert config["from_email"] == "sender@example.com"
    assert config["from_name"] == "English Reciter"


def test_forgot_password_applies_ttl_and_cooldown(client, monkeypatch) -> None:
    monkeypatch.setattr(web, "_rate_allow", lambda *args: True)
    monkeypatch.setattr(web, "_smtp_config", lambda: {})
    monkeypatch.setattr(web, "_password_reset_policy", lambda: (30, 60))
    monkeypatch.setattr(
        web,
        "load_users",
        lambda: {"alice": {"email": "alice@example.com", "enabled": True}},
    )
    claims = []
    monkeypatch.setattr(
        web,
        "create_session_if_absent",
        lambda kind, principal, ttl: claims.append((kind, principal, ttl)) or "cooldown",
    )
    monkeypatch.setattr(web, "revoke_principal", lambda *args: None)
    reset_sessions = []
    monkeypatch.setattr(
        web,
        "_db_create_auth_session",
        lambda kind, principal, ttl: reset_sessions.append((kind, principal, ttl)) or "reset",
    )
    sent = []
    monkeypatch.setattr(
        web,
        "_send_password_reset_email",
        lambda email, url, minutes: sent.append((email, url, minutes)),
    )

    response = client.post(
        "/api/auth/forgot-password",
        json={"email": "alice@example.com"},
    )

    assert response.status_code == 200
    assert claims[0][0] == web.SESSION_KIND_PASSWORD_RESET_COOLDOWN
    assert claims[0][1] == "alice"
    assert claims[0][2].total_seconds() == 60
    assert reset_sessions[0][2].total_seconds() == 30 * 60
    assert sent[0][0] == "alice@example.com"
    assert sent[0][2] == 30


def test_static_assets_use_public_cache_headers(client) -> None:
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    cache_control = response.headers.get("Cache-Control", "")
    assert "public" in cache_control
    assert "max-age=3600" in cache_control
    assert "no-cache" not in cache_control


def test_summary_payload_exposes_today_progress_and_memory_status_counts() -> None:
    pending = SimpleNamespace(english='pending', review_count=2)
    stable = SimpleNamespace(english='stable')
    reinforcement = SimpleNamespace(english='reinforcement')

    class SummaryReciter:
        all_words = [pending]
        mastered_words = [stable, reinforcement]
        current_review_round = 0
        learning_state_v2 = {
            'review_states': {
                'stable': {'memory_status': 'stable'},
                'reinforcement': {'memory_status': 'reinforcement'},
            },
        }

        @staticmethod
        def word_state_key(word):
            return word.english

        @staticmethod
        def count_due_words():
            return 3

        @staticmethod
        def daily_task_progress():
            return {'total': 10, 'completed': 4, 'remaining': 6}

    payload = web._summary_payload_from_reciter(SummaryReciter())
    stats = payload['stats']

    assert stats['mastered_words'] == 2
    assert stats['mastered_stable'] == 1
    assert stats['mastered_reinforcement'] == 1
    assert stats['today_task'] == {
        'total': 10,
        'completed': 4,
        'remaining': 6,
        'estimated_minutes': 4,
    }


def test_practice_requires_review_event_id(client, monkeypatch) -> None:
    user = {"password_hash": "unused", "enabled": True}
    monkeypatch.setattr(web, "verify_token", lambda token: "alice")
    monkeypatch.setattr(web, "get_user", lambda username: user)

    response = client.post(
        "/api/words/practice",
        headers={"Authorization": "Bearer test"},
        json={
            "word_id": "example",
            "answer": "example",
            "bonus_practice": True,
        },
    )

    assert response.status_code == 400
    assert "事件标识" in response.get_json()["error"]


def test_practice_does_not_treat_string_false_as_bonus(client, monkeypatch) -> None:
    user = {"password_hash": "unused", "enabled": True}
    monkeypatch.setattr(web, "verify_token", lambda token: "alice")
    monkeypatch.setattr(web, "get_user", lambda username: user)

    response = client.post(
        "/api/words/practice",
        headers={"Authorization": "Bearer test"},
        json={
            "word_id": "example",
            "answer": "example",
            "bonus_practice": "false",
            "review_event_id": "strict-boolean-event",
        },
    )

    assert response.status_code == 409
    assert "今日任务" in response.get_json()["error"]


def test_review_payload_redacts_semantic_answers(monkeypatch, tmp_path) -> None:
    _use_private_question_bank(monkeypatch, tmp_path)
    record = _semantic_record()
    web.gaokao_questions.persist_generation_result({'benefit': record}, {})
    reciter = _SemanticReciter('context')
    reciter.task_item['calibration_reason'] = 'legacy'
    task = {
        'items': [reciter.task_item],
        'plan': {'task_id': 'task-1'},
    }
    monkeypatch.setattr(web, 'lookup_csv_word', lambda word: None)

    payload = web._review_words_payload(reciter, [reciter.word], task)

    item = payload['words'][0]
    assert item['english'] == 'question:item-1'
    assert item['chinese'] == ''
    assert item['examples'] == []
    assert 'answer_option_id' not in item['question']
    assert 'translation_zh' not in item['question']
    assert item['task_calibration'] == 'legacy'
    assert reciter.task_item['question_id'] == record['context']['question_id']


def test_new_word_payload_includes_study_preview_before_semantic_question(
    monkeypatch,
    tmp_path,
) -> None:
    _use_private_question_bank(monkeypatch, tmp_path)
    web.gaokao_questions.persist_generation_result({'benefit': _semantic_record()}, {})
    reciter = _SemanticReciter('recognition')
    reciter.task_item['reason'] = 'new'
    task = {
        'items': [reciter.task_item],
        'plan': {'task_id': 'task-1'},
    }
    monkeypatch.setattr(web, 'lookup_csv_word', lambda word: None)

    payload = web._review_words_payload(reciter, [reciter.word], task)
    word = payload['words'][0]

    assert word['chinese'] == ''
    assert word['examples'] == []
    assert word['study']['english'] == 'benefit'
    assert word['study']['chinese'] == 'n. 益处；好处'
    assert word['study']['examples'][0]['en'] == 'Exercise brings many benefits.'


def test_question_endpoint_generates_missing_question_and_returns_public_data(
    client,
    monkeypatch,
    tmp_path,
) -> None:
    _use_private_question_bank(monkeypatch, tmp_path)
    reciter = _SemanticReciter('context')
    _mock_student_session(monkeypatch, reciter)
    monkeypatch.setattr(web, '_runtime_question_source', lambda word: _semantic_source())

    def generate(source):
        web.gaokao_questions.persist_generation_result({'benefit': _semantic_record()}, {})
        return {'generated': 1}

    monkeypatch.setattr(web, '_generate_one_runtime_question', generate)
    response = client.post(
        '/api/words/question',
        headers={'Authorization': 'Bearer test'},
        json={
            'word_id': 'question:item-1',
            'task_id': 'task-1',
            'task_item_id': 'item-1',
            'exercise_type': 'context',
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body['generated'] is True
    assert body['fallback'] is False
    assert 'answer_option_id' not in body['question']
    assert 'translation_zh' not in body['question']
    assert reciter.task_item['question_id'] == body['question']['question_id']
    assert reciter.saved == 1


def test_question_endpoint_falls_back_to_spelling_when_generation_fails(
    client,
    monkeypatch,
    tmp_path,
) -> None:
    _use_private_question_bank(monkeypatch, tmp_path)
    reciter = _SemanticReciter('recognition')
    _mock_student_session(monkeypatch, reciter)
    monkeypatch.setattr(web, '_runtime_question_source', lambda word: _semantic_source())
    monkeypatch.setattr(web, '_generate_one_runtime_question', lambda source: {'generated': 0})
    monkeypatch.setattr(web, 'lookup_csv_word', lambda word: None)

    response = client.post(
        '/api/words/question',
        headers={'Authorization': 'Bearer test'},
        json={
            'word_id': 'benefit',
            'task_id': 'task-1',
            'task_item_id': 'item-1',
            'exercise_type': 'recognition',
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body['fallback'] is True
    assert body['exercise_type'] == 'spelling'
    assert body['word']['english'] == 'benefit'
    assert reciter.task_item['exercise_type'] == 'spelling'
    assert reciter.saved == 1


def test_practice_scores_semantic_option_server_side(client, monkeypatch, tmp_path) -> None:
    _use_private_question_bank(monkeypatch, tmp_path)
    record = _semantic_record()
    web.gaokao_questions.persist_generation_result({'benefit': record}, {})
    reciter = _SemanticReciter('context')
    reciter.task_item['question_id'] = record['context']['question_id']
    _mock_student_session(monkeypatch, reciter)
    wrong_option = next(
        option['id']
        for option in record['context']['options']
        if option['id'] != record['context']['answer_option_id']
    )

    response = client.post(
        '/api/words/practice',
        headers={'Authorization': 'Bearer test'},
        json={
            'word_id': 'question:item-1',
            'task_id': 'task-1',
            'task_item_id': 'item-1',
            'exercise_type': 'context',
            'question_id': record['context']['question_id'],
            'selected_option_id': wrong_option,
            'review_event_id': 'semantic-event-1',
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body['correct'] is False
    assert body['message'] == '答案不正确'
    assert body['attempt_number'] == 1
    assert body['attempt_limit'] == 2
    assert body['final_attempt'] is False
    assert 'answer_feedback' not in body
    assert reciter.last_apply['exercise_type'] == 'context'
    assert len(reciter.last_apply['submission_fingerprint']) == 64

    second_response = client.post(
        '/api/words/practice',
        headers={'Authorization': 'Bearer test'},
        json={
            'word_id': 'question:item-1',
            'task_id': 'task-1',
            'task_item_id': 'item-1',
            'exercise_type': 'context',
            'question_id': record['context']['question_id'],
            'selected_option_id': wrong_option,
            'review_event_id': 'semantic-event-2',
        },
    )

    assert second_response.status_code == 200
    second_body = second_response.get_json()
    assert second_body['correct'] is False
    assert second_body['attempt_number'] == 2
    assert second_body['attempt_limit'] == 2
    assert second_body['final_attempt'] is True
    assert second_body['task_remedial'] is True
    assert second_body['answer_feedback']['correct_option_id'] == record['context']['answer_option_id']
    assert second_body['answer_feedback']['translation_zh']


def test_practice_rejects_question_id_from_another_version(
    client,
    monkeypatch,
    tmp_path,
) -> None:
    _use_private_question_bank(monkeypatch, tmp_path)
    record = _semantic_record()
    web.gaokao_questions.persist_generation_result({'benefit': record}, {})
    reciter = _SemanticReciter('context')
    reciter.task_item['question_id'] = record['context']['question_id']
    _mock_student_session(monkeypatch, reciter)

    response = client.post(
        '/api/words/practice',
        headers={'Authorization': 'Bearer test'},
        json={
            'word_id': 'question:item-1',
            'task_id': 'task-1',
            'task_item_id': 'item-1',
            'exercise_type': 'context',
            'question_id': 'benefit:context:v999',
            'selected_option_id': 'o1',
            'review_event_id': 'semantic-event-tampered',
        },
    )

    assert response.status_code == 409
    assert '题目版本' in response.get_json()['error']
    assert reciter.last_apply is None


def _mock_parent_session(monkeypatch) -> None:
    users = {
        "alice_parent": {
            "password_hash": "unused",
            "enabled": True,
            "role": "parent",
            "child_username": "alice",
        },
        "alice": {
            "password_hash": "unused",
            "enabled": True,
        },
    }
    monkeypatch.setattr(web, "verify_token", lambda token: "alice_parent")
    monkeypatch.setattr(web, "get_user", lambda username: users.get(username))


def test_parent_can_update_student_learning_limits(client, monkeypatch, tmp_path) -> None:
    _mock_parent_session(monkeypatch)
    monkeypatch.setattr(web, "DATA_DIR", tmp_path)

    initial = client.get(
        "/api/user/learning-settings",
        headers={"Authorization": "Bearer parent-test"},
    )
    assert initial.status_code == 200
    assert initial.get_json()["daily_review_limit"] == 120
    assert initial.get_json()["new_words_are_automatic"] is True
    assert initial.get_json()["minimum_review_share_percent"] == 60

    settings_path = web._user_learning_settings_path("alice")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        '{"daily_review_limit": 300, "daily_new_word_limit": 100}',
        encoding="utf-8",
    )

    updated = client.patch(
        "/api/user/learning-settings",
        headers={"Authorization": "Bearer parent-test"},
        json={"daily_review_limit": 240},
    )
    assert updated.status_code == 200
    assert updated.get_json()["daily_review_limit"] == 240

    saved = web._read_user_learning_settings("alice")
    assert saved == {"daily_review_limit": 240}


@pytest.mark.parametrize(
    "payload",
    [
        {"daily_review_limit": 0},
        {"daily_review_limit": 301},
        {"daily_review_limit": 100.5},
        {"daily_review_limit": True},
    ],
)
def test_parent_learning_limits_validate_bounds(
    client,
    monkeypatch,
    tmp_path,
    payload,
) -> None:
    _mock_parent_session(monkeypatch)
    monkeypatch.setattr(web, "DATA_DIR", tmp_path)

    response = client.patch(
        "/api/user/learning-settings",
        headers={"Authorization": "Bearer parent-test"},
        json=payload,
    )
    assert response.status_code == 400


def test_student_cannot_update_learning_limits(client, monkeypatch) -> None:
    student = {"password_hash": "unused", "enabled": True}
    monkeypatch.setattr(web, "verify_token", lambda token: "alice")
    monkeypatch.setattr(web, "get_user", lambda username: student)

    response = client.patch(
        "/api/user/learning-settings",
        headers={"Authorization": "Bearer student-test"},
        json={"daily_review_limit": 200},
    )
    assert response.status_code == 403


def test_unused_invites_are_owner_scoped_recoverable_and_removed_after_use(
    client, monkeypatch, tmp_path
) -> None:
    alice = {
        "password_hash": "unused",
        "created_at": "2026-01-01T00:00:00",
        "enabled": True,
        "invite_quota_limit": 15,
        "invite_quota_used": 0,
    }
    users = {"alice": alice}
    monkeypatch.setattr(web, "INVITES_FILE", tmp_path / "invites.json")
    monkeypatch.setattr(web, "INVITE_CODE_KEY_FILE", tmp_path / ".invite-code.key")
    monkeypatch.setattr(web, "INVITES_LOCK_FILE", tmp_path / ".invites.lock")
    monkeypatch.setattr(web, "_invite_fernet_cache", None)
    monkeypatch.delenv("INVITE_CODE_ENCRYPTION_SECRET", raising=False)
    monkeypatch.setattr(web, "verify_token", lambda token: "alice" if token == "test" else None)
    monkeypatch.setattr(web, "get_user", lambda username: users.get(username))
    monkeypatch.setattr(web, "load_users", lambda: users)
    monkeypatch.setattr(web, "mutate_users", lambda mutator: mutator(users))
    headers = {"Authorization": "Bearer test"}

    created = client.post("/api/user/invites", headers=headers, json={})
    assert created.status_code == 201
    code = created.get_json()["invite_code"]
    data = web.load_invites()
    stored = data["invites"][0]
    assert "code_ciphertext" in stored
    assert stored["code_ciphertext"] != code
    assert "invite_code" not in stored

    # Simulate a process restart: the generated key file must recover the same code.
    monkeypatch.setattr(web, "_invite_fernet_cache", None)

    data["invites"].extend(
        [
            {
                "id": "legacy-hash-only",
                "code_hash": web._hash_invite_code("LEGACY2345"),
                "created_at": "2026-01-01T01:00:00",
                "created_by": "alice",
                "created_by_kind": "user",
                "used_at": None,
                "used_by": None,
            },
            {
                "id": "other-user",
                "code_hash": web._hash_invite_code("OTHER23456"),
                "code_ciphertext": web._encrypt_invite_code_for_storage("OTHER23456"),
                "created_at": "2026-01-01T02:00:00",
                "created_by": "mallory",
                "created_by_kind": "user",
                "used_at": None,
                "used_by": None,
            },
        ]
    )
    web.save_invites(data)

    listed = client.get("/api/user/invites/unused", headers=headers)
    assert listed.status_code == 200
    assert listed.headers["Cache-Control"] == "private, no-store"
    rows = {row["id"]: row for row in listed.get_json()["invites"]}
    assert set(rows) == {stored["id"], "legacy-hash-only"}
    assert rows[stored["id"]]["invite_code"] == code
    assert rows[stored["id"]]["selectable"] is True
    assert rows["legacy-hash-only"]["selectable"] is False
    assert rows["legacy-hash-only"]["unavailable_reason"] == "legacy_hash_only"
    assert "invite_code" not in rows["legacy-hash-only"]

    public_rows = {
        row["id"]: row
        for row in client.get("/api/user/invites", headers=headers).get_json()["invites"]
    }
    assert public_rows[stored["id"]]["selectable"] is True
    assert public_rows["legacy-hash-only"]["selectable"] is False

    alice["enabled"] = False
    denied, denied_error = web.register_user_with_invite("blocked", "secret1", None, code)
    assert denied is False
    assert denied_error == "邀请码无效或已使用"
    assert next(
        row for row in web.load_invites()["invites"] if row["id"] == stored["id"]
    )["used_at"] is None
    alice["enabled"] = True

    ok, error = web.register_user_with_invite("bob", "secret1", None, code)
    assert ok is True
    assert error == ""
    consumed = next(row for row in web.load_invites()["invites"] if row["id"] == stored["id"])
    assert consumed["used_by"] == "bob"
    assert "code_ciphertext" not in consumed

    after_use = client.get("/api/user/invites/unused", headers=headers).get_json()["invites"]
    assert {row["id"] for row in after_use} == {"legacy-hash-only"}


def test_unused_invites_reject_parent_session(client, monkeypatch) -> None:
    users = {
        "alice": {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
        },
        "alice_parent": {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
            "role": "parent",
            "child_username": "alice",
        },
    }
    monkeypatch.setattr(web, "verify_token", lambda token: "alice_parent")
    monkeypatch.setattr(web, "get_user", lambda username: users.get(username))

    response = client.get(
        "/api/user/invites/unused",
        headers={"Authorization": "Bearer parent-test"},
    )

    assert response.status_code == 403


def test_save_invites_keeps_previous_file_when_serialization_fails(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(web, "INVITES_FILE", tmp_path / "invites.json")
    original = {"invites": [{"id": "kept"}]}
    web.save_invites(original)

    def fail_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(web.json, "dump", fail_dump)
    with pytest.raises(OSError, match="disk full"):
        web.save_invites({"invites": [{"id": "replacement"}]})

    assert web.load_invites() == original
    assert not list(tmp_path.glob(".invites.json.*.tmp"))


def test_recover_invite_supports_legacy_secret_key_ciphertext(
    monkeypatch, tmp_path
) -> None:
    from cryptography.fernet import Fernet

    code = "LEGACYKEY2"
    legacy_secret = "previous-secret"
    legacy_key = web.base64.urlsafe_b64encode(
        web.hashlib.sha256(
            (legacy_secret + "|english_reciter.invites.v1").encode("utf-8")
        ).digest()
    )
    ciphertext = web._INVITE_CODE_ENC_PREFIX + Fernet(legacy_key).encrypt(
        code.encode("utf-8")
    ).decode("ascii")
    current_key_file = tmp_path / ".invite-code.key"
    current_key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(web, "INVITE_CODE_KEY_FILE", current_key_file)
    monkeypatch.setattr(web, "_invite_fernet_cache", None)
    monkeypatch.delenv("INVITE_CODE_ENCRYPTION_SECRET", raising=False)
    monkeypatch.setenv("SECRET_KEY", legacy_secret)

    recovered = web._recover_invite_code(
        {
            "id": "legacy-secret",
            "code_hash": web._hash_invite_code(code),
            "code_ciphertext": ciphertext,
            "used_at": None,
        }
    )

    assert recovered == code


def test_purge_user_removes_owned_invites_before_username_can_be_reused(
    monkeypatch, tmp_path
) -> None:
    users = {
        "alice": {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
        },
        "alice_parent": {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
            "role": "parent",
            "child_username": "alice",
        },
    }
    monkeypatch.setattr(web, "DATA_DIR", tmp_path)
    monkeypatch.setattr(web, "INVITES_FILE", tmp_path / "invites.json")
    monkeypatch.setattr(web, "INVITES_LOCK_FILE", tmp_path / ".invites.lock")
    monkeypatch.setattr(web, "mutate_users", lambda mutator: mutator(users))
    monkeypatch.setattr(web, "get_user", lambda username: users.get(username))
    monkeypatch.setattr(web, "_revoke_user_tokens", lambda username: None)
    monkeypatch.setattr(web.challenges_mod, "purge_user_challenges_refs", lambda *args: None)
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "old-data.json").write_text("{}", encoding="utf-8")
    web.save_invites(
        {
            "invites": [
                {
                    "id": "owned-unused",
                    "created_by": "alice",
                    "created_by_kind": "user",
                    "used_at": None,
                },
                {
                    "id": "owned-used",
                    "created_by": "alice",
                    "created_by_kind": "user",
                    "used_at": "2026-01-02T00:00:00",
                },
                {
                    "id": "other",
                    "created_by": "mallory",
                    "created_by_kind": "user",
                    "used_at": None,
                },
            ]
        }
    )

    web._purge_student_account_completely("alice")

    assert "alice" not in users
    assert "alice_parent" not in users
    assert [row["id"] for row in web.load_invites()["invites"]] == ["other"]
    assert not (tmp_path / "alice").exists()


def test_purge_user_restores_directory_when_user_transaction_fails(
    monkeypatch, tmp_path
) -> None:
    user = {
        "password_hash": "unused",
        "created_at": "2026-01-01T00:00:00",
        "enabled": True,
    }
    monkeypatch.setattr(web, "DATA_DIR", tmp_path)
    monkeypatch.setattr(web, "INVITES_FILE", tmp_path / "invites.json")
    monkeypatch.setattr(web, "INVITES_LOCK_FILE", tmp_path / ".invites.lock")
    monkeypatch.setattr(web, "get_user", lambda username: user if username == "alice" else None)

    def fail_mutate(_mutator):
        raise RuntimeError("sqlite failed")

    monkeypatch.setattr(web, "mutate_users", fail_mutate)
    user_dir = tmp_path / "alice"
    user_dir.mkdir()
    (user_dir / "learning_data.json").write_text("{}", encoding="utf-8")
    original_invites = {"invites": [{"id": "kept"}]}
    web.save_invites(original_invites)

    with pytest.raises(RuntimeError, match="sqlite failed"):
        web._purge_student_account_completely("alice")

    assert (user_dir / "learning_data.json").is_file()
    assert web.load_invites() == original_invites


@pytest.mark.parametrize("payload", ["null", "[]", "{broken"])
def test_login_rejects_non_object_or_malformed_json(client, payload: str) -> None:
    response = client.post(
        "/api/auth/login",
        data=payload,
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "无效的JSON数据"


def test_register_rejects_non_object_json(client) -> None:
    response = client.post(
        "/api/auth/register",
        data="null",
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "无效的JSON数据"


def test_login_accepts_json_content_type_with_charset(client, monkeypatch) -> None:
    monkeypatch.setattr(web, "verify_user", lambda username, password: True)
    monkeypatch.setattr(
        web,
        "get_user",
        lambda username: {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
        },
    )
    monkeypatch.setattr(web, "create_token", lambda username: "test-token")
    monkeypatch.setattr(
        web,
        "_auth_session_payload",
        lambda username: {
            "login_username": username,
            "is_parent": False,
            "child_username": None,
            "system_broadcast": None,
        },
    )

    response = client.post(
        "/api/auth/login",
        data='{"username":"alice","password":"secret"}',
        content_type="application/json; charset=UTF-8",
    )

    assert response.status_code == 200
    assert response.get_json()["access_token"] == "test-token"


def test_wordbank_search_builds_english_candidates_once_per_term(client, monkeypatch) -> None:
    monkeypatch.setattr(web, "verify_token", lambda token: "alice")
    monkeypatch.setattr(
        web,
        "get_user",
        lambda username: {
            "password_hash": "unused",
            "created_at": "2026-01-01T00:00:00",
            "enabled": True,
        },
    )
    monkeypatch.setattr(web, "_rate_allow", lambda key, limit: True)
    rows = [
        {"english": "apple", "chinese": "苹果"},
        {"english": "banana", "chinese": "香蕉"},
        {"english": "orange", "chinese": "橙子"},
    ]
    monkeypatch.setattr(web, "merge_wordbank_rows_for_search", lambda level: (rows, {"apple", "banana", "orange"}))
    monkeypatch.setattr(web, "get_wordbank_lemma_mappings", lambda: {})
    monkeypatch.setattr(
        web,
        "_first_lemma_in_csv_with_kind",
        lambda term, *args: (("apple", "plural") if term == "apples" else (term, "surface")),
    )
    candidate_calls = []

    def candidates(term, *args):
        candidate_calls.append(term)
        yield ("apple" if term == "apples" else term), "test"

    monkeypatch.setattr(web, "_iter_csv_lemma_candidates", candidates)

    response = client.get(
        "/api/wordbank/csv/search?q=apples,banana&nlp=0&heuristics=1",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    assert [row["english"] for row in response.get_json()["words"]] == ["apple", "banana"]
    assert candidate_calls == ["apples", "banana"]
