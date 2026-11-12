#!/usr/bin/env python3
"""Build the U1-U4 midterm paper with fixed listening, answers, and scripts."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import KeepTogether, PageBreak, Spacer

from build_grade4_final_materials import (
    ROOT, WritingLine, answer_for, box, build_document, choice_block, p,
    register_fonts, styles,
)


DEFAULT_SOURCE = ROOT / "builtin_exams/shanghai-grade4-u1-u4.json"
OUTPUT_NAME = "上海四年级上册英语期中模拟考卷_U1-U4_含听力与答案.pdf"
STATIC_NAME = "shanghai-grade4-u1-u4-midterm-listening.pdf"


def invitation_parts(reading: list[tuple[int, dict]]) -> tuple[str, list[tuple[int, dict]]]:
    """The web prompts repeat their passage; the print paper shows it once."""
    passages = []
    questions = []
    for number, question in reading:
        parts = question["prompt"].strip().split("\n\n")
        if len(parts) < 3:
            raise ValueError("Invitation questions need an instruction, passage, and question")
        passages.append("\n\n".join(parts[1:-1]))
        questions.append((number, {**question, "question_text": parts[-1]}))
    if len(set(passages)) != 1:
        raise ValueError("The four invitation questions must share the same passage")
    return passages[0], questions


def exam_story(data: dict, st: dict) -> list:
    questions = data["questions"]
    counts = Counter(question["type"] for question in questions)
    assert counts == Counter(listening_choice=10, fill_blank=8, rewrite=4, reading_choice=4), counts
    assert [question["type"] for question in questions] == (
        ["listening_choice"] * 10 + ["fill_blank"] * 8 + ["rewrite"] * 4 + ["reading_choice"] * 4
    ), "Printable section numbering requires the declared question order"
    numbered = list(enumerate(questions, 1))
    listening = [(n, q) for n, q in numbered if q["type"] == "listening_choice"]
    fills = [(n, q) for n, q in numbered if q["type"] == "fill_blank"]
    rewrites = [(n, q) for n, q in numbered if q["type"] == "rewrite"]
    reading = [(n, q) for n, q in numbered if q["type"] == "reading_choice"]
    compact = ParagraphStyle("midterm-question", parent=st["question"], fontSize=10, leading=16, spaceAfter=4)
    answer_body = ParagraphStyle("midterm-answer", parent=st["body"], fontSize=10, leading=16, spaceAfter=4)
    answer_note = ParagraphStyle("midterm-answer-note", parent=st["small"], fontSize=8.8, leading=13.5, spaceAfter=4)

    story = [p("MIDTERM PRACTICE  /  U1-U4", st["eyebrow"]), p("英语期中模拟考卷", st["title"], True)]
    story += [p("上海四年级上册 · U1-U4 · 含听力", st["chapter"], True)]
    story += [p(f"共 26 题，建议 {data['time_minutes']} 分钟完成。", st["body"])]
    story += [p("按答对题数占总题数的比例折算百分制：答对题数 ÷ 26 × 100，保留一位小数。", st["small"])]
    story += [p("班级：____________  姓名：____________  日期：____________", st["body"])]
    story += [box("听力每段朗读两遍，两遍之间间隔 2 秒。可在 english.itorange.online 的“考前准备”中选择对应期中卷播放录音。先完成学生卷，再翻阅后附的参考答案、解析与听力稿。", st["small"])]
    story += [p("一、听力选择（第 1-5 题）", st["section"], True)]
    for number, question in listening[:5]:
        story.append(choice_block(question, number, st))
    story += [PageBreak(), p("一、听力选择（第 6-10 题）", st["chapter"], True), p("听录音，选择最合适的答案。", st["small"])]
    for number, question in listening[5:]:
        story.append(choice_block(question, number, st))

    story += [PageBreak(), p("二、语法填空（第 11-18 题）", st["chapter"], True)]
    story += [p("按提示填写正确形式或合适的词，只写空缺部分。", st["small"])]
    for number, question in fills:
        story.append(KeepTogether([p(f"{number}. {question['prompt']}", compact), Spacer(1, 5)]))
    story += [p("三、句型转换（第 19-22 题）", st["section"], True)]
    for number, question in rewrites:
        story.append(KeepTogether([p(f"{number}. {question['prompt']}", compact), WritingLine(height=18), Spacer(1, 6)]))

    invitation, reading_print = invitation_parts(reading)
    story += [PageBreak(), p("四、邀请卡阅读（第 23-26 题）", st["chapter"], True)]
    story += [p("阅读邀请卡，选择正确答案。", st["small"]), box(invitation, st["body"]), Spacer(1, 14)]
    for number, question in reading_print:
        story.append(choice_block(question, number, st, short_prompt=True))

    story += [PageBreak(), p("ANSWER KEY  /  完成学生卷后再核对", st["eyebrow"]), p("参考答案与解析：听力选择", st["chapter"], True)]
    for number, question in listening:
        story.append(KeepTogether([
            p(f"{number}. {answer_for(question)}", st["body"], True),
            p(question["explanation"], st["small"]), Spacer(1, 5),
        ]))

    story += [PageBreak(), p("ANSWER KEY  /  完成学生卷后再核对", st["eyebrow"]), p("参考答案与解析：填空与句型", st["chapter"], True)]
    for title, group in (("语法填空", fills), ("句型转换", rewrites)):
        story.append(p(title, st["section"], True))
        for number, question in group:
            story.append(KeepTogether([
                p(f"{number}. {answer_for(question, alternate=question['type'] == 'rewrite')}", answer_body, True),
                p(question["explanation"], answer_note), Spacer(1, 3),
            ]))
    story += [PageBreak(), p("ANSWER KEY  /  完成学生卷后再核对", st["eyebrow"]), p("参考答案与解析：邀请卡阅读", st["chapter"], True)]
    for number, question in reading:
        story.append(KeepTogether([
            p(f"{number}. {answer_for(question)}", st["body"], True),
            p(question["explanation"], st["small"]), Spacer(1, 8),
        ]))
    story += [Spacer(1, 18), p("计分与订正", st["section"], True)]
    story += [p("26 题按答对比例折算百分制，不为每题设置固定整数分值。例：答对 13 题为 50 分，答对 26 题为 100 分。", st["body"])]
    story += [p("错题号：________________  主要问题：词汇 / 主语 / 时态 / 结构 / 听力细节", st["body"])]
    story += [p("我需要再练：___________________________________________________", st["body"])]
    story += [p(data["source_note"], st["small"])]

    for part in range(0, len(listening), 5):
        story += [PageBreak(), p("LISTENING SCRIPTS  /  听力稿", st["eyebrow"]), p(f"听力稿（第 {part + 1}-{min(part + 5, len(listening))} 题）", st["chapter"], True)]
        story += [p("音频逐段朗读两遍，间隔 2 秒；下列每段文字只印一次。", st["small"])]
        for number, question in listening[part:part + 5]:
            story.append(KeepTogether([
                p(f"第 {number} 题", st["section"], True),
                p(question["listening_text"], st["body"]), Spacer(1, 5),
            ]))
    return story


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    data = json.loads(args.source.read_text(encoding="utf-8"))
    if data.get("exam_file") != STATIC_NAME:
        raise ValueError("The midterm JSON and printable exam filename must match")
    register_fonts()
    path = ROOT / "output/pdf" / OUTPUT_NAME
    build_document(path, exam_story(data, styles()), "上海四年级上册英语期中模拟考卷 U1-U4", "期中卷 / 含听力", scope="U1-U4")
    static_path = ROOT / "static/exam-preparation" / STATIC_NAME
    static_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, static_path)
    from pypdf import PdfReader
    print(json.dumps({"path": str(path), "pages": len(PdfReader(path).pages)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
