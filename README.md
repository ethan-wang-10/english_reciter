# English Reciter · 英语背诵系统

基于间隔重复的英语单词学习工具，提供**命令行**与 **Web** 两种使用方式：Web 版支持多用户、词库导入与游戏化进度；CLI 版适合本地终端快速复习。

## 功能概览

| 能力 | 说明 |
|------|------|
| 智能每日任务 | 默认每天最多安排 20 个词，其中新词不超过 5 个；优先处理逾期、薄弱和长期维护词 |
| 自适应复习 | 使用可解释的 `adaptive-sm2-v1` 排期，根据答题结果动态调整间隔（不是 FSRS） |
| 多维掌握 | Web 端同时记录拼写与听写能力；无可用音频的设备会安全降级为拼写门槛 |
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

### 部署脚本

生产环境可使用跨 macOS/Linux 的一键脚本，自动兼容 PM2、Docker Compose v1/v2 和原生 Gunicorn：

```bash
scripts/deploy.sh --mode auto
scripts/deploy.sh --mode pm2
scripts/deploy.sh --mode docker
```

脚本会确保 `.env` 中存在 `SECRET_KEY`，并在重启后访问 `/api/health` 做健康检查；更多参数见 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 命令行版

```bash
pip install -r requirements-simple.txt
python reciter.py
```

主菜单包含今日复习、学习进度、已掌握词汇与巩固复习等选项。

## 配置

首次克隆后请复制模板并自行编辑（**`config.json` 已加入 `.gitignore`，不会被 `git pull` 覆盖**）：

```bash
cp config.example.json config.json
```

通过项目根目录的 `config.json` 调整行为，例如：

- `word_file` / `data_file`：词表与学习数据路径（CLI 默认 `words.txt`、`learning_data.json`）
- `max_success_count`：判定「已掌握」所需连续成功次数（默认 8）
- `review_interval_days`：旧数据初始化与兼容流程使用的间隔阶梯（天）
- `daily_review_limit`：每日任务总词数上限（默认 20）
- `daily_new_word_limit`：每日任务中的新词上限（默认 5）
- `tts_enabled`：是否启用朗读相关能力
- `backup_enabled`、`backup_interval_days`、`max_backups`：备份策略

修改后重启对应进程生效。

## 数据与目录

| 路径 | 含义 |
|------|------|
| `learning_data.json` | CLI 默认学习数据（可从 `words.txt` 初始化） |
| `learning_data.learning_state_v2.json` | 自适应排期与每日任务兼容 sidecar；主数据已包含同一状态 |
| `user_data_simple/<用户名>/` | Web 版每用户独立数据 |
| `static/wordbanks/words.csv` | 内置词库（**不随 Git 发布**；本地从 `words.csv.example` 复制或自备；线上勿被 `git pull` 覆盖，由服务器文件或管理后台「增量上传」维护） |
| `static/wordbanks/words_v2.json` | 新版内置词库（线上运行时数据；仓库只保留空占位，部署脚本会保护服务器本地文件） |
| `user_data_simple/_shared/performance/` | Web 性能采集 JSONL 日志（见 [docs/performance-monitoring.md](docs/performance-monitoring.md)） |
| `backups/` | 学习数据自动备份（若开启） |
| `reciter.log` | 运行日志 |

**已有服务器升级到此版本时**：拉取前请先备份 `static/wordbanks/words.csv` 和 `static/wordbanks/words_v2.json`。`words_v2.json` 若已在服务器由后台生成大量词条，首次部署前可执行 `git update-index --skip-worktree static/wordbanks/words_v2.json`，后续 `scripts/deploy.sh` 会自动保护该本地词库文件，避免 `git pull` 因本地词库变更中断。

## 依赖说明

主要依赖见 [requirements-simple.txt](requirements-simple.txt)（Flask、Flask-CORS、Gunicorn、prettytable、readchar、NLTK 等）。安装 Web 与 CLI 共用该文件即可。

`en_core_web_sm` spaCy 模型不作为必装依赖，避免国内/离线服务器因 GitHub wheel 下载失败而部署中断；模型缺失时会降级为启发式词形处理。需要 VIP 课文 spaCy 分词时，在网络可用环境执行 `.venv/bin/python -m spacy download en_core_web_sm`。

## 更多文档

- [QUICK_START.md](QUICK_START.md) — 启动步骤与使用流程
- [DEPLOYMENT.md](DEPLOYMENT.md) — 生产部署与安全项（如 `SECRET_KEY`）
- [USAGE.md](USAGE.md) — 使用说明（若与当前版本有出入，以代码与配置为准）

## 开源协议

MIT License
