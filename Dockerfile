FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 容器内用 root 安装依赖是常见做法；抑制 pip 对 root 的告警（见 https://pip.pypa.io/warnings/venv）
ENV PIP_ROOT_USER_ACTION=ignore
ENV TZ=Asia/Shanghai
ENV DEBIAN_FRONTEND=noninteractive

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    tzdata \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    tesseract-ocr-eng \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements-simple.txt .
COPY requirements-bootstrap.txt .

# 安装 Python 依赖；en_core_web_sm 模型不强制安装，国内/离线环境可降级运行
RUN pip install --no-cache-dir -r requirements-bootstrap.txt \
    && pip install --no-cache-dir -r requirements-simple.txt

# 复制应用代码
COPY reciter.py .
COPY tts_piper.py .
COPY project_paths.py .
COPY auth_session_store.py .
COPY user_store.py .
COPY gamification.py .
COPY leaderboard_periods.py .
COPY challenges.py .
COPY chat_room.py .
COPY wordbank_v2.py .
COPY app_time.py .
COPY deepseek_policy.py .
COPY gaokao_backfill.py .
COPY gaokao_questions.py .
COPY import_job_store.py .
COPY learning_sqlite_store.py .
COPY ocr_import.py .
COPY performance_store.py .
COPY question_authoring.py .
COPY question_authoring_routes.py .
COPY question_authoring_store.py .
COPY question_external_audit.py .
COPY question_sqlite_store.py .
COPY review_scheduler.py .
COPY storage_utils.py .
COPY reward_store.py .
COPY reward_schema.py .
COPY rate_limit_store.py .
COPY builtin_exam_catalog.py .
COPY exam_grades.py .
COPY exam_mistakes.py .
COPY exam_preparation.py .
COPY exam_preparation_routes.py .
COPY subject_portal.py .
COPY subject_practice.py .
COPY subject_sso.py .
COPY subject_writing.py .
COPY subject_dictation.py .
COPY english_dictation.py .
COPY builtin_exams/ ./builtin_exams/
COPY subject_content/ ./subject_content/
COPY web_auth_routes.py .
COPY web_learning_routes.py .
COPY web_wordbank_routes.py .
COPY web_chat_routes.py .
COPY gunicorn_config.py .
COPY simple_web_app.py .
COPY config.example.json .
RUN cp config.example.json config.json

# 创建必要的目录
RUN mkdir -p static user_data_simple

# 复制静态文件
COPY static/ ./static/
COPY scripts/build_assets.py ./scripts/build_assets.py
RUN python scripts/build_assets.py

# 设置环境变量（生产环境请务必通过 compose / k8s 注入强随机 SECRET_KEY）
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

# 暴露端口
EXPOSE 8000

# 使用 Gunicorn，避免 Flask 开发服务器与 debug 风险
CMD ["gunicorn", "-c", "gunicorn_config.py", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "simple_web_app:app"]
