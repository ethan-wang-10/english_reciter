FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 容器内用 root 安装依赖是常见做法；抑制 pip 对 root 的告警（见 https://pip.pypa.io/warnings/venv）
ENV PIP_ROOT_USER_ACTION=ignore

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements-simple.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements-simple.txt

# 复制应用代码
COPY reciter.py .
COPY simple_web_app.py .
COPY config.example.json .
RUN cp config.example.json config.json

# 创建必要的目录
RUN mkdir -p static user_data_simple

# 复制静态文件
COPY static/ ./static/

# 设置环境变量（生产环境请务必通过 compose / k8s 注入强随机 SECRET_KEY）
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

# 暴露端口
EXPOSE 8000

# 使用 Gunicorn，避免 Flask 开发服务器与 debug 风险
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "simple_web_app:app"]
