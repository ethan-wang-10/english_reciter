# 双词库方案：新词库（多义项 + 词组）与老词库（兼容兜底）

## 已定稿约定

- **新词库文件**：`static/wordbanks/words_v2.json`（JSON 数组，元素为 LexicalEntryV2）。
- **新库已存在同一 `english`（规范化键相同）**：**跳过写入**（不覆盖、不合并）；VIP 加词与管理员批量迁移均按此执行；若需强制覆盖，仅保留**未来可选** `--force` 类运维开关，不在默认路径。

---

## 0. 设计动机

- **不再**在单一 `words.csv` 内用 `lex_format` / 惰性迁移做新旧混存，避免兼容分支拖慢读路径、增加 CSV 嵌 JSON 风险。
- **老词库**保持现有形态与路径（如 `static/wordbanks/words.csv`），**只读兜底**：历史数据、未迁移词条仍可用。
- **新词库**单独文件、单独 schema，原生支持**多义项、词组**，生成与维护走 **DeepSeek**（与现有能力对齐）。
- **查询语义**：**先新后旧**——命中新库则返回新结构；未命中再查老库并返回老结构（调用方需统一封装，见 §4）。

---

## 1. 文件与职责

| 存储 | 路径 | 职责 |
|------|------|------|
| **老词库** | `static/wordbanks/words.csv` | 现有表头与行为不变；**不**要求写入新义项结构；作为 **fallback**。 |
| **新词库** | `static/wordbanks/words_v2.json` | 仅存放 v2 词条；支持 `senses[]`、词组、`entry_kind` 等。 |

**为何新库用 JSON**：多义项嵌套结构避免在 CSV 单元格内嵌 JSON 的转义与人工编辑风险。

---

## 2. 新词库数据模型（权威）

单条 **LexicalEntryV2**（持久化为 `words_v2.json` 顶层数组的元素）：

```text
LexicalEntryV2 {
  english: string              # 主键；规范化后与查询键一致（如小写 strip，与现网一致）
  chinese_summary: string       # 由 senses 生成的展示/兼容摘要（确定性算法，见 §2.2）
  entry_kind: "word" | "phrase"  # 可缺省，由启发式推断（含空格 → phrase 等）
  level: string                 # 可选，与现有一致
  phonetic: string | null       # 行级音标
  senses: Sense[]               # 必填，至少 1 条

  # 例句：第一期可仍用行级 example1…example2_cn（与 simple_web_app 复习拼装对齐）
  example1, example1_form, example1_cn, example2, ...
}

Sense {
  id: string                    # {english_norm}#s{从0开始的序号}
  pos: string | null
  definition_zh: string
  phonetic_override?: string     # 仅异读等极少数情况
}
```

### 2.1 Sense.id

- 固定 **`{english_norm}#s{n}`**，`n` 与 `senses` 数组下标一致，从 0 起。

### 2.2 `chinese_summary`

- 纯函数 **`build_chinese_summary(senses) -> string`**，与多义项顺序、格式固定；**禁止**手写与 `senses` 不一致。

### 2.3 老词库行（对照）

- 仍为扁平：`english`, `chinese`, `level`, `phonetic`, `example*`…  
- **无** `senses`；展示与复习继续走现有 `csv_word_to_review_item` 类逻辑。

---

## 3. 查询语义：先新后旧

### 3.1 统一入口（概念）

**`resolve_wordbank_entry(english) -> ResolvedEntry`**

1. 规范化键 `key = normalize_english(english)`（与现网 `lookup_csv_word` 规则一致）。
2. **查新词库** `words_v2`：若存在 → 返回 `{ source: "v2", entry: LexicalEntryV2 }`。
3. **查老词库** `words.csv`：若存在 → 返回 `{ source: "legacy", row: dict }`。
4. 皆无 → `None`。

### 3.2 调用方约定

- **课文点词、复习卡片、词汇导入结果展示**：均应通过上述解析（或薄封装），避免散落「只读 CSV」。
- **复习用的 `chinese` / 例句**：
  - `v2`：用 `chinese_summary` + 行级例句，或后续扩展从 `senses` 取义项级展示；
  - `legacy`：与当前行为完全一致。

### 3.3 性能

- 新、老词库分别 **mtime 缓存**（类似现有 `load_words_csv`），避免每次查询扫双份全表；新库可额外维护 **`english_norm → 索引`** 或首次加载建 `dict`。

---

## 4. VIP 用户添加词

- **默认只写新词库**：DeepSeek 生成 **LexicalEntryV2**，追加至 `words_v2.json`。
- **若规范化后的 `english` 已在新库存在**：**跳过**（不写入、不覆盖、不合并）；可返回提示「已在新词库」。
- **不写老词库**（除非另有「同步回 legacy」的独立运维操作，本方案不要求）。

---

## 5. 管理员工具：从老词库批量生成新词库

### 5.1 目标

- 输入：老 `words.csv` 中词条（通常按 `english` 去重遍历）。
- 处理：对**尚未出现在 `words_v2` 中的词**，分批调用 **DeepSeek**（与 VIP 同一套生成管线，便于维护一份 prompt/校验）。
- 输出：写入 **`words_v2`**；可配置 **dry-run**、**并发/限速**、**失败重试列表**。

### 5.2 边界

- **跳过**：新库已存在的 `english`（与 VIP 一致：**默认不覆盖**；可选 `--force` 仅作运维例外）。
- **日志**：成功/跳过/失败行号或 `english`，便于补跑。

### 5.3 运行方式（建议）

- **CLI**：`python scripts/migrate_legacy_to_v2_wordbank.py [--dry-run] [--limit N]`  
- 或 **管理后台**受保护路由：仅管理员 token，同逻辑。

---

## 6. DeepSeek 生成（新词库专用）

- Prompt 要求返回 **`senses[]`**（含 `pos`、`definition_zh`），支持**词组**（`english` 可含空格）。
- 服务端：**校验** → **`chinese_summary = build_chinese_summary(senses)`** → 落库；不要求模型同时输出可靠的双格式。
- 校验逻辑单独函数（如 `_is_valid_v2_word_entry`），与 legacy 的 `_is_valid_deepseek_word_entry` 可并存或复用字段子集。

---

## 7. 学习进度数据（`learning_data.json` 等）

- 仍以 **`english` 字符串**关联；**不**因双词库改变 SRS 键。
- **展示释义**：解析时 **优先** `resolve_wordbank_entry` 得到最新 v2/legacy 内容；进度文件内的 `chinese` 若存在，仅作 **fallback**。

---

## 8. 兼容与迁移

- **老词库文件不删除**；未迁移的词继续从 legacy 命中。
- **无需**在单文件内维护 `lex_format` 或惰性升级；迁移进度 = **新词库体积增长** + 管理员工具报表。

---

## 9. 实施阶段建议

| 阶段 | 内容 |
|------|------|
| **1** | 新词库文件 schema + `load_words_v2` + `resolve_wordbank_entry` + 双缓存 |
| **2** | VIP 加词仅写 v2 + DeepSeek v2 prompt 与校验 |
| **3** | 全站读词路径改为 `resolve_wordbank_entry`（课文、复习、lookup） |
| **4** | 管理员批量脚本 / 后台 |
| **5**（可选） | UI 多义项展示、词组课文高亮 |

### 9.1 实现状态（仓库）

- **模块**：`wordbank_v2.py`（`words_v2.json` 读写缓存、`build_chinese_summary`、`finalize_v2_entry_from_deepseek`、追加去重）。
- **查询**：`lookup_csv_word` 已为先 v2 后 legacy；`merge_wordbank_rows_for_search` 供搜索 API；`get_wordbank_english_set` = CSV ∪ v2。
- **VIP 导入**：`/api/wordbank/csv/import-words` 仅向 `words_v2.json` 写入，DeepSeek 使用 `deepseek_generate_word_entries_v2`。
- **批量迁移**：`scripts/migrate_legacy_to_v2_wordbank.py`（`--dry-run` / `--limit`）。
- **单测**：`test_wordbank_v2.py`（不依赖 Flask）。

---

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 双份加载内存 | mtime 缓存；新库索引 dict |
| 两处都有同一 english | 明确规则：**v2 优先**；批量工具默认跳过已存在 v2 |
| 行为不一致 | 集中 `resolve_wordbank_entry`，单测覆盖 v2-only / legacy-only / miss |

---

## 11. 与旧版「单文件 lex_format」文档的关系

本文档 **取代** 原「同一 CSV 内 lex_format + 惰性升级」方案；若代码中仍有历史注释，以**双词库**为准。
