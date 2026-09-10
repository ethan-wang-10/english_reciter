# 外部生成与独立审核 API

这组管理员 API 允许 Codex 或其他外部执行者领取未生成的单词、生成题目、上传结果，再由独立执行者领取审核任务。生成和三轮审核都通过本接口完成，不调用 DeepSeek。

生产地址为 `https://english.itorange.online`。代码需要先由服务器管理员部署并重启应用；本地接口验证通过不代表生产服务器已经更新。部署前不要领取生产任务。

## 身份与认证

所有接口沿用现有管理员认证：

```http
Authorization: Bearer <admin_access_token>
```

通过现有 `POST /api/admin/login` 接口使用管理员账号密码登录，响应中的 `access_token` 即管理员令牌。浏览器已登录时，令牌保存在该标签页的 `sessionStorage.adminToken`；它不会自动出现在终端环境里。CLI 不自动读取浏览器或代替用户登录。

客户端只接受以下两种令牌来源：

- 环境变量 `ENGLISH_RECITER_ADMIN_TOKEN`。
- `--token-file /private/tmp/english-reciter-admin-token`，文件内容为纯令牌文本，可以有末尾换行。该参数优先于环境变量。

令牌文件属于本机凭据，不应提交到仓库。客户端没有明文 `--token` 参数，不在输出中显示令牌，也不跟随 HTTP 重定向。除 `localhost`、`127.0.0.1`、`::1` 等回环地址外，服务地址必须使用 HTTPS。

已在 Chrome 登录管理员时，可直接展开管理后台中的“题目任务”。工作台使用当前会话调用相同 API，不需要导出令牌；支持查看待办、领取、恢复、续租、释放与提交 JSON。领取标识、上传标识和未提交内容保存在当前标签页，网络失败或重新加载后仍可恢复；“新批次”明确重置这些内容，退出管理会清除草稿。

每个执行者还需要一个 `worker_id`。它与管理员身份不同，用来记录生成者和审核者，例如：

| 工作 | 示例 worker_id | 约束 |
| --- | --- | --- |
| 生成题目 | `codex-generator-20260910` | 接收词库来源与生成规则 |
| 识义盲审 | `codex-recognition-20260910` | 不能与生成者相同 |
| 语境盲审 | `codex-context-20260910` | 不能与生成者或识义审核者相同 |
| 最终讲解审核 | `codex-feedback-20260910` | 不能与生成者相同；建议另用独立执行者 |

这些标识由受信任的管理员提供，不是对执行者独立性的密码学证明。实际审核必须使用未看到生成过程的独立上下文。尤其是语境审核者只应接收语境领取接口返回的数据，不能附上目标词、标准答案、原始生成结果或识义任务。更换 `worker_id` 本身不能让同一上下文的自审变成独立审核。

## 流程与保留策略

1. 生成者领取 `generation`，根据返回的 `instructions` 为每个 `item_id` 生成结果，整批上传。
2. 独立执行者领取 `recognition_blind`，逐项判断中文释义与选项形式，上传布尔数组。
3. 另一独立执行者领取 `context_blind`，仅根据挖空句和英文选项逐项代入，上传语法、语义与句子质量判断。
4. 前两轮都通过后，领取 `feedback`。服务器从通过审核的候选中选出最终四选项，执行者审核翻译和解析；所有检查通过后服务器发布题目。

`generation` 不返回已有题目、已生成候选、拒绝记录或失败记录的词。领取操作会预留该词，现有 DeepSeek 自动流程也不会抢占这些外部任务。尚未上传任何结果的预留可以释放或到期后重新领取。

恢复已有候选、拒绝或失败记录时，领取 `revision`（工作台中的“恢复与修订”）。每项返回 `{item_id, source, previous_records, mode}`：`previous_records` 完整保留各命名空间的原稿、错误与旧审核；`mode=repair` 要求复用原稿修订，只有 `mode=generate` 表示没有原稿可用。提交仍采用生成任务的七个字段。领取时，服务器先将旧记录存入不可变任务快照，再标记外部流程所有权；之后可通过原任务的查询接口读取历史。释放任务不会删除历史记录或撤销外部所有权，继续修订需重新领取。

修订上传会检查当前词库来源与领取时的完整记录，任一变化均返回 `409`，已发布题目不可修订覆盖。修订保存后必须重新完成全部三轮独立审核，旧审核结论不能复用，原作者和修订作者都不能审核该题。租约及幂等标识规则同样适用；请求超时后应查询原任务并复用上传文件，避免重复生成。

领取与上传使用同一词库来源：同键优先采用 v2，CSV 重复键采用最后一条，再按级别筛选。新版来源缺少有效释义或可定位答案的例句时，不回退到旧 CSV 出题。其他进程更新 v2 后，查询会检查文件时间并刷新缓存。

外部流程自身不调用 DeepSeek。服务器若仍有旧 DeepSeek 队列，可用已有配置 `GAOKAO_AUTO_BACKFILL_ENABLED=false` 停止旧队列；外部 API 与工作台继续可用。词库联合导入遇到外部任务拥有的词，也不会再次生成或覆盖其选择题。

生成上传只接受规定字段。字段齐全且匹配任务，但句子或候选未通过结构校验时，服务端保存原始内容为 `manual` 草稿，停止自动生成。语义审核拒绝的题目也保留为待人工处理，不能通过重新领取生成任务覆盖。未知字段、缺失字段、错误单词或错误 `item_id` 属于上传格式错误，整批不应用，修正原有上传文件后重新提交。

本客户端只负责 HTTP 请求，不生成题目、不代填审核结论、不自动重试语义失败、不触发 DeepSeek。已有本地生成结果应继续上传和审核，不能因为请求超时而再次生成。

## 接口约定

接口前缀为 `/api/admin/gaokao/authoring`。所有 POST 请求使用 `Content-Type: application/json`，请求体最多 256 KB。

| 方法与路径 | 参数 |
| --- | --- |
| `GET /pending` | 查询参数 `kind`、`worker_id`、`level`、`limit`，只查看可领取任务，不预留 |
| `POST /claims` | `request_id`、`kind`、`worker_id`、`level`、`limit`、`ttl_seconds` |
| `GET /claims/<job_id>` | 查询参数 `worker_id`，恢复已有任务 |
| `POST /claims/<job_id>/renew` | `worker_id`、`ttl_seconds` |
| `POST /claims/<job_id>/release` | `worker_id` |
| `POST /claims/<job_id>/submissions` | `worker_id`、`submission_id`、`items` |

`kind` 可为 `generation`、`revision`、`recognition_blind`、`context_blind` 或 `feedback`。`limit` 为 1 至 10，租约默认 3600 秒，可设为 60 至 86400 秒。`level` 为空字符串表示所有级别，`高中` 表示高中词库。服务端省略 `level` 时默认高中、省略 `limit` 时默认 5；CLI 显式发送其默认值：全部级别、10 个任务。

`worker_id`、`request_id`、`submission_id` 等标识最长 128 个字符，以英文字母或数字开头，其余字符可使用英文字母、数字、点、下划线、冒号或连字符。

领取响应包含 `job_id`、`kind`、`worker_id`、`request_id`、`status`、`created_at`、`expires_at`、`instructions`、`items` 和规则 `versions`。生成任务的每项是 `{item_id, source}`；两种盲审的每项只有 `{item_id, prompt, options}`。其中 `context_blind` 不返回目标词、来源信息、标准答案或讲解。最终审核项包含 `{item_id, headword, recognition, context}`。

上传必须包含领取响应里的全部 `item_id`，每项恰好一次。外层 `status: completed` 表示这批上传已处理，不等于每题发布成功；查看每项的 `status`：

| 逐题状态 | 含义 |
| --- | --- |
| `awaiting_recognition_blind` | 生成结果已保存，等待识义盲审 |
| `awaiting_context_blind` | 识义盲审通过，等待语境盲审 |
| `awaiting_feedback` | 两轮盲审通过，等待最终讲解审核 |
| `published` | 所有审核通过并已发布 |
| `manual` | 生成结果已保存，但结构不合格，需要人工处理 |
| `rejected` | 审核明确拒绝，保留待人工处理 |

常见错误：`401` 表示缺失或失效令牌，`403` 表示权限或执行者不匹配，`409` 表示租约、并发任务、来源变化或幂等标识冲突，`422` 表示上传内容不符合任务要求。发生 `409` 时先查询原任务，不要重新生成题目。

若旧版本领取的来源与当前词库不一致，部署修复不会改写原任务快照。应保留原始上传文件，核对当前来源后再恢复；需要重新领取时只释放尚未上传内容的空预留，确认新来源兼容后替换上传文件的 `item_id`，复用题目正文。当前规范来源不可领取时，先处理词库数据，不关闭来源校验或重复生成。

## CLI 示例

脚本只需要 Python 标准库，以下示例假定已设置 `ENGLISH_RECITER_ADMIN_TOKEN`。也可在每条命令中增加 `--token-file`。所有选项放在子命令之后。

查看未生成的词：

```bash
python3 scripts/question_authoring_client.py pending \
  --worker-id codex-generator-20260910 --kind generation --level 高中 --limit 3
```

领取一批并保存服务器返回的规则与原始任务：

```bash
python3 scripts/question_authoring_client.py claim \
  --worker-id codex-generator-20260910 --kind generation --level 高中 --limit 3 \
  --request-id generation-20260910-batch-001 --ttl-seconds 3600 \
  --output /private/tmp/generation-claim.json
```

按照返回的 `instructions` 生成题目，将结果组织为以下上传格式。`result` 必须恰好包含展示的七个字段；题干保留完整目标词，服务器负责挖空。此处仅展示一项，实际文件须包含整个领取批次。

```json
{
  "items": [
    {
      "item_id": "服务器返回的原始 item_id",
      "result": {
        "english": "abandon",
        "recognition_distractors": ["坚持", "批评", "记忆", "观察", "收集", "衡量"],
        "recognition_explanation_zh": "abandon 表示放弃或不再继续。",
        "context_sentence": "Because the old bridge had become dangerously unstable, the engineers decided to abandon the entire project after another structural inspection failed.",
        "context_translation_zh": "由于旧桥已变得极不稳定，另一次结构检查未通过后，工程师们决定放弃整个项目。",
        "context_distractors": ["preserve", "examine", "repair", "paint", "measure", "sell"],
        "context_explanation_zh": "桥梁不稳定且检查未通过，因此工程师决定放弃项目。"
      }
    }
  ]
}
```

上述内容用于说明结构，不是已通过审核的题目。实际题干、候选和讲解都必须独立审查。上传文件也可以直接是 `items` 数组，不能额外混入 `worker_id` 或 `submission_id`；这两个标识通过 CLI 参数提供。

```bash
python3 scripts/question_authoring_client.py submit JOB_ID \
  --worker-id codex-generator-20260910 --submission-id generated-20260910-batch-001 \
  --input /private/tmp/generated-results.json --output /private/tmp/generation-receipt.json
```

识义审核者领取自己的任务，并按其返回的选项顺序审核：

```bash
python3 scripts/question_authoring_client.py claim \
  --worker-id codex-recognition-20260910 --kind recognition_blind --level 高中 --limit 3 \
  --request-id recognition-20260910-batch-001 --output /private/tmp/recognition-claim.json
```

识义结果的 `result` 格式如下。数组长度必须等于该项实际 `options` 数量，值必须是 JSON 布尔值；不能将“应为单选题”当成只标记一个可接受项的依据。

```json
{
  "recognition_valid_definition": [false, true, false, false, false, false, false],
  "recognition_parallel_form": [true, true, true, true, true, true, true]
}
```

上传识义结果后，由语境审核者以 `--kind context_blind` 领取。语境结果格式：

```json
{
  "context_grammatical": [true, true, true, true, true, true, true],
  "context_meaning_fits": [false, true, false, false, false, false, false],
  "context_quality": {
    "natural": true,
    "decisive_clues": true,
    "answer_revealed": false,
    "reason_zh": "根据实际题面给出具体核对依据或拒绝原因。"
  }
}
```

两轮盲审通过后以 `--kind feedback` 领取最终讲解审核。服务器会显示筛选后的四选项和待核对的答案标记。结果格式：

```json
{
  "feedback_quality": {
    "recognition_explanation_correct": true,
    "recognition_options_parallel": true,
    "translation_correct": true,
    "context_explanation_correct": true,
    "answer_matches_headword": true,
    "reason_zh": "根据实际最终题目给出具体核对依据或错误原因。"
  }
}
```

以上审核 JSON 只展示格式，布尔值必须来自独立判断。每轮都使用同样的 `{items: [{item_id, result}]}` 外层格式，通过 `submit` 上传；不能直接上传 `approved: true` 跳过检查。

语境审核区分情境证据与直接释义：具体事件、因果或结果可以支持选词，不因线索充分就自动视为泄题；直接给出词义定义、同义改写或翻译才标记 `answer_revealed`。候选池允许多个合理选项，审核者必须如实标记，服务端剔除这些替代答案后才选取三个安全干扰项；最终四选项仍须答案唯一。接受或排除选项都不能依赖额外编造的决定性背景。审核说明参与任务指纹，说明更新后旧任务不能混用新结论，需要重新领取审核。

## 已核对的词库纠错

`scripts/repair_wordbank_headwords.py` 处理已核对的 15 个错误词头：优先保留已有规范词条，只补充缺失的规范词条，并保留词库与学习数据库备份。默认仅生成计划；计划含学习记录快照，应存放在服务器私有目录，不提交到仓库。

```bash
python3 scripts/repair_wordbank_headwords.py --data-dir user_data_simple --plan /private/path/headwords-plan.json
```

应用前必须停止应用，随后以同样参数增加 `--apply --service-stopped`。脚本校验来源文件与学习记录没有变化，保留学习次数、状态、排期和任务引用；发现同一用户已有正确词进度等冲突时会中止，不能自动覆盖。迁移日志支持从同一计划恢复。完成后重启应用，再通过 API 领取纠正后的词条。

## 租约与中断恢复

处理中定期检查 `expires_at`，在到期前续租。下面的操作都使用领取时的相同 `worker_id`：

```bash
python3 scripts/question_authoring_client.py get JOB_ID --worker-id codex-generator-20260910
python3 scripts/question_authoring_client.py renew JOB_ID --worker-id codex-generator-20260910 --ttl-seconds 3600
python3 scripts/question_authoring_client.py release JOB_ID --worker-id codex-generator-20260910
```

尚未开始的任务可以释放；已有本地生成成果时应续租并完成上传，避免丢失工作。已存入服务器的题目不会因为释放或到期被重新生成。

`claim` 的 `request_id` 和 `submit` 的 `submission_id` 可以手动指定。省略时客户端生成 UUID，在请求发出前写入标准错误输出，并在 JSON 响应的 `client_request` 中保留。`--output` 将完整响应写到文件，错误响应也保留这些标识。

如果连接中断、超时或无法确定服务端是否处理成功，使用原来的标识、原来的参数和完全相同的上传内容重试。服务端返回已保存的领取结果或上传回执，不重复生成、发布。同一个标识不能用于修改后的内容。客户端不会自动重试，也不会在模糊失败后自动领取另一批任务。

本地验证可以增加 `--base-url http://127.0.0.1:8000`。本工具的测试使用模拟 HTTP，不调用生产接口：

```bash
pytest -q test_question_authoring_client.py test_question_external_audit.py test_question_authoring_routes.py
```
