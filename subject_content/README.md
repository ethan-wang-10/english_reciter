# 学科内容目录

每个学科有独立的 `catalog.json`。语文和数学内容不写入英语的 `builtin_exams/`；数学已加入上海2026秋新版四上练习，语文目录暂为空。

```json
{
  "schema_version": 1,
  "subject": "chinese",
  "papers": [],
  "knowledge": []
}
```

`subject` 为 `chinese` 或 `math`。每条 `papers` 支持 `id`、`title`、`description`、`grade`（1-12，可空）、`term`（1/2，可空）、`exam_kind`（midterm/final/practice）、`question_count`、`time_minutes`、可选的 `knowledge_id` 和 `pdf_url`。ID 最多 96 位，使用小写字母、数字、下划线或连字符，并在同一列表中唯一。

PDF 地址为 `/static/subject-content/<学科>/<文件名>.pdf`，文件名用小写字母、数字、下划线或连字符。真实材料准备好后再创建这些静态文件，当前不发布占位试卷。

每条 `knowledge` 支持 `id`、`title`、`description`、`grade`、`term` 和 `sections`。每个 section 包含 `title` 和文本列表 `items`，页面会安全渲染为可展开的知识点。

题目、标准答案和判分规则不属于公开元数据。可在线作答的考卷设置 `interactive: true`，私有内容位于 `subject_content/<学科>/papers/<考卷id>.json`。该文件由服务端读取，不放进静态目录。当前题型支持选择、填空、计算和应用；数值、分数及短文本采用明确参考答案评分，多栏题可部分得分。详细格式与权限见 `subject_practice.py` 和四上数学材料说明。
