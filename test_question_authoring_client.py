from io import BytesIO
import json
import urllib.error
import urllib.parse
import uuid

import pytest

from scripts import question_authoring_client as client


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv(client.TOKEN_ENV, "test-secret-admin-token")
    state = {"calls": [], "payload": {"status": "active", "job_id": "job-a", "items": []}}

    class Opener:
        def open(self, request, *, timeout):
            state["calls"].append((request, timeout))
            if state.get("error"):
                raise state["error"]
            raw = state.get("raw")
            return BytesIO(raw if raw is not None else json.dumps(state["payload"]).encode())

    def build_opener(*handlers):
        assert len(handlers) == 1 and isinstance(handlers[0], client._NoRedirects)
        return Opener()

    monkeypatch.setattr(client.urllib.request, "build_opener", build_opener)
    return state


def test_pending_uses_authenticated_get_and_encoded_filters(http, capsys):
    assert client.main(["pending", "--worker-id", "reviewer-a", "--kind", "context_blind", "--level", "高中"]) == 0
    request, timeout = http["calls"][0]
    assert request.method == "GET" and request.data is None
    assert timeout == 60
    assert request.get_header("Authorization") == "Bearer test-secret-admin-token"
    url = urllib.parse.urlsplit(request.full_url)
    assert url.scheme == "https" and url.netloc == "english.itorange.online"
    assert url.path == client.API_ROOT + "/pending"
    assert urllib.parse.parse_qs(url.query) == {
        "kind": ["context_blind"], "worker_id": ["reviewer-a"], "level": ["高中"], "limit": ["10"],
    }
    assert "test-secret-admin-token" not in capsys.readouterr().out


def test_claim_records_id_before_network_and_sends_exact_request(http, capsys):
    assert client.main(["claim", "--worker-id", "generator-a", "--limit", "2"]) == 0
    request, _ = http["calls"][0]
    payload = json.loads(request.data)
    uuid.UUID(payload["request_id"])
    assert payload == {
        "request_id": payload["request_id"], "worker_id": "generator-a", "kind": "generation",
        "level": "", "limit": 2, "ttl_seconds": 3600,
    }
    output = capsys.readouterr()
    assert f"request_id={payload['request_id']}" in output.err
    assert json.loads(output.out)["client_request"]["request_id"] == payload["request_id"]
    assert request.get_header("Content-type") == "application/json"


def test_claim_explicit_id_survives_network_error_without_automatic_retry(http, capsys):
    http["error"] = urllib.error.URLError("connection lost")
    assert client.main(["claim", "--worker-id", "generator-a", "--request-id", "retry-claim-1"]) == 1
    assert len(http["calls"]) == 1
    assert json.loads(http["calls"][0][0].data)["request_id"] == "retry-claim-1"
    output = capsys.readouterr()
    assert "request_id=retry-claim-1" in output.err
    assert json.loads(output.out)["client_request"]["request_id"] == "retry-claim-1"


def test_revision_claim_preserves_raw_history_in_response(http, capsys):
    item = {"item_id": "old-a", "mode": "repair", "source": {"english": "benefit"},
            "previous_records": {"rejections": {"raw": {"english": "benefit"}, "last_error": "ambiguous"}}}
    http["payload"] = {"job_id": "revision-a", "kind": "revision", "items": [item]}
    assert client.main(["claim", "--worker-id", "editor-a", "--kind", "revision"]) == 0
    assert json.loads(http["calls"][0][0].data)["kind"] == "revision"
    assert json.loads(capsys.readouterr().out)["items"] == [item]


@pytest.mark.parametrize("command", ["pending", "claim"])
def test_word_filters_are_normalized_and_encoded(http, command):
    assert client.main([command, "--worker-id", "editor", "--words", " Benefit ", "ice cream"]) == 0
    request = http["calls"][0][0]
    if command == "pending":
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        assert json.loads(query["words"][0]) == ["benefit", "ice cream"]
    else:
        assert json.loads(request.data)["words"] == ["benefit", "ice cream"]


@pytest.mark.parametrize("words", [["Benefit", "benefit"], [""], ["x" * 129], [str(i) for i in range(11)]])
def test_invalid_word_filters_never_call_http(http, words):
    assert client.main(["claim", "--worker-id", "editor", "--words", *words]) == 1
    assert not http["calls"]


@pytest.mark.parametrize("wrapped", [True, False])
def test_submit_sends_all_items_and_explicit_submission_id(http, tmp_path, wrapped, capsys):
    items = [{"item_id": f"item-{index}", "result": {"english": f"word{index}"}} for index in (1, 2)]
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps({"items": items} if wrapped else items))
    assert client.main([
        "submit", "job-a", "--worker-id", "generator-a", "--submission-id", "submission-a",
        "--input", str(input_file),
    ]) == 0
    request, _ = http["calls"][0]
    assert request.full_url.endswith("/claims/job-a/submissions")
    assert json.loads(request.data) == {"worker_id": "generator-a", "submission_id": "submission-a", "items": items}
    assert json.loads(capsys.readouterr().out)["client_request"]["submission_id"] == "submission-a"


def test_submit_generated_id_is_written_in_file_response(http, tmp_path, capsys):
    input_file, output_file = tmp_path / "input.json", tmp_path / "response.json"
    input_file.write_text('[{"item_id":"item-a","result":{}}]')
    assert client.main([
        "submit", "job-a", "--worker-id", "reviewer-a", "--input", str(input_file), "--output", str(output_file),
    ]) == 0
    submission_id = json.loads(http["calls"][0][0].data)["submission_id"]
    uuid.UUID(submission_id)
    assert json.loads(output_file.read_text())["client_request"]["submission_id"] == submission_id
    output = capsys.readouterr()
    assert not output.out and submission_id in output.err


@pytest.mark.parametrize("command,suffix,method", [
    ("get", "", "GET"), ("release", "/release", "POST"), ("renew", "/renew", "POST"),
])
def test_job_commands_preserve_worker_and_path(http, command, suffix, method):
    assert client.main([command, "job-a", "--worker-id", "reviewer-a"]) == 0
    request, _ = http["calls"][0]
    parsed = urllib.parse.urlsplit(request.full_url)
    assert request.method == method
    assert parsed.path == client.API_ROOT + "/claims/job-a" + suffix
    if command == "get":
        assert urllib.parse.parse_qs(parsed.query) == {"worker_id": ["reviewer-a"]}
    else:
        expected = {"worker_id": "reviewer-a"}
        if command == "renew":
            expected["ttl_seconds"] = 3600
        assert json.loads(request.data) == expected


def test_token_file_overrides_environment_and_never_appears_in_output(http, tmp_path, capsys):
    token_file = tmp_path / "token"
    token_file.write_text("file-secret-token\n")
    http["payload"] = {"error": "echo file-secret-token"}
    assert client.main(["pending", "--worker-id", "worker-a", "--token-file", str(token_file)]) == 0
    assert http["calls"][0][0].get_header("Authorization") == "Bearer file-secret-token"
    output = capsys.readouterr()
    assert "file-secret-token" not in output.out + output.err
    assert "[REDACTED]" in output.out


@pytest.mark.parametrize("base_url", [
    "http://english.itorange.online", "ftp://localhost", "https://user:password@example.org",
    "https://example.org?token=secret", "https://example.org#fragment", "https://example.org:bad",
])
def test_unsafe_base_url_is_refused_before_http(http, base_url, capsys):
    assert client.main(["pending", "--worker-id", "worker-a", "--base-url", base_url]) == 1
    assert not http["calls"]
    assert "test-secret-admin-token" not in capsys.readouterr().out


@pytest.mark.parametrize("base_url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"])
def test_local_http_is_allowed(http, base_url):
    assert client.main(["pending", "--worker-id", "worker-a", "--base-url", base_url]) == 0
    assert http["calls"][0][0].full_url.startswith(base_url + "/")


def test_redirect_cannot_forward_authorization(http, capsys):
    http["error"] = urllib.error.HTTPError(
        client.DEFAULT_BASE_URL, 302, "Found", {"Location": "https://attacker.example"}, BytesIO(b"redirect"),
    )
    assert client.main(["pending", "--worker-id", "worker-a"]) == 1
    assert len(http["calls"]) == 1
    assert "redirect refused" in capsys.readouterr().out
    request = http["calls"][0][0]
    assert client._NoRedirects().redirect_request(request, None, 302, "Found", {}, "https://attacker.example") is None


def test_http_errors_keep_status_and_metadata_but_redact_token(http, capsys):
    http["error"] = urllib.error.HTTPError(
        client.DEFAULT_BASE_URL, 409, "Conflict", {},
        BytesIO(json.dumps({"error": "conflict test-secret-admin-token"}).encode()),
    )
    assert client.main(["claim", "--worker-id", "worker-a", "--request-id", "request-a"]) == 1
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["http_status"] == 409
    assert payload["client_request"]["request_id"] == "request-a"
    assert "test-secret-admin-token" not in output.out + output.err


@pytest.mark.parametrize("raw", [b"<html>login</html>", b"[]", b"\xff"])
def test_invalid_server_json_is_clear_error(http, raw, capsys):
    http["raw"] = raw
    assert client.main(["pending", "--worker-id", "worker-a"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("value", [
    [], {"items": [], "submission_id": "other"}, [{"item_id": "a", "result": []}],
    [{"item_id": "a", "result": {}}, {"item_id": "a", "result": {}}],
])
def test_invalid_submission_file_never_calls_server(http, tmp_path, value):
    input_file = tmp_path / "invalid.json"
    input_file.write_text(json.dumps(value))
    assert client.main(["submit", "job-a", "--worker-id", "worker-a", "--input", str(input_file)]) == 1
    assert not http["calls"]


def test_missing_or_invalid_token_does_not_call_server(http, monkeypatch, capsys):
    monkeypatch.delenv(client.TOKEN_ENV)
    assert client.main(["pending", "--worker-id", "worker-a"]) == 1
    assert not http["calls"]
    assert client.TOKEN_ENV in capsys.readouterr().out
    monkeypatch.setenv(client.TOKEN_ENV, "secret\r\nInjected: value")
    assert client.main(["pending", "--worker-id", "worker-a"]) == 1
    assert not http["calls"]
    assert "Injected" not in capsys.readouterr().out


@pytest.mark.parametrize("args", [
    ["claim", "--ttl-seconds", "59"], ["claim", "--ttl-seconds", "86401"],
    ["claim", "--limit", "11"], ["pending", "--limit", "0"], ["pending", "--timeout", "nan"],
])
def test_invalid_limits_fail_argument_validation(http, args):
    with pytest.raises(SystemExit):
        client.main([*args, "--worker-id", "worker-a"])
    assert not http["calls"]
