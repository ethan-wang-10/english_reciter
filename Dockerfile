FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY reciter.py .
COPY web_app.py .
COPY config.json .

# 创建必要的目录
RUN mkdir -p static user_data

# 复制静态文件
COPY static/ ./static/

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV SECRET_KEY=${SECRET_KEY:-default-secret-key-change-in-production}

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8000"]
