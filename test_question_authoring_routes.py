import io
from functools import wraps
from unittest.mock import Mock

import pytest
from flask import Flask, jsonify, request

import question_authoring_routes as routes
from question_authoring import AuthoringError


BASE = "/api/admin/gaokao/authoring"
ADMIN_HEADERS = {"Authorization": "Bearer admin"}
ENDPOINTS = [
    ("GET", "/pending"),
    ("POST", "/claims"),
    ("GET", "/claims/job"),
    ("POST", "/claims/job/renew"),
    ("POST", "/claims/job/release"),
    ("POST", "/claims/job/submissions"),
]


@pytest.fixture
def harness(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, MAX_CONTENT_LENGTH=512 * 1024)
    service = Mock()
    for method in ("pending", "claim", "get_job", "renew", "release", "submit"):
        getattr(service, method).return_value = {"method": method}
    constructor = Mock(return_value=service)
    monkeypatch.setattr(routes, "QuestionAuthoringService", constructor)

    def admin_required(function):
        @wraps(function)
        def authenticated(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header:
                return jsonify({"error": "authentication required"}), 401
            if header != "Bearer admin":
                return jsonify({"error": "administrator required"}), 403
            return function(*args, **kwargs)
        return authenticated

    sources = Mock(name="sources")
    source_lookup = Mock(name="source_lookup")
    routes.register_question_authoring_routes(app, admin_required, sources, source_lookup)
    constructor.assert_called_once_with(sources, source_lookup)
    return app, app.test_client(), service


@pytest.mark.parametrize("method,path", ENDPOINTS)
@pytest.mark.parametrize("headers,status", [({}, 401), ({"Authorization": "Bearer student"}, 403)])
def test_every_endpoint_requires_admin_before_parsing(harness, method, path, headers, status):
    _, client, service = harness
    response = client.open(BASE + path, method=method, headers=headers, data="not-json")
    assert response.status_code == status
    assert "error" in response.get_json()
    assert service.mock_calls == []


def test_pending_forwards_query_values_to_service(harness):
    _, client, service = harness
    query = {"kind": "context_blind", "worker_id": "auditor", "level": "gaokao", "limit": "4"}
    response = client.get(BASE + "/pending", query_string=query, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.get_json() == {"method": "pending"}
    service.pending.assert_called_once_with(query)


def test_missing_pending_values_are_left_for_service_defaults(harness):
    _, client, service = harness
    response = client.get(BASE + "/pending", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    service.pending.assert_called_once_with({})


@pytest.mark.parametrize("path,method", [
    ("/claims", "claim"),
    ("/claims/job/renew", "renew"),
    ("/claims/job/release", "release"),
    ("/claims/job/submissions", "submit"),
])
def test_post_endpoints_forward_json_object(harness, path, method):
    _, client, service = harness
    data = {"worker_id": "worker", "request_id": "request", "items": [{"item_id": "item"}]}
    response = client.post(BASE + path, json=data, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.get_json() == {"method": method}
    args = (data,) if method == "claim" else ("job", data)
    getattr(service, method).assert_called_once_with(*args)


@pytest.mark.parametrize("worker", ["worker", ""])
def test_get_claim_forwards_worker_or_empty_value(harness, worker):
    _, client, service = harness
    query = {"worker_id": worker} if worker else {}
    response = client.get(BASE + "/claims/job", query_string=query, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.get_json() == {"method": "get_job"}
    service.get_job.assert_called_once_with("job", worker)


@pytest.mark.parametrize("path", [path for method, path in ENDPOINTS if method == "POST"])
@pytest.mark.parametrize("data", ["", "{", "[]", "null", "42", '"value"', '{"limit": NaN}', '{"worker_id":"a","worker_id":"b"}'])
def test_post_bodies_must_be_strict_json_objects(harness, path, data):
    _, client, service = harness
    response = client.post(BASE + path, data=data, content_type="application/json", headers=ADMIN_HEADERS)
    assert response.status_code == 400
    assert "error" in response.get_json()
    assert service.mock_calls == []


def test_json_content_type_is_required(harness):
    _, client, service = harness
    response = client.post(BASE + "/claims", data="{}", content_type="text/plain", headers=ADMIN_HEADERS)
    assert response.status_code == 415
    assert service.mock_calls == []


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_service_errors_preserve_status_message_and_details(harness, method, path):
    _, client, service = harness
    error = AuthoringError("Lease conflict", status=409, details={"job_id": "job"})
    for name in ("pending", "claim", "get_job", "renew", "release", "submit"):
        getattr(service, name).side_effect = error
    response = client.open(BASE + path, method=method, json={}, headers=ADMIN_HEADERS)
    assert response.status_code == 409
    assert response.get_json() == {"error": "Lease conflict", "details": {"job_id": "job"}}


def test_content_length_limit_does_not_change_global_upload_limit(harness):
    app, client, service = harness
    response = client.post(
        BASE + "/claims", data=b" " * (routes.MAX_AUTHORING_BODY_BYTES + 1),
        content_type="application/json", headers=ADMIN_HEADERS,
    )
    assert response.status_code == 413
    assert "error" in response.get_json()
    assert app.config["MAX_CONTENT_LENGTH"] == 512 * 1024
    assert service.mock_calls == []


class TrackingStream(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.requested_sizes = []

    def read(self, size=-1):
        self.requested_sizes.append(size)
        return super().read(size)

    def readinto(self, buffer):
        self.requested_sizes.append(len(buffer))
        return super().readinto(buffer)


@pytest.mark.parametrize("extra_bytes,status", [(0, 200), (1, 413), (256 * 1024, 413)])
def test_chunked_body_is_bounded_without_content_length(harness, extra_bytes, status):
    _, client, service = harness
    stream = TrackingStream(b"{}" + b" " * (routes.MAX_AUTHORING_BODY_BYTES - 2 + extra_bytes))
    response = client.open(
        BASE + "/claims", method="POST", headers=ADMIN_HEADERS, content_type="application/json",
        environ_overrides={"wsgi.input": stream, "wsgi.input_terminated": True, "CONTENT_LENGTH": ""},
    )
    assert response.status_code == status
    assert stream.requested_sizes == [routes.MAX_AUTHORING_BODY_BYTES + 1]
    assert stream.tell() <= routes.MAX_AUTHORING_BODY_BYTES + 1
    if status == 200:
        service.claim.assert_called_once_with({})
    else:
        assert service.mock_calls == []


def test_request_at_exact_content_length_limit_is_accepted(harness):
    _, client, service = harness
    response = client.post(
        BASE + "/claims", data=b"{}" + b" " * (routes.MAX_AUTHORING_BODY_BYTES - 2),
        content_type="application/json", headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    service.claim.assert_called_once_with({})
