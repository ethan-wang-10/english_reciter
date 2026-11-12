# 上海五四学制五年级上册配套材料

## 版本与范围

本次只扩展五年级上册。用户确认新版 U2 为 **Traditional games**、U3 为 **Amazing plants**；四年级下册、五年级下册暂不制作。

出版社公开封面和教材公开预览均标明《义务教育教科书（五·四学制）英语 五年级上册》。出版社封面有“2024 年审核通过”字样，这与实际出版或印刷日期不是同一个概念。用户指定按 2026 新版准备；本次未独立读到版权页的月次、版次，不额外声称“2026 年 7 月第一版”。

沿用已有四上内置卷的分界，期中卷覆盖 U1-U4，期末复习卷覆盖 U5-U10；卷名直接标出范围。期末卷为后半册复习卷，不宣称覆盖全册或某所学校的指定考试范围。

| 单元 | 已核对主题 | 目录中的核心结构 |
| --- | --- | --- |
| U1 | Clubs in our school | I'm / I am going to ... |
| U2 | Traditional games | 祈使句：(Do) ... |
| U3 | Amazing plants | ... look(s) like ... |
| U4 | Together with animals | feel / look / taste + 形容词 |
| U5 | Seeing a doctor | Don't forget to (do) ... |
| U6 | Rise to the challenge | ... was / were ... |
| U7 | Great scientists | This is ..., a / the ... |
| U8 | Useful inventions | It's important to (do) ... |
| U9 | Using computers | as ... as ... |
| U10 | A greener life | It is a good way to (do) ... |

## 来源与使用边界

- 出版社教材导航：<https://www.seph.com.cn/textbook>，五四学制的小学英语分类。
- 五上新版封面：<https://3wm.seph.com.cn/Uploads/2026-09-01/7642fcf65fd642e68219d41ad20ba21e.jpg>。
- 与用户给定单元匹配的原书公开预览：<https://www.scribd.com/document/1056608444/沪教版-五年级上册英语教材>，目录为文件第 4-7 页。通过网站正常公开预览及广告支持的免费阅读入口核对，没有下载受限文件。

只据目录归纳单元、主题词和结构。例句、阅读短文、试题、解析及听力稿均为原创，不转载原书故事、原页图片或原书录音。没有将目录中的主题词宣称为完整默写词表。一般语法复习、词形扩展与阅读策略会在知识点中与目录重点作适当区分。

## 构建与发布

JSON 位于 `builtin_exams/shanghai-grade5-term1-{midterm,final}.json`，同时作为网页考卷、知识点和 PDF 的来源。两套卷均有 10 道听力、12 道语法填空、8 道句型转换、10 道阅读选择；每题按答对比例折算百分制。

```sh
python scripts/build_builtin_exam_materials.py --source builtin_exams/shanghai-grade5-term1-midterm.json --source builtin_exams/shanghai-grade5-term1-final.json
python scripts/build_builtin_exam_audio.py --source builtin_exams/shanghai-grade5-term1-midterm.json --source builtin_exams/shanghai-grade5-term1-final.json
```

PDF 包括知识点梳理、学生卷、答案解析和听力稿。听力保持与现有正常语速版本相同的离线声音参数，不额外放慢；每段朗读两遍，中间间隔 2 秒。录音固定为独立目录中的 WAV，发布后的学生请求不会现场调用语音生成服务。

考卷年级编号继续使用 1-12；仅按上海五四学制显示名称和学段：小学一至五年级，初中从预备班（六年级）开始，随后初一、初二、初三，高中高一至高三。调整不迁移学生答案、成绩或个人考卷编号。
