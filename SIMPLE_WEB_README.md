# 简化版Web应用 - 英语背诵系统

## 概述

这是一个简化版的Web应用，使用Flask替代FastAPI，大大减少了依赖包的复杂度，避免了pydantic-core等需要编译的包。保留了完整的单词背诵功能和多用户支持。

## 主要简化点

1. **移除FastAPI和Pydantic**：使用Flask + 简单JSON验证
2. **移除JWT和python-jose**：使用简单的内存Token系统
3. **减少依赖数量**：从20+个依赖减少到10个左右
4. **避免编译需求**：所有依赖都是纯Python包，无需C编译器

## 快速启动

### 方法1: 使用启动脚本（推荐）

```bash
./start_simple_web.sh
```

### 方法2: 手动安装和启动

1. 安装依赖:
```bash
pip3 install -r requirements-simple.txt
```

2. 启动应用:
```bash
python3 simple_web_app.py
```

### 方法3: Docker运行

```bash
docker build -t english-reciter-simple .
docker run -p 8000:8000 english-reciter-simple
```

## 访问地址

- 主页: http://localhost:8000
- 健康检查: http://localhost:8000/api/health

## API端点

与原始版本兼容的API：

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/auth/register` | POST | 用户注册 |
| `/api/auth/login` | POST | 用户登录 |
| `/api/auth/logout` | POST | 用户退出 |
| `/api/words/status` | GET | 获取学习状态 |
| `/api/words/review` | GET | 获取今日复习列表 |
| `/api/words/practice` | POST | 练习单词 |
| `/api/words/import-json` | POST | 批量导入单词（JSON 数组或 `{ "words": [...] }`） |
| `/api/wordbank/csv` | GET | 内置词库（来自 `static/wordbanks/words.csv`） |
| `/api/wordbank/csv/search` | GET | 在词库中搜索（`q` 参数） |
| `/api/words/mastered` | GET | 获取已掌握单词 |

内置词库文件：`static/wordbanks/words.csv`（唯一数据源；**已加入 `.gitignore`**，不提交仓库，避免部署时 `git pull` 覆盖线上词库。新环境可复制 `static/wordbanks/words.csv.example` 为 `words.csv` 再扩充；线上更新请用管理后台「内置词库 CSV 增量上传」或直接在服务器编辑该文件。）

## 认证方式

使用Bearer Token认证：
1. 登录后获取`access_token`
2. 在请求头中添加：`Authorization: Bearer <token>`
3. Token有效期为24小时

## 文件结构

```
english_reciter/
├── simple_web_app.py      # 简化版Web应用
├── requirements-simple.txt # 简化依赖
├── start_simple_web.sh    # 启动脚本
├── SIMPLE_WEB_README.md   # 本文档
├── static/                # 前端与内置词库
│   ├── index.html
│   ├── css/
│   ├── js/
│   └── wordbanks/words.csv  # 内置词库（运行时文件，见 .gitignore；示例见 words.csv.example）
├── user_data_simple/      # 用户数据目录（新）
│   ├── users.json         # 用户信息
│   └── <username>/        # 每个用户的数据目录
└── reciter.py             # 核心背诵功能（不变）
```

## 数据存储

- 用户信息: `user_data_simple/users.json`
- 用户单词数据: `user_data_simple/<username>/learning_data.json`
- 用户示例库: `user_data_simple/<username>/word_examples.json`

## 注意事项

1. **Token存储**: Token存储在内存中，重启应用后所有Token失效
2. **用户数据**: 使用JSON文件存储，适合小型应用
3. **性能**: 适合小规模使用，大规模应用建议使用数据库
4. **安全性**: 使用SHA256哈希密码，适合学习使用

## 故障排除

### 1. 端口被占用
```bash
# 查找占用端口的进程
lsof -i :8000
# 停止进程
kill -9 <PID>
```

### 2. 依赖安装失败
```bash
# 尝试单独安装主要依赖
pip3 install Flask Flask-CORS
pip3 install gtts playsound prettytable readchar
pip3 install nltk requests python-dateutil
```

### 3. NLTK数据缺失
```bash
python3 -c "import nltk; nltk.download('wordnet')"
```

### 4. 权限问题
```bash
# 使用--user选项
pip3 install --user -r requirements-simple.txt
```

## 从原始版本迁移

如果你已经有原始版本的数据，可以按以下步骤迁移：

1. 停止原始Web应用
2. 备份数据
3. 启动简化版应用
4. 重新注册用户（用户名密码不变）
5. 导入单词文件

简化版使用独立的`user_data_simple`目录，不会影响原始数据。

## 开发

要修改或扩展应用：

1. 编辑`simple_web_app.py`
2. 修改后重启应用即可生效
3. Flask的debug模式支持热重载

## 许可证

与原始项目相同