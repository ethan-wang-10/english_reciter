# English Reciter · 英语背诵系统

基于间隔重复的英语单词学习工具，提供**命令行**与 **Web** 两种使用方式：Web 版支持多用户、词库导入与游戏化进度；CLI 版适合本地终端快速复习。

## 功能概览

| 能力 | 说明 |
|------|------|
| 智能每日任务 | 默认每天最多安排 120 个词；默认复习优先，至少 60% 容量保留给复习，新词量按近 7 天正确率、完成率、作答难度与积压自动调整 |
| 自适应复习 | 使用可解释的 `adaptive-sm2-v1` 排期，根据答题结果动态调整间隔（不是 FSRS） |
| 高考多维掌握 | Web 端以英文识义、语境选词和拼写作为掌握门槛；可靠音频可用时，听写作为已掌握词的增强练习 |
| 私有选择题库 | 英文识义与语境选词由服务端判分；支持离线批量生成、断点续跑和缺题时在线补题 |
| 关联复习顺序 | 每日任务选词不变；任务内按本地拼写相似度将近形词排在一起，便于对比记忆 |
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
- `max_success_count`：旧版累计成功进度的兼容上限（默认 8，不再作为「已掌握」门槛）
- `review_interval_days`：旧数据初始化与兼容流程使用的间隔阶梯（天）
- `daily_review_limit`：每日任务总词数上限（默认 120，家长可调整，最大 300）
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
| `user_data_simple/_shared/gaokao_questions_v2.json` | 服务端私有的版本化高考选择题库，包含答案，不得放入 `static/` |
| `user_data_simple/_shared/performance/` | Web 性能采集 JSONL 日志（见 [docs/performance-monitoring.md](docs/performance-monitoring.md)） |
| `backups/` | 学习数据自动备份（若开启） |
| `reciter.log` | 运行日志 |

**已有服务器升级到此版本时**：拉取前请先备份 `static/wordbanks/words.csv` 和 `static/wordbanks/words_v2.json`。`words_v2.json` 若已在服务器由后台生成大量词条，首次部署前可执行 `git update-index --skip-worktree static/wordbanks/words_v2.json`，后续 `scripts/deploy.sh` 会自动保护该本地词库文件，避免 `git pull` 因本地词库变更中断。

## 高考选择题库生成

先配置 `DEEPSEEK_API_KEY` 或 `config.json` 中的 `deepseek_api_key`，在服务器项目根目录执行：

```bash
python3 scripts/generate_gaokao_questions.py --stage generate --level 高中 --dry-run
python3 scripts/generate_gaokao_questions.py --stage generate --level 高中 --batch-size 10 --pause 1
```

`generate` 每批最多处理 10 个词，只调用一次 AI。Prompt 要求模型在输出前逐项代入四个选项并自行消除同义释义和语境歧义；程序随后执行 JSON、选项数量、重复项、答案词形和语境长度等确定性校验。通过本地校验的题目直接写入 `questions`，并标记当前 `generation_prompt_version` 和 `quality_gate`。语义质量采用 Prompt 自检策略，允许少量歧义题进入题库，后续通过抽检和下线机制处理。

VIP 词汇导入每批使用一次组合请求，同时生成 `words_v2.json` 词条和高考题；服务端分别完成确定性校验后直接落盘，不再发起第二次 AI 审查请求。

重新运行 `generate` 时会自动跳过已发布题；配合 `--limit 200` 可分批处理。需要用当前 Prompt 版本完整重建旧题库时使用 `--refresh-prompt-version`，该模式会跳过已经升级完成的题，适合断点续跑。`--stage audit` 和候选区继续保留，仅用于处理旧部署遗留的候选题。

线上服务兼容旧的独立审查题和当前 Prompt 自检题。缺题时当前任务会立即降级为拼写，不在学生请求中调用 AI。升级时不要让旧版批处理脚本与新脚本并行生成同一题库。

```bash
python3 scripts/generate_gaokao_questions.py --stage generate --level "" --batch-size 10 --limit 200 --refresh-prompt-version --pause 1
```

脚本出现生成失败时返回退出码 `2`；直接再次执行同一命令即可续跑。可通过题库 JSON 中的 `questions` 和 `failures` 分别查看已发布和生成失败数量；`candidates`、`rejections` 只保留旧审查流程的兼容数据。

## 依赖说明

主要依赖见 [requirements-simple.txt](requirements-simple.txt)（Flask、Flask-CORS、Gunicorn、prettytable、readchar、NLTK 等）。安装 Web 与 CLI 共用该文件即可。

`en_core_web_sm` spaCy 模型不作为必装依赖，避免国内/离线服务器因 GitHub wheel 下载失败而部署中断；模型缺失时会降级为启发式词形处理。需要 VIP 课文 spaCy 分词时，在网络可用环境执行 `.venv/bin/python -m spacy download en_core_web_sm`。

## 更多文档

- [QUICK_START.md](QUICK_START.md) — 启动步骤与使用流程
- [DEPLOYMENT.md](DEPLOYMENT.md) — 生产部署与安全项（如 `SECRET_KEY`）
- [USAGE.md](USAGE.md) — 使用说明（若与当前版本有出入，以代码与配置为准）

## 开源协议

MIT License
