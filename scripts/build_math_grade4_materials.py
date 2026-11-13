#!/usr/bin/env python3
"""Create printable mathematics papers and matching knowledge booklets."""
from pathlib import Path
import json

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, PageBreak, Paragraph, Spacer, KeepTogether, Flowable, Table, TableStyle
from build_grade4_final_materials import register_fonts, styles, p, box, PAGE_WIDTH, PAGE_HEIGHT, MARGIN, CONTENT_WIDTH, WritingLine

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT/'static/subject-content/math'
INK = colors.HexColor('#24436d')
BLUE = colors.HexColor('#3768a9')
PALE = colors.HexColor('#f4f8ff')


class Diagram(Flowable):
    def __init__(self, name, width=280):
        super().__init__()
        self.name = name
        self.width = width
        self.height = width / 420 * (240 if name=='math-g4-straight-angle.svg' else 300)

    def draw(self):
        c=self.canv
        c.saveState()
        c.scale(self.width/420,self.width/420)
        height=240 if self.name=='math-g4-straight-angle.svg' else 300
        c.setFillColor(PALE);c.roundRect(0,0,420,height,18,fill=1,stroke=0)
        c.setStrokeColor(BLUE);c.setFillColor(INK);c.setLineWidth(3);c.setFont('ReviewCN',16)
        if self.name=='math-g4-straight-angle.svg':
            c.line(30,60,390,60);c.line(210,60,270,208)
            c.setLineWidth(2);c.arc(155,5,265,115,0,68)
            c.setStrokeColor(colors.HexColor('#d87a42'));c.arc(145,-5,275,125,68,112)
            for x,y,label in [(25,33,'A'),(202,33,'O'),(384,33,'B'),(275,210,'C'),(265,103,'68°'),(144,118,'?°')]:c.drawString(x,y,label)
            c.setFont('ReviewCN',12);c.drawString(30,10,'A、O、B在同一直线上；图形不按角度精确绘制。')
        else:
            c.line(376,228,376,274);c.line(376,274,368,262);c.line(376,274,384,262);c.drawString(367,280,'北')
            labels=[(160,150,'学校'),(290,65,'图书馆'),(160,240,'公园'),(290,150,'书店')] if self.name=='math-g4-midterm-map.svg' else [(95,80,'校门'),(225,190,'操场')]
            for x,y,label in labels:
                c.setFillColor(BLUE);c.circle(x,300-y,7,fill=1,stroke=0);c.setFillColor(INK);c.drawString(x+12,295-y,label)
            c.setFont('ReviewCN',12);c.drawString(20,14,'上北下南，左西右东（位置示意）')
        c.restoreState()


def document(path, story, title, scope):
    def chrome(c, doc):
        c.saveState();c.setFillColor(BLUE);c.setFont('ReviewCN',9)
        c.drawString(MARGIN,PAGE_HEIGHT-29,'上海四年级上册数学 / 2026秋新版 / '+scope)
        c.setStrokeColor(colors.HexColor('#d6e1e4'));c.line(MARGIN,PAGE_HEIGHT-43,PAGE_WIDTH-MARGIN,PAGE_HEIGHT-43)
        c.line(MARGIN,35,PAGE_WIDTH-MARGIN,35);c.setFont('ReviewCN',8)
        c.drawString(MARGIN,22,'原创配套练习 | 范围以课本和教师要求为准');c.drawRightString(PAGE_WIDTH-MARGIN,22,f'第{doc.page}页');c.restoreState()
    doc=BaseDocTemplate(str(path),pagesize=(PAGE_WIDTH,PAGE_HEIGHT),leftMargin=MARGIN,rightMargin=MARGIN,topMargin=62,bottomMargin=46,title=title,author='Itorange Subject Learning')
    frame=Frame(MARGIN,46,CONTENT_WIDTH,PAGE_HEIGHT-108,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id='math',frames=[frame],onPage=chrome));doc.build(story)


def knowledge_story(data, knowledge, st):
    result=[p('MATHEMATICS / KNOWLEDGE REVIEW',st['eyebrow']),p(knowledge['title'],st['title'],True),
            p('复习范围：'+knowledge['scope'],st['section'],True),p(knowledge['edition'],st['small']),
            box(knowledge['description'],st['body']),Spacer(1,12),p('复习顺序',st['section'],True),
            p('先读方法与易错点，用自己的话解释；再独立完成配套练习，最后核对答案、列式和单位。',st['body'])]
    for section in knowledge['sections']:
        result += [PageBreak(),p(section['title'],st['chapter'],True)]
        for index,item in enumerate(section['items'],1): result.append(p(f'{index}. {item}',st['body']))
    result += [PageBreak(),p('范围与资料说明',st['chapter'],True),p('期中按第1–4单元编排，期末覆盖全册第1–9单元。原书目录已与出版社新版封面和公开目录核对；本套例题、试题和图示均为原创。未额外声称教材版权页的具体月次、印次。',st['body']),p('在线练习的计分方式',st['section'],True),p(data['grading_note'],st['body'])]
    for ref in data['references']:
        result += [p(ref['label'],st['section'],True),p(ref['url'],st['small'])]
    return result


def fields_line(q, st, compact=False):
    return p('    '.join(f"{f['label']}：____________{f.get('unit','')}（{f['points']}分）" for f in q['fields']),st['math-answer'] if compact else st['body'])


def student_question(number, q, st):
    result=[p(f"{number}. {q['prompt']}（{q['points']}分）",st['math-fill'] if q['type']=='fill' else st['question'])]
    if q.get('diagram_url'):result += [Diagram(Path(q['diagram_url']).name,230 if q['type']=='fill' else 250),Spacer(1,8)]
    if q['type']=='choice':
        rows=[]
        for i in range(0,len(q['options']),2):
            rows.append([p(f"{chr(65+j)}. {q['options'][j]['text']}",st['option']) for j in range(i,min(i+2,len(q['options'])))])
        table=Table(rows,colWidths=[CONTENT_WIDTH/2]*2)
        table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        result += [table,Spacer(1,14)]
    else:
        result += [fields_line(q,st,compact=q['type']=='fill')]
        if q['type'] in ('calculation','application'):result += [WritingLine(height=28) for _ in range(4 if q.get('difficulty')=='challenge' else 2)]
        result.append(Spacer(1,5 if q['type']=='fill' else 12))
    return KeepTogether(result)


def exam_story(data, st):
    questions=data['questions']
    result=[p('MATHEMATICS / PRACTICE PAPER',st['eyebrow']),p(data['title'],st['title'],True),
            p(f"范围：{data['scope']} · 32题 · 满分100分 · 建议{data['time_minutes']}分钟",st['body']),
            p('班级：____________    姓名：____________    日期：____________',st['body']),
            box(data['grading_note']+' 分数用a/b填写，可不约分。学生卷之后附参考答案与解析，请先独立完成。',st['small']),Spacer(1,12)]
    labels={'choice':'一、选择题（每题2分，共16分）','fill':'二、填空题（每题2分，共24分）','calculation':'三、计算题（每题5分，共40分）','application':'四、应用题（第29-30题，共10分）'}
    previous=None
    for number,q in enumerate(questions,1):
        if q['type']=='calculation':
            if previous=='calculation':continue
            result += [PageBreak(),p(labels['calculation'],st['chapter'],True)]
            calculations=[(n,item) for n,item in enumerate(questions,1) if item['type']=='calculation']
            cells=[]
            for n,item in calculations:
                cells.append([p(f"{n}. {item['prompt']}（{item['points']}分）",st['question']),fields_line(item,st),Spacer(1,75)])
            rows=[cells[i:i+2] for i in range(0,len(cells),2)]
            table=Table(rows,colWidths=[CONTENT_WIDTH/2]*2)
            table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),9),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#e5ebef'))]))
            result.append(table);previous='calculation';continue
        if q['type']!=previous:
            if previous is not None:result.append(PageBreak())
            result.append(p(labels[q['type']],st['chapter'] if previous else st['section'],True));previous=q['type']
        if number==31:
            result += [PageBreak(),p('五、思维拓展（第31-32题，共10分）',st['chapter'],True),p('可先完成基础部分，再尝试这两题。先整理条件，画图或逆推，最后检验临界情况。',st['small'])]
        result.append(student_question(number,q,st))
    result += [PageBreak(),p('参考答案与解析',st['title'],True),p('请先完成学生卷。多栏题按各栏分值计分，分数接受等值写法。',st['small'])]
    for number,q in enumerate(questions,1):
        if q['type']=='choice':
            options=q['options'];i=next(i for i,x in enumerate(options) if x['id']==q['answer']);answer=f"{chr(65+i)}. {options[i]['text']}"
        else:answer='；'.join(f"{f['label']}：{f['answer']}{f.get('unit','')}（{f['points']}分）" for f in q['fields'])
        result.append(KeepTogether([p(f'{number}. {answer}',st['body'],True),p(q['explanation'],st['small']),Spacer(1,7)]))
    return result


def build():
    register_fonts();st=styles()
    st['math-fill']=ParagraphStyle('math-fill',parent=st['question'],fontSize=10,leading=15,spaceAfter=3)
    st['math-answer']=ParagraphStyle('math-answer',parent=st['body'],fontSize=9.7,leading=14,spaceAfter=2)
    catalog=json.loads((ROOT/'subject_content/math/catalog.json').read_text())
    from pypdf import PdfReader
    output=ROOT/'output/pdf';output.mkdir(parents=True,exist_ok=True)
    for paper in catalog['papers']:
        data=json.loads((ROOT/'subject_content/math/papers'/f"{paper['id']}.json").read_text())
        knowledge=next(x for x in catalog['knowledge'] if x['id']==paper['knowledge_id'])
        for item,story in [(paper,exam_story(data,st)),(knowledge,knowledge_story(data,knowledge,st))]:
            destination=ROOT/item['pdf_url'].lstrip('/')
            document(destination,story,item['title'],item['scope'])
            destination.chmod(0o644)
            (output/destination.name).write_bytes(destination.read_bytes())
            print(destination.name,len(PdfReader(destination).pages),'pages')


if __name__=='__main__':build()
