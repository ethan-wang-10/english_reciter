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
python -m pip install -r requirements-bootstrap.txt
python -m pip install -r requirements-simple.txt
python scripts/build_assets.py
```

2. **运行应用**
```bash
python3 simple_web_app.py
```

3. **访问应用**
```
http://localhost:8000
```

### PM2 / Linux 部署（当前生产目标）

使用 Python 3.11+、项目独立 `.venv/` 和同一本地数据目录。PM2 管理一个 Gunicorn 主进程，`WEB_CONCURRENCY` 控制其 worker 数；不要额外开启多个指向不同数据副本的 PM2 实例。

已有服务器必须先按下文“2026-10-29 可靠性升级”完成停服和一致备份，再拉取代码、安装依赖及启动：

```bash
cd /data/english_reciter
pm2 stop english-reciter
# 确认所有旧 worker、后台任务和维护脚本均已退出，再备份数据及配置。
# 若服务器本地词库仍是 tracked 文件，保护它后再拉取。
git update-index --skip-worktree static/wordbanks/words_v2.json
git pull --ff-only
scripts/deploy.sh --mode pm2 --skip-pull
pm2 status english-reciter
curl --fail http://127.0.0.1:8000/api/health
```

首次部署没有已存在的 PM2 应用时跳过 `pm2 stop`。脚本会安装 bootstrap 和应用依赖、构建哈希资产、检查健康状态。健康接口成功只表示进程可响应，不能替代账号、学习、积分、导入和迁移验收。本次存储升级必须完整停止再启动，禁止 `pm2 reload`、滚动升级或新旧 worker 同时写入。

### Docker 部署（可选）

1. **构建镜像**
```bash
docker build -t english-reciter-simple .
```

2. **运行容器**
```bash
docker run -d -p 8000:8000 --env-file .env \
  -v "$(pwd)/user_data_simple:/app/user_data_simple" \
  -v "$(pwd)/static/wordbanks:/app/static/wordbanks" english-reciter-simple
```

3. **访问应用**
```
http://localhost:8000
```

### Docker Compose 部署（可选）

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

- PM2 / 原生 Gunicorn：需要 Python 3.11+；依赖安装到 `.venv/`，不会污染系统 Python。
- Docker：自动兼容 `docker compose`（v2）和 `docker-compose`（v1）；宿主机没有 Python 时会跳过宿主 Python 语法检查，由镜像构建验证依赖。
- 本地词库：`static/wordbanks/words_v2.json` 是线上运行时文件。脚本默认会在拉代码前对它执行 `git update-index --skip-worktree`，从而保留服务器本地词库；其它代码文件如有未提交改动仍会中断部署。若希望严格检查所有 tracked 文件，使用 `--strict-dirty`。
- 依赖安装：脚本先安装 `requirements-bootstrap.txt` 中审核过的 pip/setuptools，再安装应用依赖；离线服务器应提前准备匹配 Python/平台的完整 wheel 包及安装源。`en_core_web_sm` 不作为必装依赖，模型缺失时词形处理会降级；需要时可执行 `scripts/deploy.sh --install-spacy-model` 或 `.venv/bin/python -m spacy download en_core_web_sm`。
- 前端构建：`static/js/app/` 是应用脚本源文件，`scripts/build_assets.py` 生成兼容 bundle、`static/assets/` 内容哈希文件和 `static/asset-manifest.json`。发布时同步生成的 HTML、manifest 和全部资产，运行 `.venv/bin/python scripts/build_assets.py --check` 检查一致性，不再手工修改脚本查询版本。
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

#### 5. 配置 Nginx 反向代理与静态资源

```bash
sudo cp deploy/nginx/00-english-reciter-log-format.conf /etc/nginx/conf.d/
sudo cp deploy/nginx/english-reciter.conf /etc/nginx/conf.d/
sudo nginx -t
sudo systemctl reload nginx
```

模板让 Nginx 直接提供 `/static/`、启用 gzip 和一年 immutable 缓存，并以 JSON 记录
`request_time`、`upstream_response_time`、请求 ID 和应用耗时。部署到不同域名或目录时，
先修改模板中的 `server_name`、证书路径和 `/data/english_reciter`。

### 方案二：使用 CloudBase（腾讯云无服务器）

以下为历史部署参考，当前版本验收目标为 PM2/Linux 单机。现有 SQLite 和本地文件锁不能直接支撑多主机或无持久磁盘实例；使用该方案前需要另行设计并验收数据持久化与一致性。

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

以下模板仅适用于单副本、持久本地卷方案，尚不能作为多主机扩容配置。存储升级采用完整替换策略，不能滚动混跑新旧版本；词库目录也需要持久化。

#### 1. 创建 Kubernetes 配置

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: english-reciter
spec:
  replicas: 1
  strategy:
    type: Recreate
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
          mountPath: /app/user_data_simple
        - name: wordbank-data
          mountPath: /app/static/wordbanks
      volumes:
      - name: user-data
        persistentVolumeClaim:
          claimName: user-data-pvc
      - name: wordbank-data
        persistentVolumeClaim:
          claimName: wordbank-data-pvc

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
| PUBLIC_BASE_URL | 重置密码链接使用的站点地址，如 `https://reciter.example.com`；不可包含账号、路径、查询或片段 | 无；本地开发仅允许回环地址回退 | 生产环境启用密码找回时必需 |
| TRUSTED_PROXY_IPS | 允许提供 `X-Forwarded-For` 的直接代理 IP/CIDR，逗号分隔 | 空，不信任转发头 | 使用反向代理时配置 |
| CHAT_SSE_ENABLED | 启用兼容聊天 SSE 接口；前端默认使用短轮询 | false | 否 |
| SMTP_HOST | SMTP 服务器地址 | 无 | 启用密码找回时必需 |
| SMTP_PORT | SMTP 端口 | SSL 为 465，否则 587 | 否 |
| SMTP_USERNAME | SMTP 登录用户名 | 无 | 由邮件服务决定 |
| SMTP_PASSWORD | SMTP 密码或邮箱授权码 | 无 | 由邮件服务决定 |
| SMTP_FROM_EMAIL | 发件邮箱 | `SMTP_USERNAME` | 启用密码找回时必需 |
| SMTP_FROM_NAME | 发件人名称 | 智能英语背诵 | 否 |
| SMTP_USE_SSL | 使用 SMTP SSL | true | 否 |
| SMTP_STARTTLS | 非 SSL 连接后启用 STARTTLS | `SMTP_USE_SSL=false` 时为 true | 否 |
| SMTP_TIMEOUT | SMTP 连接超时秒数 | 15 | 否 |
| PASSWORD_RESET_TTL_MINUTES | 密码重置链接有效分钟数（1–1440） | 30 | 否 |
| PASSWORD_RESET_COOLDOWN_SECONDS | 同一账号再次发送重置邮件的冷却秒数（0–86400，0 为关闭） | 60 | 否 |
| IMPORT_JOBS_ASYNC | VIP 词汇导入使用持久后台任务 | true | 否 |
| RECITER_CACHE_MAX_USERS | 单 worker 背诵器 LRU 最大用户数 | 24 | 否 |
| RECITER_CACHE_TTL_SECONDS | 背诵器缓存空闲过期秒数 | 1800 | 否 |
| PERF_RETENTION_DAYS | 性能 JSONL 保留天数 | 30 | 否 |
| PIPER_CACHE_MAX_BYTES | Piper WAV 缓存容量上限 | 536870912 | 否 |

### 端口配置

- **应用端口**: 8000
- **健康检查**: http://localhost:8000/api/health

### 数据持久化

用户数据存储在 `user_data_simple/` 目录：
```
user_data_simple/
├── users.sqlite3           # 用户账号；旧 users.json 首次导入后保留备份
├── username1/              # 用户1的数据
│   ├── learning_data.json  # 旧进度迁移源（迁移后保留，不再逐请求重写）
│   ├── learning.sqlite3    # Web 学习进度（SQLite WAL）
│   └── word_examples.json  # 例句库
├── _shared/import_jobs.sqlite3        # 持久导入任务
├── _shared/rewards.sqlite3            # 积分、挑战和榜单统一事务；永久事件回执
├── _shared/rate_limits.sqlite3        # 同机所有 worker 共用的限流额度
├── _shared/gaokao_questions_v2.sqlite3 # 高考题增量存储
├── _shared/chat_messages.sqlite3      # 聊天历史与分页索引
├── _shared/chat_messages.jsonl        # 旧聊天迁移源，迁移后保留
└── username2/              # 用户2的数据
    └── ...
```

迁移后的积分、挑战、榜单 JSON 与学习/聊天迁移源保留用于核对，不再同步更新，不能用作当前状态备份。账号 `users.json` 成功迁移后会重命名为备份文件。题库仍检查旧 JSON 的修改时间和大小，详见升级约束；不能单独覆盖旧题库 JSON。

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

以下为停服备份示例：先停止所有 Web worker、后台任务和维护脚本，再执行归档。在线 SQLite 备份应使用 backup API，不能直接对运行中的 WAL 数据库执行 `tar` 来获得一致快照。

```bash
# 停服后备份数据与可写词库；.env/config.json 应另行妥善备份
tar czf backup_$(date +%Y%m%d).tar.gz user_data_simple/ static/wordbanks/
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

升级前先阅读下面的迁移说明；本次账号、奖励、学习、聊天和导入存储升级需要停止所有旧进程后统一启动新版本。PM2/Linux 使用前述部署流程，Docker 同样必须先停旧容器并完成一致备份。

```bash
# 停止全部旧容器，按下文完成备份后再继续
docker-compose down

# 拉取新代码
git pull

# 重新构建
docker-compose build

# 统一启动新版本
docker-compose up -d
```

### 2026-10-29 可靠性升级

1. **停止写入并备份**：停止所有 Web worker、导入任务进程和相关维护脚本，再备份整个 `user_data_simple/`、`static/wordbanks/`、本地 `config.json` 和 `.env`。SQLite 使用 WAL；停服备份时必须保留仍存在的 `-wal`、`-shm` 文件，不能只复制主 `.sqlite3` 文件。在线备份应使用 SQLite backup API。
2. **配置公开域名**：生产环境启用密码找回时设置 `PUBLIC_BASE_URL=https://实际域名`。未配置时找回接口会报配置错误，避免根据客户端提供的 Host 生成重置链接。
3. **配置代理信任和共享限流**：同机 Nginx 可用 `TRUSTED_PROXY_IPS=127.0.0.1/32,::1/128`；Docker 使用实际代理来源 IP/CIDR。不要填写所有网段，且应通过网络规则阻止客户端绕过代理直连后端。登录、聊天等应用限流已由 `_shared/rate_limits.sqlite3` 统一计数，同一数据目录中的 worker 共用额度；它不等同于多主机全局限流。
4. **持久化两个目录**：Compose 已挂载 `user_data_simple/` 与 `static/wordbanks/`。独立 `docker run` 也必须保留这两个挂载；否则后台新增词条可能在容器重建时丢失。
5. **统一升级存储**：学习、聊天和奖励旧文件按各自迁移标记转入 SQLite，迁移源保留但不再同步写入；账号 JSON 成功导入后保留重命名备份。积分、PK 扣款、月池与榜单奖励现在共用 `_shared/rewards.sqlite3` 事务和持久事件回执。禁止旧脚本继续写奖励 JSON、学习 JSON 或聊天 JSONL；旧代码看不到新库增量。大历史首次迁移会增加耗时，应在维护窗口内检查。无效聊天 JSONL 行沿用旧解析规则跳过，迁移前保留原始备份。题库机制不同：旧 `gaokao_questions_v2.json` 的修改时间或大小变化仍会触发全量重导并替换 SQLite 内容，不能单独覆盖或恢复旧题库文件。
6. **检查家长账号**：新建家长账号使用独立随机初始密码，由管理员在创建时查看一次。已有家长账号的密码不会自动轮换；仍使用旧默认密码的账号应由管理员重置。
7. **检查账号与依赖**：邮箱按去首尾空格及 `casefold()` 生成唯一键，既有邮箱冲突或损坏账号 JSON 会阻止初始化并保留数据。应停止启动、核对日志与备份，人工处理真实归属后重试，不能删除账户或清空数据库绕过检查。Python 最低 3.11；安装 `requirements-bootstrap.txt` 与更新后的 `requirements-simple.txt`，其中 RapidFuzz 是排序性能所需依赖。执行 `.venv/bin/python -m pip check`，并对完整 installed 环境执行 `pip-audit`；单独审核直接依赖不能覆盖传递依赖。
8. **执行验收**：检查 `/api/health`，再验证登录、学习进度、积分和重复奖励、挑战/榜单结算、题库、聊天历史及发消息、词库导入、密码找回和带哈希的静态资产。聊天默认展开每 3 秒、收起每 20 秒轮询，隐藏标签页暂停；SSE 须单独评估连接容量。

如果遇到积分、挑战、榜单或旧题库文件损坏，相关接口会停止修改并返回 503。应保留原文件、查看服务端日志并从一致备份修复，不能通过删除数据文件消除错误。升级前已经裁剪的事件 ID、重复奖励、漏记和历史余额差异不会自动重建或冲正，需要人工核对。

持久导入任务除续租与执行代次校验外，还记录外部请求步骤和候选批次 checkpoint。已确认响应可复用，完成批次可恢复写入；失去租约的执行者不能继续产生有效结果。若付费请求已发出但结果未确认，任务会停止自动重试并要求管理员核对。先核查供应商请求/账单及本地候选，再决定补偿或新建任务；不要直接改任务状态、删步骤记录或反复重新提交来绕过未知结果保护。这不是跨供应商的“恰好一次扣费”保证。

多个 worker 必须使用同一支持 SQLite WAL 与跨进程文件锁的本地数据目录。此配置不等同于多主机部署方案，不应直接将数据库放到不满足这些语义的网络共享目录。

回滚时统一停止新进程与维护脚本，再恢复同一时点的代码、依赖、配置、用户数据和词库整套备份，检查 WAL/SHM 文件完整性后统一启动。不能只回滚代码、只恢复 JSON 或只替换单个数据库；升级后产生的新消息、进度和奖励需先留存并人工制定保留方案。

本次已在 `https://english.itorange.online` 使用 PM2/Linux、Python 3.11.6 完成发布，1 worker、4 threads，仅监听 `127.0.0.1:8000`，信任代理限定 loopback。871 项测试及 12 项子测试通过；三轮双 worker 共 1500 次隔离 HTTP 请求零错误。96 个可审计安装包未发现已知漏洞，本地 spaCy 模型公告查询跳过，真实推理通过。

部署时的一致备份原位于 `/data/english_reciter-backups/review-20261029-99b7a1`，包含旧代码、旧 `.venv`、运行数据、词库和配置。独立恢复的 1229 个文件哈希及 53 个 SQLite 完整性检查通过，再完成 28 账户迁移与私有端口 canary 后正式启用公网。最终 7 份哈希资产、公网健康和认证拒绝通过，28 账户与 602 个词库文件保持一致。该备份已于 2026-10-29 13:22（Asia/Shanghai）按用户要求删除，同时清理两份数据演练副本及验收配置副本，本次旧版本回滚点已移除。删除前复验 22 个活跃 SQLite、28 账户、资产和依赖正常，删除后公网复查通过。证据见 [部署验收记录](docs/deployment-verification-2026-10-29.json) 的 `backup_cleanup` 字段。

当前 `/data/english_reciter/.venv` 指向验证过的 `/data/english_reciter-releases/review-20261029-99b7a1/.venv`；不可移动或清理这个发布目录。旧环境 `venv-before` 已随本次备份删除，当前新环境保持完整。后续发布仍须重新建立一致备份；恢复须停服并同时恢复代码、完整运行数据和配置，发布开放后不能自动覆盖运行数据，须先保留新增数据与外部请求步骤并核对后续写入。

公开静态目录须为 Nginx 可遍历权限（通常 0755），资源文件 0644；私密备份目录和配置继续分别使用 0700、0600。不要将备份的安全 umask 沿用到新公开目录。构建器已显式设置新目录和原子发布文件权限，并有 Linux 严格 umask 回归。上线后的构建器补丁作为独立归档及 SHA-256 记录在部署验收 JSON 的 `post_release_patch` 字段。

本次审查、性能数据、验证范围与后续计划见 [完整审查报告](docs/code-review-2026-10-29.md)。

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

账号、学习、奖励、聊天、任务和限流已经使用 SQLite。未来确有多主机或容量需求时，可以评估将相关事务整体迁到 PostgreSQL/MySQL；不能只替换一个 JSON 文件就认为已获得分布式一致性。以下代码仅展示连接方式，不是可直接采用的迁移实现：

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
