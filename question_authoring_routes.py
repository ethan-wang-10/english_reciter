"""Administrator HTTP endpoints for external question generation and review."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from question_authoring import AuthoringError, QuestionAuthoringService


MAX_AUTHORING_BODY_BYTES = 256 * 1024


def _object_pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _invalid_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _json_object() -> dict:
    if request.content_length is not None and request.content_length > MAX_AUTHORING_BODY_BYTES:
        raise AuthoringError("Request body exceeds 256 KB", status=413)
    if not request.is_json:
        raise AuthoringError("Content-Type must be application/json", status=415)
    try:
        # Bound the stream itself because chunked requests need not have Content-Length.
        raw = request.stream.read(MAX_AUTHORING_BODY_BYTES + 1)
    except RequestEntityTooLarge as error:
        raise AuthoringError("Request body exceeds the upload limit", status=413) from error
    except BadRequest as error:
        raise AuthoringError("Could not read the request body") from error
    if len(raw) > MAX_AUTHORING_BODY_BYTES:
        raise AuthoringError("Request body exceeds 256 KB", status=413)
    try:
        body = json.loads(raw, object_pairs_hook=_object_pairs, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise AuthoringError("Request body must contain valid JSON") from error
    if not isinstance(body, dict):
        raise AuthoringError("Request body must be a JSON object")
    return body


def register_question_authoring_routes(app, admin_required, sources, source_lookup) -> None:
    service = QuestionAuthoringService(sources, source_lookup)
    routes = Blueprint("question_authoring", __name__, url_prefix="/api/admin/gaokao/authoring")

    @routes.errorhandler(AuthoringError)
    def authoring_error(error):
        return jsonify({"error": str(error), "details": error.details}), error.status

    @routes.get("/pending")
    @admin_required
    def pending():
        return jsonify(service.pending(request.args.to_dict()))

    @routes.post("/claims")
    @admin_required
    def claim():
        return jsonify(service.claim(_json_object()))

    @routes.get("/claims/<job_id>")
    @admin_required
    def get_job(job_id):
        return jsonify(service.get_job(job_id, request.args.get("worker_id", "")))

    @routes.post("/claims/<job_id>/renew")
    @admin_required
    def renew(job_id):
        return jsonify(service.renew(job_id, _json_object()))

    @routes.post("/claims/<job_id>/release")
    @admin_required
    def release(job_id):
        return jsonify(service.release(job_id, _json_object()))

    @routes.post("/claims/<job_id>/submissions")
    @admin_required
    def submit(job_id):
        return jsonify(service.submit(job_id, _json_object()))

    app.register_blueprint(routes)
