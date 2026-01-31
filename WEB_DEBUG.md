# Web 应用启动失败 - Debug 指南

## 问题诊断

**错误信息**: `ModuleNotFoundError: No module named 'fastapi'`

**原因**: 缺少 web 应用所需的 Python 依赖包

## 解决方案

### 方法 1: 使用安装脚本（推荐）

```bash
cd /Users/wanggang/workspace/ai-gen/english_reciter
bash install_web.sh
```

### 方法 2: 手动安装依赖

```bash
cd /Users/wanggang/workspace/ai-gen/english_reciter
pip3 install -r requirements.txt
```

### 方法 3: 单独安装缺失的包

如果上面的方法失败,可以单独安装:

```bash
pip3 install fastapi uvicorn python-jose python-multipart pydantic python-dateutil
pip3 install gtts playsound prettytable readchar nltk requests
```

## 安装完成后启动

```bash
python3 web_app.py
```

启动成功后会看到类似输出:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## 访问地址

- 主页: http://localhost:8000
- API文档: http://localhost:8000/api/docs
- ReDoc文档: http://localhost:8000/api/redoc

## 常见问题

### 1. pip 权限问题
如果遇到权限问题,使用:
```bash
pip3 install --user -r requirements.txt
```

### 2. Python 版本不兼容
确保 Python 版本 >= 3.8:
```bash
python3 --version
```

### 3. NLTK 数据下载
如果遇到 NLTK 相关错误:
```bash
python3 -c "import nltk; nltk.download('wordnet')"
```

### 4. 端口已被占用
如果 8000 端口被占用,修改 `web_app.py` 中的端口号或先停止占用进程:
```bash
lsof -i :8000
kill -9 <PID>
```

### 5. macOS TTS 权限
如果在 macOS 上遇到 TTS 问题,确保有系统音频权限

## 验证安装

运行以下命令检查是否安装成功:

```bash
python3 -c "import fastapi; print('FastAPI OK')"
python3 -c "import uvicorn; print('Uvicorn OK')"
python3 -c "import jose; print('python-jose OK')"
python3 -c "import pydantic; print('Pydantic OK')"
```

## Docker 方式（无需安装本地依赖）

如果本地安装遇到问题,可以使用 Docker:

```bash
docker-compose up -d
```

首次运行会自动构建镜像并安装所有依赖。

## 获取帮助

如果以上方法都无法解决问题,请提供:
1. 完整的错误信息
2. Python 版本: `python3 --version`
3. pip 版本: `pip3 --version`
4. 操作系统版本
