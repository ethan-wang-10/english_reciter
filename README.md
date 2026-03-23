# English Reciter · 英语背诵系统

基于间隔重复的英语单词学习工具，提供**命令行**与 **Web** 两种使用方式：Web 版支持多用户、词库导入与游戏化进度；CLI 版适合本地终端快速复习。

## 功能概览

| 能力 | 说明 |
|------|------|
| 间隔重复 | 按 `config.json` 中的天数阶梯安排复习（默认 1→2→4→7→15→30→60→90 天） |
| 掌握判定 | 连续成功达到 `max_success_count`（默认 8）后移入已掌握列表 |
| 例句 | 本地例句库（`word_examples.json`），离线可用 |
| Web 版 | Flask 应用、注册登录、多用户数据隔离（`user_data_simple/`）、静态前端 |
| CLI 版 | 交互菜单：今日复习、进度、已掌握词汇与巩固 |
| 备份 | 可配置自动备份学习数据到 `backups/` |
| 游戏化 | Web 端积分与成就（见 `gamification.py`） |

## 环境要求

- Python **3.11+**（与 Docker 镜像一致时推荐 3.11）
- 操作系统：Windows / macOS / Linux

## 快速开始

### Web 版（推荐）

```bash
pip install -r requirements-simple.txt
python simple_web_app.py
```

浏览器访问：<http://localhost:8000>。生产环境请设置 `SECRET_KEY`，详见下文与 [DEPLOYMENT.md](DEPLOYMENT.md)。

### Docker

```bash
docker compose up -d
```

默认映射端口以 `docker-compose.yml` 为准；容器内使用 Gunicorn 启动 `simple_web_app`。

### 命令行版

```bash
pip install -r requirements-simple.txt
python reciter.py
```

主菜单包含今日复习、学习进度、已掌握词汇与巩固复习等选项。

## 配置

通过项目根目录的 [config.json](config.json) 调整行为，例如：

- `word_file` / `data_file`：词表与学习数据路径（CLI 默认 `words.txt`、`learning_data.json`）
- `max_success_count`：判定「已掌握」所需连续成功次数（默认 8）
- `review_interval_days`：各成功阶段对应的复习间隔（天）
- `tts_enabled`：是否启用朗读相关能力
- `backup_enabled`、`backup_interval_days`、`max_backups`：备份策略

修改后重启对应进程生效。

## 数据与目录

| 路径 | 含义 |
|------|------|
| `learning_data.json` | CLI 默认学习数据（可从 `words.txt` 初始化） |
| `user_data_simple/<用户名>/` | Web 版每用户独立数据 |
| `static/wordbanks/words.csv` | 内置词库（唯一数据源；改后提交 Git 并部署） |
| `backups/` | 学习数据自动备份（若开启） |
| `reciter.log` | 运行日志 |

## 依赖说明

主要依赖见 [requirements-simple.txt](requirements-simple.txt)（Flask、Flask-CORS、Gunicorn、prettytable、readchar、NLTK 等）。安装 Web 与 CLI 共用该文件即可。

## 更多文档

- [QUICK_START.md](QUICK_START.md) — 启动步骤与使用流程
- [DEPLOYMENT.md](DEPLOYMENT.md) — 生产部署与安全项（如 `SECRET_KEY`）
- [USAGE.md](USAGE.md) — 使用说明（若与当前版本有出入，以代码与配置为准）

## 开源协议

MIT License
