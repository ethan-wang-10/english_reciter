"""Grade metadata shared by private papers and the built-in catalog."""

import re


_CHINESE_NUMBERS = {name: number for number, name in enumerate(
    ('一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'), 1,
)}


def normalize_grade(value):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, str) and re.fullmatch(r'(?:[1-9]|1[0-2])', value.strip()):
        value = int(value.strip())
    if type(value) is not int or not 1 <= value <= 12:
        raise ValueError('请选择一年级到高三的年级，或选择“未分类”')
    return value


def normalize_term(value):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, str) and value.strip() in ('1', '2'):
        value = int(value.strip())
    if type(value) is not int or value not in (1, 2):
        raise ValueError('请选择上册、下册或“未分学期”')
    return value


def infer_term(name):
    title = str(name or '')
    if re.search(r'上[\s、/和及与]*下(?:册|学期)', title):
        return None
    terms = {
        term for marker, term in (('上', 1), ('下', 2))
        if re.search(marker + r'(?:册|学期)', title)
        or re.search(r'(?:[一二三四五六七八九123456789]|年级)' + marker
                     + r'(?=期中|期末|英语|复习|考试|\s|[·：:]|$)', title)
    }
    return terms.pop() if len(terms) == 1 else None


def infer_grade(name):
    """Only classify an old title when it identifies one unambiguous grade."""
    found = set()
    title = str(name or '')
    for match in re.finditer(r'(?<![\d一二三四五六七八九十])([一二三四五六七八九]|十[一二]?|\d+)\s*年级', title):
        token = match.group(1)
        grade = _CHINESE_NUMBERS.get(token) or (int(token) if token.isdigit() else 0)
        prefix = title[max(0, match.start() - 2):match.start()]
        if grade in (1, 2, 3) and prefix.endswith(('初', '初中', '高', '高中')):
            grade += 6 if prefix.endswith(('初', '初中')) else 9
        if 1 <= grade <= 12:
            found.add(grade)
    for stage, token in re.findall(r'(初|高)([一二三123])', title):
        grade = _CHINESE_NUMBERS.get(token) or int(token)
        found.add(grade + (6 if stage == '初' else 9))
    for token in re.findall(r'(?<![\d一二三四五六七八九十])([一二三四五六1-6])[上下](?:册|学期|期中|期末|英语|复习|考试|$)', title):
        found.add(_CHINESE_NUMBERS.get(token) or int(token))
    for token in re.findall(r'\bgrade\s*(1[0-2]|[1-9])\b', title, re.IGNORECASE):
        found.add(int(token))
    return found.pop() if len(found) == 1 else None
