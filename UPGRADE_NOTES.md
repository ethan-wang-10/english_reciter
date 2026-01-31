# 系统升级说明

## 主要改进

### 1. 完全离线模式 ✅
- **移除混元 API 依赖**：不再需要腾讯云 API 密钥
- **本地例句生成**：使用本地例句库 + NLTK WordNet + 智能模板
- **无网络依赖**：完全在本地运行

### 2. 配置外部化 ✅
- **配置文件**：`config.json` 替代硬编码配置
- **可定制化**：轻松修改复习间隔、掌握次数、备份策略等
- **日志级别**：支持 DEBUG/INFO/WARNING/ERROR 等级别

### 3. 日志系统 ✅
- **日志文件**：`reciter.log` 记录所有操作
- **详细记录**：保存成功、加载失败、例句生成等都有日志
- **便于调试**：问题排查更加方便

### 4. 错误处理改进 ✅
- **异常捕获**：所有关键操作都有 try-except 保护
- **优雅降级**：配置文件损坏时使用默认配置
- **数据安全**：数据文件损坏时不会丢失其他数据

### 5. 输入体验优化 ✅
- **退格键修复**：正确处理字符删除
- **实时反馈**：显示已输入字母数量
- **异常处理**：输入错误不会导致程序崩溃

### 6. 类型提示和文档 ✅
- **类型注解**：所有函数参数和返回值都有类型提示
- **文档字符串**：详细说明函数用途和参数
- **代码可读性**：更易于维护和扩展

### 7. 数据备份功能 ✅
- **自动备份**：定期自动备份数据文件
- **备份管理**：自动清理旧备份，最多保留指定数量
- **可配置**：可自定义备份间隔和最大备份数

### 8. 单元测试 ✅
- **测试覆盖**：20 个单元测试覆盖核心功能
- **自动化**：使用 unittest 框架
- **持续集成**：便于代码重构和功能扩展

## 架构改进

### 代码结构
```
Config (配置管理)
  ↓
ExampleGenerator (离线例句生成)
  ↓
Word (数据模型)
  ↓
WordRepository (数据访问层)
  ↓
WordReciter (核心业务逻辑)
  ↓
ReciterCLI (用户界面)
```

### 关键类说明

#### Config
- 管理所有配置项
- 从 `config.json` 加载配置
- 提供默认配置作为后备

#### ExampleGenerator
- 完全离线的例句生成
- 优先使用本地例句库
- 支持 NLTK WordNet（可选）
- 提供智能模板生成

#### WordRepository
- 负责数据持久化
- 处理备份和恢复
- 确保数据完整性

#### WordReciter
- 核心学习引擎
- 实现艾宾浩斯遗忘曲线
- 管理复习轮次和进度

## 配置说明

### config.json 示例
```json
{
  "word_file": "words.txt",
  "data_file": "learning_data.json",
  "example_db": "word_examples.json",
  "max_success_count": 8,
  "tts_enabled": true,
  "max_review_round": 8,
  "review_interval_days": [1, 2, 4, 7, 15, 30, 60, 90],
  "backup_enabled": true,
  "backup_interval_days": 7,
  "max_backups": 10,
  "language": "zh",
  "log_level": "INFO"
}
```

### 配置项说明
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `word_file` | 单词导入文件 | words.txt |
| `data_file` | 学习数据文件 | learning_data.json |
| `example_db` | 例句数据库 | word_examples.json |
| `max_success_count` | 掌握所需成功次数 | 8 |
| `tts_enabled` | 是否启用语音 | true |
| `max_review_round` | 最大复习轮次 | 8 |
| `review_interval_days` | 复习间隔天数 | [1,2,4,7,15,30,60,90] |
| `backup_enabled` | 是否启用备份 | true |
| `backup_interval_days` | 备份间隔天数 | 7 |
| `max_backups` | 最大备份数量 | 10 |
| `language` | 界面语言 | zh |
| `log_level` | 日志级别 | INFO |

## 数据兼容性

### 旧数据兼容
新版本完全兼容旧版本的 `learning_data.json`：
- 自动添加缺失的字段（`review_round`, `review_count`）
- 保留所有现有的学习进度
- 无需数据迁移

## 使用方法

### 运行程序
```bash
python3 reciter.py
```

### 运行测试
```bash
python3 test_reciter.py
```

### 查看日志
```bash
cat reciter.log
```

### 备份数据
数据会自动备份到 `backups/` 目录，格式为：
```
backups/learning_data_backup_2026-01-31.json
```

## 依赖项

### 必需依赖
```bash
pip install prettytable readchar
```

### 可选依赖
```bash
# NLTK WordNet（提供更好的例句）
pip install nltk

# gTTS（虽然代码中保留了导入，但实际使用 macOS 的 say 命令）
pip install gtts
```

### 已移除的依赖
```bash
# 不再需要以下依赖：
# - tencentcloud-hunyuan
# - tencentcloud-common
# - playsound
```

## 注意事项

1. **首次运行**：会自动创建 `config.json` 和 `reciter.log`
2. **备份数据**：建议定期手动备份 `learning_data.json`
3. **日志文件**：日志文件会持续增长，需要定期清理
4. **NLTK 数据**：首次使用 NLTK 时需要下载 WordNet 数据

## 故障排查

### 配置文件损坏
程序会自动使用默认配置，并记录警告日志。

### 数据文件损坏
程序会重置为初始状态，并在日志中记录错误。
建议从 `backups/` 目录恢复备份。

### 日志级别调整
在 `config.json` 中修改 `log_level`：
- `DEBUG`：详细调试信息
- `INFO`：常规操作信息（默认）
- `WARNING`：警告信息
- `ERROR`：仅错误信息

## 后续改进计划

1. 添加学习统计可视化图表
2. 支持更多语言对
3. 提供数据导入/导出功能
4. 添加学习报告生成
5. 优化例句生成算法

## 技术支持

如有问题，请查看 `reciter.log` 日志文件获取详细信息。
