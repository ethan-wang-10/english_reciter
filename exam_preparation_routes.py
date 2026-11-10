"""Authenticated endpoints for students' named exam papers."""

from flask import Blueprint, jsonify, request

from exam_preparation import (
    ExamPreparationError,
    ExamPreparationStore,
    MAX_PAPER_NAME,
    parse_exam_words,
)


def _body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ExamPreparationError("请提交有效的 JSON 对象")
    return body


def register_exam_preparation_routes(app, token_required, parent_forbidden, data_dir, word_lookup, question_lookup):
    routes = Blueprint("exam_preparation", __name__, url_prefix="/api/exam-preparation")

    def store(username):
        return ExamPreparationStore(data_dir() / username / "exam_preparation.sqlite3")

    @routes.errorhandler(ExamPreparationError)
    def invalid_exam(error):
        return jsonify({"error": str(error), **error.details}), error.status

    @routes.get("/papers")
    @token_required
    def list_papers(username):
        return jsonify({"papers": store(username).list_papers()})

    @routes.post("/papers")
    @token_required
    def create_paper(username):
        body = _body()
        name = body.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_PAPER_NAME:
            raise ExamPreparationError(f"请填写 1～{MAX_PAPER_NAME} 个字符的考卷名称")
        keys, duplicates = parse_exam_words(body.get("words"))
        found = word_lookup(username, keys)
        missing = [key for key in keys if not (found.get(key, {}).get("chinese") or "").strip()]
        if missing:
            preview = "、".join(missing[:12]) + ("等" if len(missing) > 12 else "")
            raise ExamPreparationError(
                f"以下单词未找到中文释义：{preview}。请检查拼写，或先在“导入单词”中补充后再保存。",
                missing_words=missing,
            )
        words = [{"english": found[key]["english"], "chinese": found[key]["chinese"]} for key in keys]
        paper = store(username).create_paper(name.strip(), words)
        return jsonify({"paper": paper, "duplicates_removed": duplicates}), 201

    @routes.post("/papers/<paper_id>/attempts")
    @token_required
    @parent_forbidden
    def start_attempt(username, paper_id):
        body = _body()
        include_choices = body.get("include_choices", False)
        if not isinstance(include_choices, bool):
            raise ExamPreparationError("是否开启选择题必须为布尔值")
        user_store = store(username)
        paper = user_store.get_paper(paper_id)
        return jsonify({"attempt": user_store.start_attempt(paper, include_choices, question_lookup)}), 201

    @routes.get("/attempts/<attempt_id>")
    @token_required
    @parent_forbidden
    def get_attempt(username, attempt_id):
        return jsonify(store(username).get_attempt(attempt_id))

    @routes.post("/attempts/<attempt_id>/submit")
    @token_required
    @parent_forbidden
    def submit_attempt(username, attempt_id):
        return jsonify({"result": store(username).submit_attempt(attempt_id, _body().get("answers"))})

    app.register_blueprint(routes)
