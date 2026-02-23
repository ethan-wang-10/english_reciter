# 快速使用指南

## 安装依赖

```bash
pip install prettytable readchar
```

可选（提供更好的例句）：
```bash
pip install nltk
```

## 首次运行

```bash
python3 reciter.py
```

首次运行会自动创建：
- `config.json` - 配置文件
- `reciter.log` - 日志文件
- `backups/` - 备份目录

## 主菜单

```
1. 开始今日复习    - 复习到期的单词
2. 查看学习进度    - 查看所有单词的学习状态
3. 导入单词文件    - 从文本文件导入新单词
4. 查看已掌握词汇  - 查看已掌握的单词列表
5. 复习已掌握词汇  - 复习已掌握的单词（防止遗忘）
6. 退出系统        - 退出程序
```

## 导入单词格式

创建 `words.txt` 文件，每行一个单词：
```
apple,苹果
banana,香蕉
cat,猫
```

然后选择菜单项 3 导入。

## 快捷键

- `h` - 显示答案
- `s` - 播放语音
- 回车 - 提交答案
- 退格 - 删除字符

## 配置说明

编辑 `config.json` 可以自定义：
- 复习间隔天数
- 掌握所需次数
- 是否启用语音
- 备份策略
- 日志级别

## 数据备份

数据会自动备份到 `backups/` 目录。

手动恢复备份：
```bash
cp backups/learning_data_backup_2026-01-31.json learning_data.json
```

## 运行测试

```bash
python3 test_reciter.py
```

## 查看日志

```bash
cat reciter.log
```

## 常见问题

**Q: 如何修改复习间隔？**
A: 编辑 `config.json` 中的 `review_interval_days`。

**Q: 如何禁用语音？**
A: 设置 `config.json` 中的 `tts_enabled` 为 `false`。

**Q: 数据丢失了怎么办？**
A: 从 `backups/` 目录恢复最近的备份。

**Q: 如何离线运行？**
A: 本程序已经是完全离线的，无需网络连接。
