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

手工脚本每次默认最多处理 30 个词，首轮生成每请求最多 10 个词；显式传入 `--limit 0` 才会全量处理。生成目标为每题提供 6 个中文和 6 个英文候选，程序要求至少各有 3 个可用干扰项，校验字段类型、重复项、答案词形、语境长度和唯一空格，再分别请求识义盲审和语境盲审。语境审计只接收挖空句与打乱后的英文选项，不接收目标词、答案标签、中文释义或解析。程序从安全候选中各选取 3 个后，第三次审计校对最终题目的译文、两类解析和答案词形与目标词的对应关系。正常批次需要一次生成与三次审计请求；可以通过 `GAOKAO_AUDIT_MODEL` 单独配置审计模型，默认仍使用生成模型。

当前审计版本为 5，只有通过当前 `generation_prompt_version`、`audit_version` 和 `quality_gate` 的题目才可发布；旧版题目须重新审计或生成。每个审计阶段只对缺失、重复 ID、格式异常或暂时无响应的题重试一次，并缩小批次，保留其他有效结果。生成或语义失败最多修复一次，携带确切失败原因并缩小批次；仅译文或解析不合格时，程序保留题干及候选，只合入指定字段的修正，再执行完整审计。输出截断通过 `finish_reason=length` 识别。

VIP 词汇导入每批使用一次组合请求，同时生成 `words_v2.json` 词条和 6+6 高考题候选。词条立即落盘，题目只写入服务端候选区，不在用户请求内追加审计调用；候选累计后由低峰后台异步审计，通过后才发布。

重新运行 `generate` 时会自动跳过已通过当前生成和审计版本的题，并优先续审同一词库版本的已有候选，不重复生成；配合 `--limit 200` 可分批处理。需要重建旧质量版本时使用 `--refresh-prompt-version`，该模式会跳过已经升级完成的题，适合断点续跑。`--stage audit` 可手工处理词汇导入留下的异步候选。

Web 直跑时会启动高考题后台调度器；Gunicorn worker 在首次收到请求时启动调度器。当前流水线中已到重试时间的候选与失败题累计达到 30 个，或最老的待处理题已等待 1 小时，调度器就在 DeepSeek 官方低峰时段领取最多 30 个。按最近处理时间排序，避免反复失败的题阻塞后续题；已有候选只续审，语义拒绝或生成失败才重生成。历史未标记失败和全词库缺题不会被自动领取，历史全量升级仍由手工脚本控制。按 [DeepSeek 官方计费说明](https://api-docs.deepseek.com/quick_start/pricing)，当前高峰为工作日 UTC `01:00-04:00` 和 `06:00-10:00`，即北京时间工作日 `09:00-12:00` 和 `14:00-18:00`，其余时间及周末为低峰。多个 Gunicorn worker 和手工脚本共用跨进程任务锁，不会并行补题。

默认每 5 分钟检查一次、两次任务至少间隔 30 分钟。可通过 `.env` 的 `GAOKAO_AUTO_BACKFILL_ENABLED`、`GAOKAO_AUTO_BACKFILL_CHECK_SECONDS`、`GAOKAO_AUTO_BACKFILL_MIN_INTERVAL_SECONDS` 和 `GAOKAO_AUTO_BACKFILL_MAX_WAIT_SECONDS` 调整。逐题失败从 30 分钟开始指数退避；累计 5 次持久化失败后标记 `manual_review_required=true`，停止自动调用，仍可使用手工脚本重试。候选的审计异常与重生成失败共用累计预算。最近一次任务状态保存在 `user_data_simple/_shared/gaokao_backfill_state.json`。

线上服务只提供通过当前独立审计质量门的题目。缺题或候选待审时当前任务会立即降级为拼写，不在学生请求中调用 AI。升级时不要让旧版批处理脚本与新脚本并行生成同一题库。

```bash
python3 scripts/generate_gaokao_questions.py --stage generate --level "" --batch-size 10 --limit 200 --refresh-prompt-version --pause 1
```

脚本出现生成失败时返回退出码 `2`；直接再次执行同一命令即可续跑。题库 JSON 中的 `questions` 保存已通过独立审计的题目，`candidates` 保存异步待审候选，`rejections` 保存审计拒绝记录，`failures` 保存等待后续重生成的失败项。

## 依赖说明

主要依赖见 [requirements-simple.txt](requirements-simple.txt)（Flask、Flask-CORS、Gunicorn、prettytable、readchar、NLTK 等）。安装 Web 与 CLI 共用该文件即可。

`en_core_web_sm` spaCy 模型不作为必装依赖，避免国内/离线服务器因 GitHub wheel 下载失败而部署中断；模型缺失时会降级为启发式词形处理。需要 VIP 课文 spaCy 分词时，在网络可用环境执行 `.venv/bin/python -m spacy download en_core_web_sm`。

图片文字识别默认使用 RapidOCR 内置 PP-OCR 模型和 ONNX Runtime，首次识别时懒加载，不需要运行时下载模型。推理默认使用 2 个 CPU 线程，可通过 `OCR_ONNX_THREADS=1..8` 调整。RapidOCR 不可用或识别为空时会自动尝试 Tesseract；Docker 镜像已包含 OpenCV 和 Tesseract 所需的系统包。PM2/原生 Gunicorn 部署还需安装 OpenCV 运行库：Debian/Ubuntu 使用 `apt install libgl1 libglib2.0-0`，OpenCloudOS/RHEL 使用 `dnf install libglvnd-glx glib2`。

图片导入默认勾选「仅提取英文」和「手写增强」。手写模式保留原图读法，并对文字区域裁剪、增强对比度后再次识别；按位置合并结果，用随项目提供的词表及当前词库生成拼写候选。中文释义仅在本地辅助候选排序，正文仅输出英文，短语保持整行。候选不会自动写入正文：可在「识别校对」中查看原图局部、选词、编辑、删除或补充，再点击「应用校对」。关闭「仅提取英文」后返回原始中英文本，手写增强不启用。

OCR 运行时不调用云 API，也不自动下载模型；固定读取 `rapidocr==3.9.2` 包内的 `PP-OCRv6_det_small.onnx`、`PP-OCRv6_rec_small.onnx` 和方向分类模型，缺失时尝试本地 Tesseract。离线部署需事先准备 Python 依赖及模型文件。增强失败保留原图结果。图片提取后的常规词库匹配仍在本地完成；独立的 VIP 新词自动补全功能仍使用 DeepSeek。

## 更多文档

- [QUICK_START.md](QUICK_START.md) — 启动步骤与使用流程
- [DEPLOYMENT.md](DEPLOYMENT.md) — 生产部署与安全项（如 `SECRET_KEY`）
- [USAGE.md](USAGE.md) — 使用说明（若与当前版本有出入，以代码与配置为准）

## 开源协议

MIT License
