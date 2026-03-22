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
  "review_interval_days": [1, 2, 4, 7, 15, 30, 60, 90],
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
