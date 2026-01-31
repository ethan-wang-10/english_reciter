# 智能英语背诵系统 - Web 版

## 🌟 特性

- ✅ **多用户支持** - 每个用户独立的学习数据
- ✅ **JWT 认证** - 安全的登录系统
- ✅ **响应式界面** - 支持手机、平板、电脑
- ✅ **跨平台访问** - 通过浏览器访问
- ✅ **云端部署** - 支持 Docker、Kubernetes
- ✅ **数据持久化** - 自动保存学习进度
- ✅ **单词导入** - 批量导入单词文件
- ✅ **学习统计** - 详细的学习进度追踪

## 🚀 快速开始

### 本地运行

1. **安装依赖**
```bash
pip install -r requirements.txt
```

2. **启动服务**
```bash
python3 web_app.py
```

3. **访问应用**
```
浏览器打开: http://localhost:8000
```

### Docker 运行（推荐）

1. **启动容器**
```bash
docker-compose up -d
```

2. **访问应用**
```
浏览器打开: http://localhost:8000
```

3. **查看日志**
```bash
docker-compose logs -f
```

### 腾讯云部署

详细部署指南请查看 [DEPLOYMENT.md](DEPLOYMENT.md)

## 📱 使用指南

### 注册账号

1. 打开应用首页
2. 点击"注册"标签
3. 输入用户名和密码
4. 点击"注册"按钮

### 导入单词

1. 准备单词文件（格式：`英文,中文`）
2. 登录后进入"导入单词"页面
3. 选择文件并点击"导入"

### 开始复习

1. 进入"今日复习"页面
2. 查看例句并输入英文单词
3. 提交答案获得反馈
4. 系统自动安排下次复习

### 查看进度

- **今日复习**: 查看待复习单词和统计
- **学习进度**: 查看所有单词的学习状态
- **已掌握**: 查看已掌握的单词列表

## 🛠️ 技术栈

### 后端
- **FastAPI** - 高性能 Web 框架
- **Pydantic** - 数据验证
- **python-jose** - JWT 认证
- **Uvicorn** - ASGI 服务器

### 前端
- **HTML5** - 页面结构
- **CSS3** - 样式设计
- **JavaScript (ES6+)** - 交互逻辑
- **Fetch API** - HTTP 请求

### 部署
- **Docker** - 容器化
- **Docker Compose** - 编排工具
- **Nginx** - 反向代理（可选）

## 📂 项目结构

```
.
├── web_app.py              # FastAPI 应用主文件
├── reciter.py              # 核心背诵逻辑
├── requirements.txt         # Python 依赖
├── Dockerfile             # Docker 镜像配置
├── docker-compose.yml     # Docker 编排配置
├── static/                # 静态文件
│   ├── index.html        # 主页面
│   ├── css/
│   │   └── style.css    # 样式文件
│   └── js/
│       └── app.js       # JavaScript 逻辑
└── user_data/           # 用户数据（运行时生成）
    ├── users.json       # 用户数据库
    └── {username}/     # 各用户数据
        └── learning_data.json
```

## 🔧 配置说明

### 环境变量

在 `docker-compose.yml` 或 `.env` 文件中配置：

```yaml
environment:
  - SECRET_KEY=your-secret-key-here  # JWT 密钥（必填）
  - TZ=Asia/Shanghai                # 时区
```

### 端口配置

默认端口：`8000`

可在 `docker-compose.yml` 中修改：
```yaml
ports:
  - "8080:8000"  # 修改为 8080
```

## 📊 API 接口

完整 API 文档访问：
- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc

### 主要接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/auth/register` | POST | 用户注册 |
| `/api/auth/login` | POST | 用户登录 |
| `/api/words/status` | GET | 获取学习状态 |
| `/api/words/review` | GET | 获取复习列表 |
| `/api/words/practice` | POST | 练习单词 |
| `/api/words/import` | POST | 导入单词 |
| `/api/words/mastered` | GET | 获取已掌握单词 |

## 🔒 安全建议

1. **生产环境必须修改 SECRET_KEY**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

2. **使用 HTTPS**
- 配置 SSL 证书
- 使用 Let's Encrypt 免费证书

3. **定期备份数据**
```bash
tar czf backup_$(date +%Y%m%d).tar.gz user_data/
```

## 🐛 故障排查

### 问题：无法启动

```bash
# 检查端口占用
lsof -i :8000

# 查看日志
docker-compose logs
```

### 问题：登录失败

```bash
# 检查用户数据
cat user_data/users.json

# 重置用户数据（谨慎）
rm user_data/users.json
```

### 问题：数据丢失

```bash
# 恢复备份
tar xzf backup_YYYYMMDD.tar.gz
```

## 📈 性能优化

1. **启用 Gzip 压缩**
2. **使用 CDN 加速**
3. **配置 Redis 缓存**
4. **使用负载均衡**

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 📧 联系方式

- Email: support@example.com
- GitHub: [项目地址]

---

## 🎯 下一步

- [ ] 添加单词发音功能
- [ ] 支持多人协作学习
- [ ] 添加学习数据可视化
- [ ] 支持导入导出学习进度
- [ ] 添加学习提醒功能
- [ ] 支持移动端 App

---

**开始使用**：```bash
docker-compose up -d
```

**访问应用**：http://localhost:8000
