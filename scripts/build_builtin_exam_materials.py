#!/usr/bin/env python3
"""Build review handbooks and printable papers from versioned exam catalogs.

Usage: python scripts/build_builtin_exam_materials.py --source builtin_exams/PAPER.json
The same catalog supplies the online questions, answer key and listening scripts.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from html import escape
import json
from pathlib import Path
import re
import shutil

from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Spacer, Table, TableStyle

from build_grade4_final_materials import (
    CONTENT_WIDTH, LINE, MARGIN, MUTED, PAGE_HEIGHT, PAGE_WIDTH, PALE, ROOT,
    TEAL, CheckItem, WritingLine, answer_for, box, choice_block, p, register_fonts,
    rich, styles,
)


def labels(data: dict) -> tuple[str, str, str]:
    grade = {4: '四', 5: '五'}.get(data['grade'], str(data['grade']))
    semester = f"上海{grade}年级{'上' if data['term'] == 1 else '下'}册英语"
    kind = '期中' if '期中' in data['name'] else '期末'
    return semester, kind, data['scope']


def document(path: Path, story: list, title: str, header: str, kind: str) -> None:
    def chrome(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(TEAL)
        canvas.setFont('ReviewCN', 8.5)
        canvas.drawString(MARGIN, PAGE_HEIGHT - 29, header)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 29, kind)
        canvas.setStrokeColor(LINE)
        canvas.line(MARGIN, PAGE_HEIGHT - 43, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 43)
        canvas.line(MARGIN, 35, PAGE_WIDTH - MARGIN, 35)
        canvas.setFillColor(MUTED)
        canvas.setFont('ReviewCN', 8)
        canvas.drawString(MARGIN, 22, '自编复习材料 | 范围以课本及老师要求为准')
        canvas.drawRightString(PAGE_WIDTH - MARGIN, 22, f'第 {doc.page} 页')
        canvas.restoreState()

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
                          leftMargin=MARGIN, rightMargin=MARGIN, topMargin=62,
                          bottomMargin=46, title=title, author='English Reciter', pageCompression=1)
    frame = Frame(MARGIN, 46, CONTENT_WIDTH, PAGE_HEIGHT - 108,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id='content', frames=[frame], onPage=chrome))
    doc.build(story)


def knowledge_story(data: dict, st: dict) -> list:
    semester, kind, scope = labels(data)
    story = [p(f'TERM REVIEW / {scope}', st['eyebrow']),
             p(f'英语{kind}知识点梳理', st['title'], True),
             p(f'{semester} · {scope}', st['chapter'], True),
             p(data['edition'], st['small']), p(data['source_note'], st['small']),
             Spacer(1, 10), box(data['knowledge']['intro'], st['body']), Spacer(1, 14)]
    units = [section for section in data['knowledge']['sections']
             if re.fullmatch(r'(?:m\d+[-_]?)?u\d+', section['id'], re.I)]
    rows = [[p('复习单元', st['tablehead'], True), p('主题', st['tablehead'], True)]]
    for section in units:
        rows.append([p(section['id'].upper(), st['table']), p(section['title'], st['table'])])
    if units:
        table = Table(rows, colWidths=[80, CONTENT_WIDTH - 80], repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PALE), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE),
            ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]))
        story += [table, Spacer(1, 12)]
    story.append(p('三轮复习建议', st['section'], True))
    for text in ('先读重点词汇和句型，再遮住英文，根据中文提示默写或口述。',
                 '用核心结构造句，检查主语、动词形式、名词单复数和标点。',
                 '独立完成听力与笔试，最后核对答案，并用自己的话说明错因。'):
        story.append(CheckItem(text, st['body']))
    for section in data['knowledge']['sections']:
        story += [PageBreak(), p(section['id'].upper(), st['eyebrow']), p(section['title'], st['chapter'], True)]
        for block in section['blocks']:
            heading = p(block['title'], st['section'], True)
            items = [p('- ' + text, st['body']) for text in block['items']]
            story.append(KeepTogether([heading, *items[:1]]))
            story.extend(items[1:])
    if data.get('references'):
        story += [PageBreak(), p('资料说明与核对来源', st['chapter'], True),
                  p(data['source_note'], st['small'])]
        for reference in data['references']:
            story.append(p(reference['label'], st['section'], True))
            url = reference['url']
            story.append(ParagraphLink(url, st['tiny']))
    return story


def ParagraphLink(url: str, style):
    from reportlab.platypus import Paragraph
    return Paragraph(f'<link href="{escape(url, quote=True)}" color="#157C83">{rich(url)}</link>', style)


def question_range(group: list) -> str:
    return f'第 {group[0][0]}-{group[-1][0]} 题'


def exam_story(data: dict, st: dict) -> list:
    semester, kind, scope = labels(data)
    groups = OrderedDict((name, []) for name in ('listening_choice', 'fill_blank', 'rewrite', 'reading_choice'))
    for number, question in enumerate(data['questions'], 1):
        groups[question['type']].append((number, question))
    total = len(data['questions'])
    story = [p(f'{"MIDTERM" if kind == "期中" else "FINAL"} PRACTICE / {scope}', st['eyebrow']),
             p(f'英语{kind}模拟考卷', st['title'], True), p(f'{semester} · {scope} · 含听力', st['chapter'], True),
             p(f"共 {total} 题，按答对题数比例折算百分制。建议 {data['time_minutes']} 分钟完成。", st['body']),
             p('班级：____________  姓名：____________  日期：____________', st['body']),
             box('听力采用正常语速，每段朗读两遍，中间间隔 2 秒。可在 english.itorange.online 的“考前准备”中选择对应考卷播放。先完成学生卷，再核对后附答案、解析和听力稿。', st['small'])]
    listening = groups['listening_choice']
    for start in range(0, len(listening), 5):
        batch = listening[start:start + 5]
        if start:
            story.append(PageBreak())
        story.append(p(f'一、听力选择（{question_range(batch)}）', st['section'] if start == 0 else st['chapter'], True))
        for number, question in batch:
            story.append(choice_block(question, number, st))
    for heading, name in (('二、语法填空', 'fill_blank'), ('三、句型转换', 'rewrite')):
        group = groups[name]
        if not group:
            continue
        story += [PageBreak(), p(f'{heading}（{question_range(group)}）', st['chapter'], True),
                  p('只写空缺部分。' if name == 'fill_blank' else '按要求写完整英文句子。', st['small'])]
        for number, question in group:
            block = [p(f"{number}. {question['prompt']}", st['question'])]
            if name == 'rewrite':
                block += [WritingLine(), Spacer(1, 10)]
            else:
                block.append(Spacer(1, 12))
            story.append(KeepTogether(block))
    passages = {passage['id']: passage for passage in data.get('reading_passages', [])}
    reading_groups = OrderedDict()
    for number, question in groups['reading_choice']:
        reading_groups.setdefault(question['passage_id'], []).append((number, question))
    for passage_id, group in reading_groups.items():
        passage = passages[passage_id]
        story += [PageBreak(), p(f'四、阅读选择（{question_range(group)}）', st['chapter'], True),
                  p(passage['title'], st['section'], True), box(passage['text'], st['body']), Spacer(1, 12)]
        for number, question in group:
            story.append(choice_block(question, number, st, short_prompt=True))
    for heading, name in (('听力选择', 'listening_choice'), ('语法填空', 'fill_blank'),
                          ('句型转换', 'rewrite'), ('阅读选择', 'reading_choice')):
        story += [PageBreak(), p('ANSWER KEY / 完成学生卷后再核对', st['eyebrow']),
                  p('参考答案与解析：' + heading, st['chapter'], True)]
        for number, question in groups[name]:
            story.append(KeepTogether([
                p(f"{number}. {answer_for(question, alternate=name == 'rewrite')}", st['body'], True),
                p(question['explanation'], st['small']), Spacer(1, 5),
            ]))
    for start in range(0, len(listening), 5):
        batch = listening[start:start + 5]
        story += [PageBreak(), p('LISTENING SCRIPTS / 听力稿', st['eyebrow']),
                  p(f'听力稿（{question_range(batch)}）', st['chapter'], True),
                  p('每段朗读两遍，间隔 2 秒；下列文字只印一次。', st['small'])]
        for number, question in batch:
            story.append(KeepTogether([p(f'第 {number} 题', st['section'], True),
                                       p(question['listening_text'], st['body']), Spacer(1, 5)]))
    return story


def build(source: Path, output_root: Path = ROOT / 'output/pdf',
          static_root: Path = ROOT / 'static/exam-preparation') -> dict:
    data = json.loads(source.read_text(encoding='utf-8'))
    semester, kind, scope = labels(data)
    register_fonts()
    st = styles()
    report = {'paper_id': data['id']}
    from pypdf import PdfReader
    for name, title, story in (
        ('source_file', f'{semester}{kind}知识点梳理_{scope}', knowledge_story(data, st)),
        ('exam_file', f'{semester}{kind}模拟考卷_{scope}_含听力与答案', exam_story(data, st)),
    ):
        if Path(data[name]).name != data[name]:
            raise ValueError('PDF filenames must be plain basenames')
        output = output_root / (title + '.pdf')
        document(output, story, title, f'{semester} / {scope}', '知识点梳理' if name == 'source_file' else f'{kind}模拟卷')
        destination = static_root / data[name]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output, destination)
        destination.chmod(0o644)
        report[name] = {'path': str(destination), 'pages': len(PdfReader(destination).pages)}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output/pdf')
    parser.add_argument('--static-dir', type=Path, default=ROOT / 'static/exam-preparation')
    args = parser.parse_args()
    for source in args.source:
        print(json.dumps(build(source, args.output_dir, args.static_dir), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
