#!/usr/bin/env python3
"""Build the U5-U10 review handbook and its matching printable final exam.

The versioned built-in exam JSON is the single source for both the web exam
and these PDFs. Run with a Python runtime containing reportlab and pypdf.
"""

from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from html import escape
import json
from pathlib import Path
import re
import shutil

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "builtin_exams/shanghai-grade4-u5-u10-final.json"
REVIEW_NAME = "上海四年级上册英语期末复习手册_U5-U10.pdf"
EXAM_NAME = "上海四年级上册英语期末模拟考卷_U5-U10_含听力与答案.pdf"
STATIC_REVIEW_NAME = "shanghai-grade4-u5-u10-review.pdf"
STATIC_EXAM_NAME = "shanghai-grade4-u5-u10-final.pdf"
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 42
CONTENT_WIDTH = PAGE_WIDTH - MARGIN * 2
TEAL = colors.HexColor("#157C83")
INK = colors.HexColor("#173B4A")
MUTED = colors.HexColor("#647680")
LINE = colors.HexColor("#D6E1E4")
PALE = colors.HexColor("#EFF6F6")


def register_fonts() -> None:
    fonts = {
        "ReviewCN": "/System/Library/Fonts/STHeiti Light.ttc",
        "ReviewCNBold": "/System/Library/Fonts/STHeiti Medium.ttc",
        "ReviewEN": "/System/Library/Fonts/Supplemental/Arial.ttf",
        "ReviewENBold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    }
    for name, filename in fonts.items():
        if not Path(filename).is_file():
            raise RuntimeError(f"Required font not found: {filename}")
        pdfmetrics.registerFont(TTFont(name, filename, subfontIndex=0))


def normalized(text: object) -> str:
    return str(text).replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")


def rich(text: object, bold: bool = False) -> str:
    """Embed both Chinese and Latin font subsets, without interpreting JSON HTML."""
    font = "ReviewENBold" if bold else "ReviewEN"
    cn_font = "ReviewCNBold" if bold else "ReviewCN"
    result = []
    for line in normalized(text).split("\n"):
        chunks = re.split(r"([\x20-\x7e]+)", line)
        result.append("".join(
            f'<font name="{font}">{escape(chunk, quote=False)}</font>'
            if re.fullmatch(r"[\x20-\x7e]+", chunk or "\n")
            else f'<font name="{cn_font}">{escape(chunk, quote=False)}</font>'
            for chunk in chunks
        ))
    return "<br/>".join(result)


def styles() -> dict[str, ParagraphStyle]:
    # Reserve one CJK glyph at the right edge: ReportLab permits hanging punctuation.
    base = dict(fontName="ReviewCN", textColor=INK, wordWrap="CJK", rightIndent=11,
                allowWidows=0, allowOrphans=0)
    return {
        "body": ParagraphStyle("body", fontSize=10.3, leading=17.5, spaceAfter=6, **base),
        "small": ParagraphStyle("small", fontSize=9, leading=14.5, spaceAfter=5, textColor=MUTED,
                                **{key: value for key, value in base.items() if key != "textColor"}),
        "tiny": ParagraphStyle("tiny", fontSize=8.4, leading=13, spaceAfter=4, **base),
        "title": ParagraphStyle("title", fontName="ReviewCNBold", fontSize=23, leading=33,
                                spaceAfter=13, textColor=INK, wordWrap="CJK"),
        "chapter": ParagraphStyle("chapter", fontName="ReviewCNBold", fontSize=17,
                                  leading=25, spaceAfter=13, textColor=INK, wordWrap="CJK"),
        "section": ParagraphStyle("section", fontName="ReviewCNBold", fontSize=11.3,
                                  leading=17, spaceBefore=12, spaceAfter=7, textColor=TEAL, wordWrap="CJK"),
        "question": ParagraphStyle("question", fontSize=10.3, leading=17.5, spaceAfter=6, **base),
        "option": ParagraphStyle("option", fontSize=10, leading=15.5, spaceAfter=0, **base),
        "table": ParagraphStyle("table", fontSize=10, leading=16, spaceAfter=0, **base),
        "tablehead": ParagraphStyle("tablehead", fontName="ReviewCNBold", fontSize=10,
                                    leading=16, textColor=TEAL, wordWrap="CJK"),
        "eyebrow": ParagraphStyle("eyebrow", fontName="ReviewENBold", fontSize=9,
                                  leading=14, textColor=TEAL, spaceAfter=10),
    }


class WritingLine(Flowable):
    def __init__(self, width: float = CONTENT_WIDTH, height: float = 22):
        super().__init__()
        self.width, self.height = width, height

    def draw(self):
        self.canv.setStrokeColor(LINE)
        self.canv.setLineWidth(0.6)
        self.canv.line(0, 5, self.width, 5)


class CheckItem(Flowable):
    def __init__(self, text: str, style: ParagraphStyle):
        super().__init__()
        self.paragraph = Paragraph(rich(text), style)

    def wrap(self, width, height):
        _, ph = self.paragraph.wrap(width - 18, height)
        self.width, self.height = width, ph + 7
        return self.width, self.height

    def draw(self):
        self.canv.setStrokeColor(TEAL)
        self.canv.setLineWidth(0.7)
        self.canv.rect(0, self.height - 13, 7, 7)
        self.paragraph.drawOn(self.canv, 18, 3)


def p(text: object, style: ParagraphStyle, bold: bool | None = None) -> Paragraph:
    if bold is None:
        bold = style.fontName.endswith("Bold")
    return Paragraph(rich(text, bold), style)


def box(text: str, style: ParagraphStyle) -> Table:
    table = Table([[p(text, style)]], colWidths=[CONTENT_WIDTH])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def page_chrome(kind: str, scope: str = "U5-U10"):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(TEAL)
        canvas.setFont("ReviewCN", 8.8)
        canvas.drawString(MARGIN, PAGE_HEIGHT - 29, "上海四年级上册英语 / " + scope)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 29, kind)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, PAGE_HEIGHT - 43, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 43)
        canvas.line(MARGIN, 35, PAGE_WIDTH - MARGIN, 35)
        canvas.setFillColor(MUTED)
        canvas.setFont("ReviewCN", 8)
        canvas.drawString(MARGIN, 22, "自编复习材料 | 范围以课本及老师要求为准")
        canvas.drawRightString(PAGE_WIDTH - MARGIN, 22, f"第 {doc.page} 页")
        canvas.restoreState()
    return draw


def build_document(filename: Path, story: list, title: str, kind: str, scope: str = "U5-U10") -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    document = BaseDocTemplate(
        str(filename), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=62, bottomMargin=46,
        title=title, author="English Reciter", pageCompression=1,
    )
    chrome = page_chrome(kind, scope)
    frame = Frame(MARGIN, 46, CONTENT_WIDTH, PAGE_HEIGHT - 108,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    document.addPageTemplates(PageTemplate(id="content", frames=[frame], onPage=chrome))
    document.build(story)


def knowledge_story(data: dict, st: dict) -> list:
    story = [p("TERM REVIEW  /  U5-U10", st["eyebrow"])]
    story += [p("英语期末复习手册", st["title"], True), p("上海四年级上册 · U5-U10", st["chapter"], True)]
    story += [p(data["edition"], st["small"]), p(data["source_note"], st["small"]), Spacer(1, 10)]
    story += [box(data["knowledge"]["intro"], st["body"]), Spacer(1, 16)]
    story += [p("先看六条学习主线", st["section"], True)]
    units = [section for section in data["knowledge"]["sections"] if re.fullmatch(r"u(?:[5-9]|10)", section["id"], re.I)]
    rows = [[p("单元", st["tablehead"], True), p("主题", st["tablehead"], True)]]
    for section in units:
        match = re.match(r"(U\d+)\s*(.*)", section["title"], re.I)
        rows.append([p(match.group(1) if match else section["id"].upper(), st["table"]),
                     p(match.group(2).strip(" ·:-") if match else section["title"], st["table"])])
    table = Table(rows, colWidths=[65, CONTENT_WIDTH - 65], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9F9")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [table, Spacer(1, 16), p("三轮复习，每轮都要自己说出来", st["section"], True)]
    for text in (
        "第一轮：读主题词汇和短语，再遮住英文，用中文提示默写。",
        "第二轮：用核心结构口头造句，再做填空与句型转换。",
        "第三轮：先听录音找关键词，再独立完成配套模拟卷，最后订正。",
    ):
        story.append(CheckItem(text, st["body"]))
    story += [Spacer(1, 12), p("班级：____________  姓名：____________  复习日期：____________", st["body"])]

    for section in data["knowledge"]["sections"]:
        story += [PageBreak(), p(section["id"].upper(), st["eyebrow"]), p(section["title"], st["chapter"], True)]
        for block in section["blocks"]:
            heading = p(block["title"], st["section"], True)
            items = [p("- " + item, st["body"]) for item in block["items"]]
            if items:
                story.append(KeepTogether([heading, items[0]]))
                story.extend(items[1:])
            else:
                story.append(heading)

    references = data.get("references", [])
    if references:
        story += [Spacer(1, 15), p("资料说明与核对来源", st["section"], True)]
        story += [p("单元范围与重点据公开目录和原页朗读核对；配套例句、题目和听力录音为自编复习练习。完整词表与校考要求请结合课本及老师说明。", st["small"])]
        for reference in references:
            url = reference["url"]
            story.append(p(reference["label"], st["tiny"], True))
            story.append(Paragraph(f'<link href="{escape(url, quote=True)}" color="#157C83">{rich(url)}</link>', st["tiny"]))
    return story


def choice_block(question: dict, number: int, st: dict, *, short_prompt: bool = False) -> KeepTogether:
    prompt = question.get("question_text", question["prompt"]) if short_prompt else question["prompt"]
    caption = p(f"{number}. {prompt}  （    ）", st["question"])
    options = question["options"]
    rows = []
    for index in range(0, len(options), 2):
        row = []
        for option_index in range(index, min(index + 2, len(options))):
            option = options[option_index]
            letter = chr(65 + option_index)
            row.append(p(f"{letter}. {option['text']}", st["option"]))
        if len(row) == 1:
            row.append("")
        rows.append(row)
    table = Table(rows, colWidths=[CONTENT_WIDTH / 2] * 2)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return KeepTogether([caption, table, Spacer(1, 8)])


def answer_for(question: dict, *, alternate: bool = False) -> str:
    if question["type"] in ("listening_choice", "reading_choice"):
        for index, option in enumerate(question["options"]):
            if option["id"] == question["answer"]:
                return f"{chr(65 + index)}. {option['text']}"
        raise ValueError(f"Missing choice answer for {question['id']}")
    values = question.get("accepted_answers", [question["answer"]])
    if alternate and len(values) > 1:
        return values[0] + "\n也接受：" + " / ".join(values[1:])
    return question["answer"]


def exam_story(data: dict, st: dict) -> list:
    questions = data["questions"]
    counts = Counter(question["type"] for question in questions)
    assert counts == Counter(listening_choice=10, fill_blank=12, rewrite=8, reading_choice=10), counts
    assert [question["type"] for question in questions] == (
        ["listening_choice"] * 10 + ["fill_blank"] * 12 + ["rewrite"] * 8 + ["reading_choice"] * 10
    ), "Printable section numbering requires the declared question order"
    numbered = list(enumerate(questions, 1))
    listening = [(n, q) for n, q in numbered if q["type"] == "listening_choice"]
    fills = [(n, q) for n, q in numbered if q["type"] == "fill_blank"]
    rewrites = [(n, q) for n, q in numbered if q["type"] == "rewrite"]
    reading = [(n, q) for n, q in numbered if q["type"] == "reading_choice"]
    story = [p("FINAL PRACTICE  /  U5-U10", st["eyebrow"]), p("英语期末模拟考卷", st["title"], True)]
    story += [p("上海四年级上册 · U5-U10 · 含听力", st["chapter"], True)]
    story += [p(f"共 40 题，每题 2.5 分，满分 100 分。建议 {data['time_minutes']} 分钟完成。", st["body"])]
    story += [p("班级：____________  姓名：____________  日期：____________", st["body"])]
    story += [box("听力每段朗读两遍，两遍之间间隔 2 秒。可在 english.itorange.online 的“考前准备”中选择对应期末卷播放录音。先完成学生卷，再翻阅后附的参考答案、解析与听力稿。", st["small"])]
    story += [p("一、听力选择（第 1-5 题）", st["section"], True)]
    for number, question in listening[:5]:
        story.append(choice_block(question, number, st))
    story += [PageBreak(), p("一、听力选择（第 6-10 题）", st["chapter"], True), p("听录音，选择最合适的答案。", st["small"])]
    for number, question in listening[5:]:
        story.append(choice_block(question, number, st))

    story += [PageBreak(), p("二、语法填空（第 11-22 题）", st["chapter"], True)]
    story += [p("按提示填写正确形式或合适的词，只写空缺部分。", st["small"])]
    for number, question in fills:
        story.append(KeepTogether([p(f"{number}. {question['prompt']}", st["question"]), Spacer(1, 13)]))

    story += [PageBreak(), p("三、句型转换（第 23-30 题）", st["chapter"], True)]
    story += [p("按要求写出完整英文句子，注意主语、动词形式与单词间距。", st["small"])]
    for number, question in rewrites:
        story.append(KeepTogether([p(f"{number}. {question['prompt']}", st["question"]), WritingLine(), Spacer(1, 10)]))

    passages = {item["id"]: item for item in data.get("reading_passages", [])}
    groups = OrderedDict()
    for number, question in reading:
        if "passage_id" not in question or "question_text" not in question:
            raise ValueError("Reading questions require passage_id and question_text for printing")
        groups.setdefault(question["passage_id"], []).append((number, question))
    for index, (passage_id, group) in enumerate(groups.items()):
        passage = passages[passage_id]
        story += [PageBreak(), p("四、阅读选择" + ("（续）" if index else "（第 31-40 题）"), st["chapter"], True)]
        story += [p(f"{chr(65 + index)}. {passage['title']}", st["section"], True), box(passage["text"], st["body"]), Spacer(1, 13)]
        for number, question in group:
            story.append(choice_block(question, number, st, short_prompt=True))

    for title, group in (
        ("参考答案与解析：听力选择", listening),
        ("参考答案与解析：语法填空", fills),
        ("参考答案与解析：句型转换", rewrites),
        ("参考答案与解析：阅读选择", reading),
    ):
        story += [PageBreak(), p("ANSWER KEY  /  完成学生卷后再核对", st["eyebrow"]), p(title, st["chapter"], True)]
        for number, question in group:
            story.append(KeepTogether([
                p(f"{number}. {answer_for(question, alternate=question['type'] == 'rewrite')}", st["body"], True),
                p(question["explanation"], st["small"]), Spacer(1, 5),
            ]))
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
    if data["source_file"] != STATIC_REVIEW_NAME or data["exam_file"] != STATIC_EXAM_NAME:
        raise ValueError("The final-exam JSON and PDF filenames must match")
    register_fonts()
    st = styles()
    output = ROOT / "output/pdf"
    review_path, exam_path = output / REVIEW_NAME, output / EXAM_NAME
    build_document(review_path, knowledge_story(data, st), "上海四年级上册英语期末复习手册 U5-U10", "知识点复习")
    build_document(exam_path, exam_story(data, st), "上海四年级上册英语期末模拟考卷 U5-U10", "模拟卷 / 含听力")
    static = ROOT / "static/exam-preparation"
    static.mkdir(parents=True, exist_ok=True)
    shutil.copy2(review_path, static / STATIC_REVIEW_NAME)
    shutil.copy2(exam_path, static / STATIC_EXAM_NAME)
    from pypdf import PdfReader
    print(json.dumps({
        "review": {"path": str(review_path), "pages": len(PdfReader(review_path).pages)},
        "exam": {"path": str(exam_path), "pages": len(PdfReader(exam_path).pages)},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
