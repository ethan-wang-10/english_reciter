# Web 版部署文档 (简化版)

## 概述

智能英语背诵系统的简化版 Web 版本支持：
- ✅ 多用户系统
- ✅ Token 身份认证
- ✅ 跨平台访问
- ✅ 可部署到腾讯云
- ✅ Docker 容器化部署
- ✅ 响应式 Web 界面
- ✅ 简化依赖 (Flask 替代 FastAPI)

## 快速开始

### 本地开发

1. **安装依赖**
```bash
pip install -r requirements-simple.txt
```

2. **运行应用**
```bash
python3 simple_web_app.py
```

3. **访问应用**
```
http://localhost:8000
```

### Docker 部署

1. **构建镜像**
```bash
docker build -t english-reciter-simple .
```

2. **运行容器**
```bash
docker run -d -p 8000:8000 -v $(pwd)/user_data_simple:/app/user_data_simple english-reciter-simple
```

3. **访问应用**
```
http://localhost:8000
```

### Docker Compose 部署（推荐）

1. **启动服务**
```bash
docker-compose up -d
```

2. **查看日志**
```bash
docker-compose logs -f
```

3. **停止服务**
```bash
docker-compose down
```

### 一键部署脚本（macOS/Linux）

项目提供 `scripts/deploy.sh`，会按当前环境选择 PM2、Docker Compose 或原生 Gunicorn；也可以显式指定模式。脚本会创建必要运行目录、从 `config.example.json` 初始化本地 `config.json`、确保 `.env` 中存在 `SECRET_KEY` 和 `TZ`，并在重启后访问 `/api/health` 做健康检查。

```bash
# 首次使用（文件已带执行权限时可跳过）
chmod +x scripts/deploy.sh

# 自动选择：优先 PM2，其次 Docker Compose，最后原生 Gunicorn
scripts/deploy.sh

# 显式使用 PM2 / Docker / 原生 Gunicorn
scripts/deploy.sh --mode pm2
scripts/deploy.sh --mode docker
scripts/deploy.sh --mode native

# 兼容非 8000 端口；Docker 模式会映射为 PORT:8000
scripts/deploy.sh --mode native --port 9000
PORT=9000 scripts/deploy.sh --mode docker

# 只检查流程，不拉代码、不安装依赖、不重启服务
scripts/deploy.sh --dry-run --skip-pull --skip-install --no-restart
```

兼容性说明：

- PM2 / 原生 Gunicorn：需要 Python 3.9+，推荐 3.11；依赖安装到 `.venv/`，不会污染系统 Python。
- Docker：自动兼容 `docker compose`（v2）和 `docker-compose`（v1）；宿主机没有 Python 时会跳过宿主 Python 语法检查，由镜像构建验证依赖。
- 本地词库：`static/wordbanks/words_v2.json` 是线上运行时文件。脚本默认会在拉代码前对它执行 `git update-index --skip-worktree`，从而保留服务器本地词库；其它代码文件如有未提交改动仍会中断部署。若希望严格检查所有 tracked 文件，使用 `--strict-dirty`。
- 依赖安装：脚本默认不升级 pip，减少无外网服务器的部署失败概率；`en_core_web_sm` 不再作为必装依赖，模型缺失时词形处理会降级。若服务器可访问外网且需要 VIP 课文 spaCy 分词，可执行 `scripts/deploy.sh --install-spacy-model` 或手动运行 `.venv/bin/python -m spacy download en_core_web_sm`。
- 安全：`.env` 已被 `.gitignore` 忽略；生产环境建议提前写入固定强随机 `SECRET_KEY`，避免重启后密钥变化影响已加密配置。

## 腾讯云部署

### 方案一：使用云服务器（CVM）

#### 1. 准备服务器

```bash
# 安装 Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 安装 Docker Compose
sudo apt-get install docker-compose
```

#### 2. 上传代码

```bash
# 在本地打包项目
tar czf english-reciter.tar.gz .

# 上传到服务器
scp english-reciter.tar.gz root@your-server-ip:/root/

# 在服务器上解压
ssh root@your-server-ip
cd /root
tar xzf english-reciter.tar.gz
cd english-reciter
```

#### 3. 配置环境变量

```bash
# 生成密钥
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# 创建 .env 文件
cat > .env << EOF
SECRET_KEY=$SECRET_KEY
TZ=Asia/Shanghai
EOF
```

#### 4. 启动服务

```bash
docker-compose up -d
```

#### 5. 配置 Nginx 反向代理（可选）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 方案二：使用 CloudBase（腾讯云无服务器）

#### 1. 安装 CloudBase CLI

```bash
npm install -g @cloudbase/cli
```

#### 2. 初始化项目

```bash
cloudbase init
```

#### 3. 配置 cloud.yml

```yaml
version: 2.0
name: english-reciter
description: 智能英语背诵系统

services:
  web:
    container:
      port: 8000
      cpu: 1.0
      mem: 2.0G
    environment:
      - SECRET_KEY=${SECRET_KEY}
```

#### 4. 部署

```bash
cloudbase deploy
```

### 方案三：使用腾讯云容器服务（TKE）

#### 1. 创建 Kubernetes 配置

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: english-reciter
spec:
  replicas: 2
  selector:
    matchLabels:
      app: english-reciter
  template:
    metadata:
      labels:
        app: english-reciter
    spec:
      containers:
      - name: web
        image: your-registry/english-reciter:latest
        ports:
        - containerPort: 8000
        env:
        - name: SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: app-secrets
              key: secret-key
        volumeMounts:
        - name: user-data
          mountPath: /app/user_data
      volumes:
      - name: user-data
        persistentVolumeClaim:
          claimName: user-data-pvc

---
apiVersion: v1
kind: Service
metadata:
  name: english-reciter-service
spec:
  selector:
    app: english-reciter
  ports:
  - port: 80
    targetPort: 8000
  type: LoadBalancer
```

#### 2. 部署到 TKE

```bash
kubectl apply -f deployment.yaml
```

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 | 是否必需 |
|--------|------|--------|----------|
| SECRET_KEY | Token 密钥 | 自动生成 | 否 |
| TZ | 时区 | Asia/Shanghai | 否 |
| PUBLIC_BASE_URL | 重置密码链接使用的公网地址，如 `https://reciter.example.com` | 当前请求地址 | 生产环境建议 |
| SMTP_HOST | SMTP 服务器地址 | 无 | 启用密码找回时必需 |
| SMTP_PORT | SMTP 端口 | SSL 为 465，否则 587 | 否 |
| SMTP_USERNAME | SMTP 登录用户名 | 无 | 由邮件服务决定 |
| SMTP_PASSWORD | SMTP 密码或邮箱授权码 | 无 | 由邮件服务决定 |
| SMTP_FROM_EMAIL | 发件邮箱 | `SMTP_USERNAME` | 启用密码找回时必需 |
| SMTP_FROM_NAME | 发件人名称 | 智能英语背诵 | 否 |
| SMTP_USE_SSL | 使用 SMTP SSL | true | 否 |
| SMTP_STARTTLS | 非 SSL 连接后启用 STARTTLS | `SMTP_USE_SSL=false` 时为 true | 否 |
| SMTP_TIMEOUT | SMTP 连接超时秒数 | 15 | 否 |

### 端口配置

- **应用端口**: 8000
- **健康检查**: http://localhost:8000/api/health

### 数据持久化

用户数据存储在 `user_data_simple/` 目录：
```
user_data_simple/
├── users.json              # 用户数据
├── username1/              # 用户1的数据
│   ├── learning_data.json  # 学习进度
│   └── word_examples.json  # 例句库
└── username2/              # 用户2的数据
    └── ...
```

## API 接口文档

### 认证接口

#### 注册
```
POST /api/auth/register
Content-Type: application/json

{
  "username": "testuser",
  "password": "password123",
  "email": "user@example.com"
}
```

#### 登录
```
POST /api/auth/login
Content-Type: application/x-www-form-urlencoded

username=testuser&password=password123

Response:
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer",
  "username": "testuser"
}
```

### 单词接口

#### 获取学习状态
```
GET /api/words/status
Authorization: Bearer <token>
```

#### 获取复习列表
```
GET /api/words/review
Authorization: Bearer <token>
```

#### 练习单词
```
POST /api/words/practice
Authorization: Bearer <token>
Content-Type: application/json

{
  "word_id": "apple",
  "answer": "apple"
}
```

#### 批量导入单词（JSON）
```
POST /api/words/import-json
Authorization: Bearer <token>
Content-Type: application/json

[ { "english": "apple", "chinese": "苹果" }, ... ]
```

#### 获取已掌握单词
```
GET /api/words/mastered
Authorization: Bearer <token>
```

## 安全建议

1. **修改 SECRET_KEY**
```bash
# 生产环境必须设置强密钥
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
```

2. **使用 HTTPS**
- 在生产环境中配置 SSL 证书
- 使用 Let's Encrypt 免费证书

3. **限制访问**
```bash
# 使用防火墙限制端口访问
sudo ufw allow 80
sudo ufw allow 443
sudo ufw deny 8000
```

4. **定期备份**
```bash
# 备份用户数据
tar czf backup_$(date +%Y%m%d).tar.gz user_data_simple/
```

## 监控和维护

### 查看日志

```bash
# Docker 日志
docker-compose logs -f

# 应用日志
docker-compose exec web tail -f /app/user_data/reciter.log
```

### 健康检查

```bash
# 检查服务状态
curl http://localhost:8000/api/health

# Docker 健康检查
docker-compose ps
```

### 更新应用

```bash
# 拉取新代码
git pull

# 重新构建
docker-compose build

# 重启服务
docker-compose up -d
```

## 无音频服务器配置

如果服务器没有声卡（如云服务器），需要配置应用以兼容无音频环境：

### 1. 禁用 TTS 功能
在服务器上创建或修改配置文件，禁用文本转语音功能：

```bash
# 创建用户配置目录
mkdir -p user_data_simple/your-username

# 创建配置文件
cat > user_data_simple/your-username/config.json << 'EOF'
{
  "tts_enabled": false,
  "max_success_count": 8,
  "max_review_round": 8,
  "review_interval_days": [1, 2, 4, 7, 15, 30, 60],
  "backup_enabled": true,
  "backup_interval_days": 7,
  "max_backups": 10,
  "language": "zh",
  "log_level": "INFO"
}
EOF
```

### 2. 代码自动兼容
最新代码已包含以下兼容性改进：
- **自动检测**：如果 `say` 命令不存在，自动跳过语音播放
- **优雅降级**：TTS 失败不会影响其他功能
- **跨平台支持**：macOS、Linux、Windows 均可运行

### 3. 前端适配
前端会自动处理以下情况：
- 如果语音播放不可用，朗读按钮将显示为禁用状态
- 用户仍可正常使用所有学习功能

### 4. 验证配置
```bash
# 检查 say 命令是否存在
which say

# 如果返回空，表示系统不支持 TTS
# 应用将自动跳过语音功能
```

### 5. Piper 神经语音（推荐，远程用户可听到）

在**服务器**上安装 [Piper](https://github.com/rhasspy/piper) 可执行文件并下载英文 `.onnx` 模型（及同目录下的 `.onnx.json` 配置，若发布包提供）。设置环境变量后重启 Web 进程：

- `PIPER_MODEL`：模型文件路径，例如 `/opt/piper/en_US-lessac-medium.onnx`
- `PIPER_BINARY`（可选）：`piper` 可执行文件的完整路径；不设置则在 `PATH` 中查找

前端在登录后会请求 `/api/tts/capabilities`；若 `piper` 为 `true`，朗读将优先请求 `/api/words/speak-audio` 返回 WAV，在浏览器中播放（不依赖服务器声卡）。未配置 Piper 时仍使用浏览器 Web Speech 或本机 `say` 降级。

命令行版 `reciter.py` 可在 `config.json` 中增加 `piper_model`、`piper_binary`（也可用上述环境变量），优先 Piper，其次 macOS `say`。

## 故障排查

### 问题 1: 无法启动

```bash
# 检查端口占用
netstat -tlnp | grep 8000

# 检查 Docker 日志
docker-compose logs
```

### 问题 2: 数据丢失

```bash
# 检查数据卷挂载
docker volume ls
docker volume inspect <volume-name>

# 恢复备份
tar xzf backup_YYYYMMDD.tar.gz
```

### 问题 3: 性能问题

```bash
# 查看资源使用
docker stats

# 优化 Docker 配置
# 调整内存和 CPU 限制
```

## 扩展功能

### 添加数据库支持

可以集成 PostgreSQL 或 MySQL 替代 JSON 文件存储：

```python
# 使用 SQLAlchemy
from sqlalchemy import create_engine, Column, String, Integer
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()
engine = create_engine('postgresql://user:pass@localhost/db')
```

### 添加 Redis 缓存

```python
import redis

redis_client = redis.Redis(host='localhost', port=6379, db=0)
```

### 添加消息队列

```python
# 使用 Celery 处理异步任务
from celery import Celery

app = Celery('tasks', broker='redis://localhost:6379')
```

## 性能优化

1. **启用 Gzip 压缩**
2. **使用 CDN 加速静态资源**
3. **配置缓存策略**
4. **使用负载均衡**

## 成本估算（腾讯云）

### CVM 方案
- 2核4G 服务器: 约 ¥200/月
- 带宽: 约 ¥100/月
- 总计: 约 ¥300/月

### TKE 方案
- 集群管理: ¥0.02/小时
- Pod 资源: 约 ¥200/月
- 存储卷: 约 ¥50/月
- 总计: 约 ¥350/月

## 技术支持

- GitHub Issues: [项目地址]
- 文档: [文档地址]
- 邮箱: support@example.com

## 许可证

MIT License
