# English Reciter · 英语背诵系统

基于间隔重复的英语单词学习工具，提供**命令行**与 **Web** 两种使用方式：Web 版支持多用户、词库导入与游戏化进度；CLI 版适合本地终端快速复习。

## 功能概览

| 能力 | 说明 |
|------|------|
| 智能每日任务 | 默认每天最多安排 120 个词；默认复习优先，至少 60% 容量保留给复习，新词量按近 7 天正确率、完成率、作答难度与积压自动调整 |
| 自适应复习 | 使用可解释的 `adaptive-sm2-v1` 排期，根据答题结果动态调整间隔（不是 FSRS） |
| 高考多维掌握 | Web 端以英文识义、语境选词和拼写作为掌握门槛；可靠音频可用时，听写作为已掌握词的增强练习 |
| 考前准备 | 创建、编辑考试单词和句子中译英考卷；句子支持多个参考译法，也可单独组卷；可额外开启单词选择题，交卷后查看成绩与错题 |
| 私有选择题库 | 英文识义与语境选词由服务端判分；支持离线批量生成、断点续跑和缺题时在线补题 |
| 外部题目编写 | 管理员 API 支持领取未生成词、上传题目和分阶段独立审核；该流程不调用 DeepSeek |
| 关联复习顺序 | 每日任务选词不变；按本地拼写相似度聚合近形词，RapidFuzz 加速最多 300 词的任务排序 |
| 例句 | 本地例句库（`word_examples.json`），离线可用 |
| Web 版 | Flask 应用、注册登录、多用户数据隔离（`user_data_simple/`）、静态前端 |
| 语文与数学 | 两个独立学科页面共用原有账号，支持切换学科自动登录；预留内置考卷、知识点及学习记录，详见 [学科门户说明](docs/subject_portals.md) |
| 四上数学练习 | 上海2026秋沪教五四新版：期中第1–4单元、期末全册，各32题；基础90分加拓展10分，含在线作答、续答／放弃、分栏评分、知识点和PDF，详见 [材料与计分说明](docs/math-grade4-2026.md) |
| CLI 版 | 交互菜单：今日复习、进度、已掌握词汇与巩固 |
| 备份 | 可配置自动备份学习数据到 `backups/` |
| 游戏化 | 积分、成就、PK 与榜单结算共享 SQLite 事务及持久事件回执 |

## 环境要求

- Python **3.11+**（与 Docker 镜像一致时推荐 3.11）
- 操作系统：Windows / macOS / Linux

## 快速开始

### Web 版（推荐）

```bash
python -m pip install -r requirements-bootstrap.txt
python -m pip install -r requirements-simple.txt
python scripts/build_assets.py
python simple_web_app.py
```

浏览器访问：<http://localhost:8000>。生产环境请设置 `SECRET_KEY`，详见下文与 [DEPLOYMENT.md](DEPLOYMENT.md)。

### Docker

```bash
docker compose up -d
```

默认映射端口以 `docker-compose.yml` 为准；容器内使用 Gunicorn 启动 `simple_web_app`。

### 部署脚本

当前生产验收目标为单机 PM2/Linux，使用 Python 3.11+ 与独立 `.venv/`。部署脚本也兼容 Docker Compose v1/v2 和原生 Gunicorn：

```bash
scripts/deploy.sh --mode auto
scripts/deploy.sh --mode pm2
scripts/deploy.sh --mode docker
```

脚本会确保 `.env` 中存在 `SECRET_KEY`，安装 bootstrap/应用依赖、生成内容哈希资产，并访问 `/api/health` 做健康检查。已有服务器应先停止所有旧 worker、后台任务及维护脚本，备份同一时点的整套数据后再更新，禁止滚动重载或混跑新旧版本。迁移与回滚步骤见 [DEPLOYMENT.md](DEPLOYMENT.md#2026-10-29-可靠性升级)。

### 命令行版

```bash
python -m pip install -r requirements-bootstrap.txt
python -m pip install -r requirements-simple.txt
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

## 考卷编辑与句子中译英

“考前准备”的“选择考卷”列表同时展示个人考卷和内置固定考卷。上海四年级上册 U1-U4 期中卷包含 10 道听力选择及原有的 16 道笔试题，共 26 题，建议 30 分钟完成；U5-U10 期末卷包含 10 道听力选择、12 道语法填空、8 道句型转换和 10 道阅读选择，共 40 题，建议 45 分钟完成。内置题目和录音固定，开始考试直接播放已发布音频；每位学生的成绩和错题独立保存，内置卷不可直接修改。

五年级上册新增 U1-U4 期中卷和 U5-U10 期末复习卷，各 40 题（听力 10、填空 12、句型转换 8、阅读 10），建议 45 分钟完成。知识点对应五四学制新版目录，包括 U2 Traditional games、U3 Amazing plants；期末复习卷范围为后半册，完整校考范围以教师要求为准。两套均配套正常语速听力、知识手册及附答案解析和听力稿的 PDF。四下、五下暂未加入。版本核对和来源见 [五上材料说明](docs/grade5-term1-materials.md)。

考卷的年级编辑、筛选与分组采用上海五四学制：小学一至五年级；初中为预备班（六年级）、初一（七年级）、初二（八年级）、初三（九年级）；高中为高一至高三。保存的年级编号仍为 1-12，已有考卷和成绩继续对应原年级。

点击内置卷旁的“知识点梳理”可阅读对应单元及易错点，并打开 PDF 知识手册与模拟卷；模拟卷后附答案、解析和听力稿。返回选择考卷时保留正在编辑的个人草稿。句型转换按预设正确表达匹配，兼容所列常用缩写和完整否定形式。录音采用正常语速，可反复播放，每段朗读两遍，中间间隔 2 秒。内置卷属于自编复习材料，重点结构与公开原页核对，完整词表和校考范围以课本及教师要求为准。

新增内置卷可在 `builtin_exams/` 中添加同结构 JSON 内容及对应 PDF。听力题使用 `listening_choice`，私有字段 `listening_text` 和 `audio_file` 分别保存录音稿与固定 WAV 路径，作答接口只返回本地 `audio_url`。更改已发布听力内容时使用新的音频文件名，保留历史作答引用与缓存一致性。新的听力开始考试和复习请求携带 `supports_listening: true`；旧页面遇到听力题会提示刷新，已签发的原 16 题期中快照继续按原题判分。

PDF 可在配有 ReportLab、pypdf 和相应中文字体的编辑环境中通过 `scripts/build_grade4_final_materials.py`、`scripts/build_grade4_midterm_materials.py` 重建。固定音频可在 macOS 编辑环境中使用 `scripts/build_grade4_final_audio.py` 重建，发布后的网页播放不依赖本机语音服务。

五上材料可通过 `scripts/build_builtin_exam_materials.py --source builtin_exams/shanghai-grade5-term1-midterm.json --source builtin_exams/shanghai-grade5-term1-final.json` 批量重建；将脚本名换为 `build_builtin_exam_audio.py` 可生成对应固定录音。两者都读取同一份考卷 JSON。

在“考前准备”右侧选择一张考卷，点击“编辑考卷”，即可在左侧修改名称、单词和句子题，再点击“保存修改”。“取消编辑”会恢复原先正在填写的新考卷草稿。已开始的考试保留当时的题目和参考答案，历史成绩继续保留。

未交卷的考试或错题复习会在刷新后自动恢复。答题页可点击“放弃本次考试”（错题复习为“放弃本次复习”），确认后清除本次答题进度并返回选择考卷，不生成成绩，也不更改已保存的错题；点击“继续答题”则保留当前进度。

编辑时，已有单词以气泡显示并按字母排序，点击旁边的 × 可移除。在“添加单词或词组”中输入后点击“添加”或按回车，支持逗号、换行批量添加，重复项自动合并。直接保存时也会自动加入添加框中的内容；增删在保存修改后生效。

保存时若有单词未找到中文释义，编辑区下方会展开词汇导入窗口，并自动填入缺词。VIP 用户可选择难度后点击“导入并保存考卷”，支持后台导入并在完成后继续保存；部分失败时保留考卷草稿，只列出仍需补充的单词。“同时加入待复习列表”默认不勾选。

新建或编辑时，展开“句子中译英（可选）”，点击“添加句子题”，填写中文题干和英文参考答案。例如：

```text
中文题干：我每天步行去学校。
英文参考答案（每行一个）：
I walk to school every day.
I go to school on foot every day.
```

单词可以留空，创建纯句子考卷。每张考卷最多 300 个单词、100 道句子题；每道句子题最多 10 个正确译法，题干和每个译法最多 500 个字符。句子按预设译法匹配，忽略大小写、多余空白、直弯单引号差异和句末句号、问号、感叹号；其他同义表达需要补充到参考答案中。单词仍沿用原有拼写判分。

## 数据与目录

| 路径 | 含义 |
|------|------|
| `learning_data.json` | CLI 默认学习数据（可从 `words.txt` 初始化） |
| `learning_data.learning_state_v2.json` | 自适应排期与每日任务兼容 sidecar；主数据已包含同一状态 |
| `user_data_simple/<用户名>/` | Web 版每用户独立数据 |
| `user_data_simple/users.sqlite3` | 账号持久化，按账户读取/更新；规范化邮箱唯一约束 |
| `user_data_simple/<用户名>/learning.sqlite3` | Web 学习进度与任务状态；旧学习 JSON 是迁移源 |
| `user_data_simple/_shared/rewards.sqlite3` | 积分、挑战、榜单统一事务及持久幂等事件 |
| `user_data_simple/_shared/rate_limits.sqlite3` | 同一主机数据目录下所有 worker 共用限流 |
| `user_data_simple/_shared/import_jobs.sqlite3` | 导入租约、外部请求步骤、已确认响应和候选批次 checkpoint |
| `user_data_simple/_shared/chat_messages.sqlite3` | 聊天历史与索引分页，旧 JSONL 为迁移源 |
| `user_data_simple/<用户名>/exam_preparation.sqlite3` | 考前准备的考卷与考试记录；独立保存，不计入每日任务与学习积分 |
| `static/wordbanks/words.csv` | 内置词库（**不随 Git 发布**；本地从 `words.csv.example` 复制或自备；线上勿被 `git pull` 覆盖，由服务器文件或管理后台「增量上传」维护） |
| `static/wordbanks/words_v2.json` | 新版内置词库（线上运行时数据；仓库只保留空占位，部署脚本会保护服务器本地文件） |
| `user_data_simple/_shared/gaokao_questions_v2.json` | 服务端私有的版本化高考选择题库，包含答案，不得放入 `static/` |
| `user_data_simple/_shared/question_authoring.sqlite3` | 外部题目任务的租约、阶段快照和幂等上传回执，服务端私有 |
| `user_data_simple/_shared/performance/` | Web 性能采集 JSONL 日志（见 [docs/performance-monitoring.md](docs/performance-monitoring.md)） |
| `backups/` | 学习数据自动备份（若开启） |
| `reciter.log` | 运行日志 |

**已有服务器升级到此版本时**：拉取前请先备份 `static/wordbanks/words.csv` 和 `static/wordbanks/words_v2.json`。`words_v2.json` 若已在服务器由后台生成大量词条，首次部署前可执行 `git update-index --skip-worktree static/wordbanks/words_v2.json`，后续 `scripts/deploy.sh` 会自动保护该本地词库文件，避免 `git pull` 因本地词库变更中断。

奖励、学习及聊天迁移后的旧 JSON/JSONL 保留用于核对，不再同步更新，不能当作当前备份；账号 JSON 成功导入后重命名保留。题库仍检查旧 JSON 的变动，不能单独恢复旧文件。回滚必须停止全部写入并恢复同一时点的代码、依赖、配置、用户数据和词库。升级前已裁剪的事件 ID、重复奖励和历史差异不会自动修复；邮箱冲突或损坏账号文件会阻止启动并保留数据，需人工核对归属。

## 本轮架构与性能改进

- 鉴权、学习、词库和聊天拆为四个 Flask Blueprint；前端源文件按账户、会话、学习、词库等模块放在 `static/js/app/`。
- `scripts/build_assets.py` 生成兼容 bundle、内容哈希资产及 manifest，发布不再依赖手工修改查询版本；不要直接编辑生成的 `static/js/app.js`。
- 奖励的账户变动与结算标记在同一 SQLite 事务提交，永久事件回执防止新记录在历史展示裁剪后再次发奖；应用限流在同机 worker 之间共享。
- 导入任务持久化外部请求步骤和候选批次 checkpoint，恢复时复用已确认结果；付费请求结果未知时停止自动重试，必须先人工核查供应商记录和账单。
- 单账户更新在 1 万合成账户下由约 258 ms 降到 0.22 ms；300 词排序的三组本地基准提速约 8-316 倍，输出顺序逐项一致。方法、原始样本和限制见 [性能与依赖专项报告](docs/performance-security-review-2026-10-29.md)。

已部署到 [english.itorange.online](https://english.itorange.online)。Linux 最终回归为 **871 项测试与 12 项子测试全部通过**。双 worker 三轮共 1500 次隔离 HTTP 请求零错误，p95 为 26.56–34.99 ms；真实 OCR、Piper、spaCy、DeepSeek 只读鉴权及 SMTP TLS 登录通过。正式发布及备份恢复记录见 [审查报告](docs/code-review-2026-10-29.md)。这些短时验收结果不代表长期生产容量或 SLA。

## 高考选择题库生成

使用 Codex 等外部执行者生成和审核时，参见[外部生成与独立审核 API](docs/question_authoring_api.md)。可直接使用管理后台“题目任务”工作台，或运行 `scripts/question_authoring_client.py`。该流程沿用管理员认证，支持领取、续租、上传及中断恢复，三阶段审核全部通过后才发布。外部任务与原有 DeepSeek 队列隔离；无需配置 DeepSeek Key。下面的 DeepSeek 生成方式仍可用于其他未被外部流程领取的词。

先配置 `DEEPSEEK_API_KEY` 或 `config.json` 中的 `deepseek_api_key`，在服务器项目根目录执行：

```bash
python3 scripts/generate_gaokao_questions.py --stage generate --level 高中 --dry-run
python3 scripts/generate_gaokao_questions.py --stage generate --level 高中 --batch-size 10 --pause 1
```

手工脚本每次默认最多处理 30 个词，首轮生成每请求最多 10 个词；显式传入 `--limit 0` 才会全量处理。生成目标为每题提供 6 个中文和 6 个英文候选，程序要求至少各有 3 个可用干扰项，校验字段类型、重复项、答案词形、语境长度和唯一空格，再分别请求识义盲审和语境盲审。语境审计只接收挖空句与打乱后的英文选项，不接收目标词、答案标签、中文释义或解析。程序从安全候选中各选取 3 个后，第三次审计校对最终题目的译文、两类解析和答案词形与目标词的对应关系。正常批次需要一次生成与三次审计请求；可以通过 `GAOKAO_AUDIT_MODEL` 单独配置审计模型，默认仍使用生成模型。

当前审计版本为 5，只有通过当前 `generation_prompt_version`、`audit_version` 和 `quality_gate` 且匹配当前词库语义来源的题目才可发布。每个审计阶段保留有效响应，只对缺失、重复 ID、格式异常或截断的题缩小批次重试一次；识义已明确拒绝时停止该题后续审计。输出截断通过 `finish_reason=length` 识别。配置或网络故障会停止本次任务，不拆分成逐题重试，也不消耗题目质量重试额度。

VIP 词汇导入每批使用一次组合请求，同时生成 `words_v2.json` 词条和 6+6 高考题候选。词条立即落盘，题目只写入服务端候选区，不在用户请求内追加审计调用；候选累计后由低峰后台异步审计，通过后才发布。

仅从未生成、且没有任何历史处理痕迹的词允许首次整题生成。已有题目、候选、拒绝记录、原始草稿或失败记录的词不会再次整题生成，`--force` 也遵守此规则。刷新旧题使用 `--refresh-prompt-version`：兼容旧题直接重审，候选从断点续审；原始生成/审计版本保留在 `refreshed_from` 中。`--force` 可将当前版本题纳入复审，不删除原题或强制重新生成。

缺失或不合格的中文译文、解析使用短请求，只返回指定修复字段，保留题干和选项，再完成所需审计。语义拒绝、结构不兼容或来源语义已变化的内容保留并标记待人工处理，不重新生成语境或干扰项。默认跳过历史失败词，优先处理尚未尝试的词；显式使用 `--retry-failed` 才将失败词纳入续审或字段修复，仍不允许整题生成。配合 `--limit 200` 可分批处理，避免少数困难词占满每批限额。

题目兼容性使用语义来源指纹，涵盖词条、释义、词性、原始例句和答案词形；音标、级别或例句中文译文的展示修正不会使已兼容的题目过期。供题、发布和后台审计均核对当前来源；缺少语义指纹的旧题可经重审建立指纹，不因此重新生成。

识义盲审、语境盲审和讲解校对的有效响应逐批保存到候选记录的 `audit_progress`；中断续跑只补未完成的阶段。断点绑定审计版本、模型配置、Prompt 和实际题面，不依赖批次位置。修改译文只会使讲解校对失效；更换模型或审计规则会使相应断点失效。题目仍需通过全部当前质量门才能发布。

脚本按键分块读取题库，达到 `--limit` 后停止扫描，不为打印汇总而解压整份题库和失败记录。SQLite 会增量补齐 `word_key` 索引，使读取单词和保存断点使用索引查找；补索引不改写题目、审计进度或题库版本号。`--stage audit` 也遵守 `--level` 与来源版本限制，默认每请求 10 题；`--stage all` 两个阶段共用 `--limit`，不会在同次运行中重复审计已经处理过的词。运行日志分别显示整题生成、字段修复、审计逻辑请求数，以及旧题复用数、候选续审数和复用的逐题审计阶段数；请求数不包含底层 HTTP 重试，也不等同于实际 token 账单。

Web 直跑时会启动高考题后台调度器；Gunicorn worker 在首次收到请求时启动调度器。当前流水线中已到重试时间的候选与可修复失败题累计达到 30 个，或最老的待处理题已等待 1 小时，调度器就在 DeepSeek 官方低峰时段领取最多 30 个。按最近处理时间排序，已有候选只续审或修复讲解字段，语义拒绝和无法安全复用的内容转人工处理。历史未标记失败和全词库缺题不会被自动领取，历史全量升级仍由手工脚本控制。低峰限制在每次实际 HTTP 请求前检查，包括底层重试；跨入高峰时暂停并保留断点。按 [DeepSeek 官方计费说明](https://api-docs.deepseek.com/quick_start/pricing)，当前高峰为工作日 UTC `01:00-04:00` 和 `06:00-10:00`，即北京时间工作日 `09:00-12:00` 和 `14:00-18:00`，其余时间及周末为低峰。多个 Gunicorn worker 和手工脚本共用跨进程任务锁，不会并行补题。

默认每 5 分钟检查一次、两次任务至少间隔 30 分钟。可通过 `.env` 的 `GAOKAO_AUTO_BACKFILL_ENABLED`、`GAOKAO_AUTO_BACKFILL_CHECK_SECONDS`、`GAOKAO_AUTO_BACKFILL_MIN_INTERVAL_SECONDS` 和 `GAOKAO_AUTO_BACKFILL_MAX_WAIT_SECONDS` 调整。可重试的逐题失败从 30 分钟开始指数退避，累计 5 次后标记 `manual_review_required=true`；确定的语义或结构问题直接转人工处理。服务传输故障和低峰暂停不计入该预算。手工 `--retry-failed` 仍只允许复审或字段修复。最近一次任务状态保存在 `user_data_simple/_shared/gaokao_backfill_state.json`。

线上服务只提供通过当前独立审计质量门的题目。缺题或候选待审时当前任务会立即降级为拼写，不在学生请求中调用 AI。升级时不要让旧版批处理脚本与新脚本并行生成同一题库。

```bash
python3 scripts/generate_gaokao_questions.py --level "" --limit 200 --refresh-prompt-version --dry-run
python3 scripts/generate_gaokao_questions.py --level "" --limit 200 --refresh-prompt-version --off-peak-only --pause 1
```

`--dry-run` 显示复用、续审、字段修复、人工处理和首次生成计划，不调用 AI 或改写题目；首次访问旧 JSON 时，底层存储仍可能初始化 SQLite。`--off-peak-only` 在每次实际 HTTP 请求及重试前检查时段，遇到高峰返回退出码 `4`，保留已生成候选和审计断点；低峰时重跑相同命令即可。处理失败返回 `2`，任务锁被占用返回 `3`，手工中断返回 `130`。题库的 `questions` 保存已发布题目，`candidates` 保存候选、原始草稿和审计进度，`rejections` 保存拒绝内容，`failures` 保存失败原因、重试进度和人工处理标记。

## 依赖说明

主要依赖见 [requirements-simple.txt](requirements-simple.txt)（Flask、Flask-CORS、Gunicorn、NLTK、RapidFuzz、Pillow 等），Web 与 CLI 共用。先安装 [requirements-bootstrap.txt](requirements-bootstrap.txt) 中审核过的 pip/setuptools，再安装应用依赖；离线环境应准备对应平台/Python 的完整 wheel 包。

本轮依据 PyPI/OSV 已确认公告升级 Flask、Werkzeug、Flask-CORS、Gunicorn、certifi、cryptography、NLTK 和 Pillow，最低 Python 版本为 3.11。服务器 `pip-audit` 检查 96 个可审计包，未发现已知漏洞；本地安装的 spaCy 模型 `en-core-web-sm` 不在 PyPI，公告查询跳过，实际推理验证通过。证据见 [installed 审计](docs/installed-dependency-audit-2026-10-29.json)。可用 `python scripts/audit_dependency_versions.py --output audit.json` 复查直接声明，该脚本不解析或审核完整传递依赖。

`en_core_web_sm` spaCy 模型不作为必装依赖，避免国内/离线服务器因 GitHub wheel 下载失败而部署中断；模型缺失时会降级为启发式词形处理。需要 VIP 课文 spaCy 分词时，在网络可用环境执行 `.venv/bin/python -m spacy download en_core_web_sm`。

图片文字识别默认使用 RapidOCR 内置 PP-OCR 模型和 ONNX Runtime，首次识别时懒加载，不需要运行时下载模型。推理默认使用 2 个 CPU 线程，可通过 `OCR_ONNX_THREADS=1..8` 调整。RapidOCR 不可用或识别为空时会自动尝试 Tesseract；Docker 镜像已包含 OpenCV 和 Tesseract 所需的系统包。PM2/原生 Gunicorn 部署还需安装 OpenCV 运行库：Debian/Ubuntu 使用 `apt install libgl1 libglib2.0-0`，OpenCloudOS/RHEL 使用 `dnf install libglvnd-glx glib2`。

图片导入默认勾选「仅提取英文」和「手写增强」。手写模式保留原图读法，并对文字区域裁剪、增强对比度后再次识别；按位置合并结果，用随项目提供的词表及当前词库生成拼写候选。中文释义仅在本地辅助候选排序，正文仅输出英文，短语保持整行。候选不会自动写入正文：可在「识别校对」中查看原图局部、选词、编辑、删除或补充，再点击「应用校对」。关闭「仅提取英文」后返回原始中英文本，手写增强不启用。

OCR 运行时不调用云 API，也不自动下载模型；固定读取 `rapidocr==3.9.2` 包内的 `PP-OCRv6_det_small.onnx`、`PP-OCRv6_rec_small.onnx` 和方向分类模型，缺失时尝试本地 Tesseract。离线部署需事先准备 Python 依赖及模型文件。增强失败保留原图结果。图片提取后的常规词库匹配仍在本地完成；独立的 VIP 新词自动补全功能仍使用 DeepSeek。

## 更多文档

- [全代码审查与优化报告](docs/code-review-2026-10-29.md)：架构、性能、用户体验的已落地修复、验证证据和后续计划
- [QUICK_START.md](QUICK_START.md) — 启动步骤与使用流程
- [DEPLOYMENT.md](DEPLOYMENT.md) — 生产部署与安全项（如 `SECRET_KEY`）
- [USAGE.md](USAGE.md) — 使用说明（若与当前版本有出入，以代码与配置为准）

## 开源协议

MIT License
