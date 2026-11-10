"""Authenticated endpoints for students' named exam papers."""

from flask import Blueprint, jsonify, request
import builtin_exam_catalog

from exam_preparation import (
    ExamPreparationError,
    ExamPreparationStore,
    MAX_PAPER_NAME,
    parse_exam_sentences,
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
        papers = store(username).list_papers()
        return jsonify({
            "papers": [paper for paper in papers if not paper.get('builtin')],
            "builtin_papers": [paper for paper in papers if paper.get('builtin')],
        })

    @routes.get('/papers/<paper_id>/knowledge')
    @token_required
    def paper_knowledge(username, paper_id):
        if not builtin_exam_catalog.is_builtin(paper_id):
            raise ExamPreparationError('这张考卷暂无知识点梳理', 404)
        return jsonify(builtin_exam_catalog.knowledge(paper_id))

    def paper_content(username, body):
        name = body.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_PAPER_NAME:
            raise ExamPreparationError(f"请填写 1～{MAX_PAPER_NAME} 个字符的考卷名称")
        sentences = parse_exam_sentences(body.get("sentences", []))
        keys, duplicates = parse_exam_words(body.get("words", ""), allow_empty=bool(sentences))
        found = word_lookup(username, keys) if keys else {}
        missing = [key for key in keys if not (found.get(key, {}).get("chinese") or "").strip()]
        if missing:
            preview = "、".join(missing[:12]) + ("等" if len(missing) > 12 else "")
            raise ExamPreparationError(
                f"以下单词未找到中文释义：{preview}。请检查拼写，或先在“导入单词”中补充后再保存。",
                missing_words=missing,
            )
        words = [{"english": found[key]["english"], "chinese": found[key]["chinese"]} for key in keys]
        return name.strip(), words, sentences, duplicates

    @routes.post("/papers")
    @token_required
    def create_paper(username):
        name, words, sentences, duplicates = paper_content(username, _body())
        paper = store(username).create_paper(name, words, sentences)
        return jsonify({"paper": paper, "duplicates_removed": duplicates}), 201

    @routes.put("/papers/<paper_id>")
    @token_required
    def update_paper(username, paper_id):
        user_store = store(username)
        existing = user_store.get_paper(paper_id)
        if existing.get('builtin'):
            raise ExamPreparationError("内置考卷不支持修改，请编辑自己的考卷", 403)
        body = _body()
        # Older clients editing only words must preserve any existing sentence questions.
        body.setdefault("sentences", existing["sentences"])
        name, words, sentences, duplicates = paper_content(username, body)
        paper = user_store.update_paper(paper_id, name, words, sentences)
        return jsonify({"paper": paper, "duplicates_removed": duplicates})

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
        user_store = store(username)
        payload = user_store.get_attempt(attempt_id)
        payload["mistake_count"] = user_store.mistake_count(payload["attempt"]["paper_id"])
        return jsonify(payload)

    @routes.post("/attempts/<attempt_id>/submit")
    @token_required
    @parent_forbidden
    def submit_attempt(username, attempt_id):
        user_store = store(username)
        result = user_store.submit_attempt(attempt_id, _body().get("answers"))
        return jsonify({"result": result, "mistake_count": user_store.mistake_count(result["paper_id"])})

    @routes.post("/papers/<paper_id>/mistake-reviews")
    @token_required
    @parent_forbidden
    def start_mistake_review(username, paper_id):
        _body()
        return jsonify({"attempt": store(username).start_mistake_review(paper_id)}), 201

    app.register_blueprint(routes)
