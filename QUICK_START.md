# 🚀 快速启动指南

## 前置要求

- Python 3.11+
- Docker（可选）
- Docker Compose（可选）

## 启动步骤

### 选项 1：Docker 运行（推荐）✨

```bash
# 一键启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 选项 2：本地运行

```bash
# 安装依赖
pip3 install -r requirements.txt

# 启动服务
python3 web_app.py
```

### 选项 3：使用安装脚本

```bash
# 运行安装脚本
bash install_web.sh

# 启动服务
python3 web_app.py
```

## 访问应用

启动成功后，访问：
- **主页**：http://localhost:8000
- **API 文档**：http://localhost:8000/api/docs
- **ReDoc**：http://localhost:8000/api/redoc

## 使用流程

1. **注册账号**
   - 打开 http://localhost:8000
   - 点击"注册"标签
   - 输入用户名和密码
   - 点击"注册"按钮

2. **导入单词**
   - 准备单词文件（格式：`英文,中文`）
   - 进入"导入单词"页面
   - 选择文件并导入

3. **开始复习**
   - 进入"今日复习"页面
   - 查看例句
   - 输入英文单词
   - 提交答案

4. **查看进度**
   - "学习进度"查看所有单词
   - "已掌握"查看已掌握的单词

## 测试数据

准备测试单词文件 `test_words.txt`：
```
apple,苹果
banana,香蕉
cat,猫
dog,狗
elephant,大象
```

## 常见问题

### 端口被占用

```bash
# 查看端口占用
lsof -i :8000

# 杀死进程
kill -9 <PID>
```

### 依赖安装失败

```bash
# 使用国内镜像源
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Docker 启动失败

```bash
# 查看详细日志
docker-compose logs web

# 重新构建
docker-compose build --no-cache
```

## 腾讯云部署

详细部署步骤请查看 [DEPLOYMENT.md](DEPLOYMENT.md)

快速部署命令：

```bash
# 1. 安装 Docker
curl -fsSL https://get.docker.com | sh

# 2. 上传代码
scp -r english-reciter root@your-server:/root/

# 3. 启动服务
cd english-reciter
docker-compose up -d
```

## 下一步

- 查看 [WEB_README.md](WEB_README.md) 了解更多功能
- 查看 [DEPLOYMENT.md](DEPLOYMENT.md) 了解部署选项
- 访问 http://localhost:8000/api/docs 查看 API 文档

---

**开始使用**：```bash
docker-compose up -d```
