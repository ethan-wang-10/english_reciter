"""Versioned built-in papers; answer keys stay on the server."""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re
from exam_grades import normalize_grade, normalize_term


CATALOG_DIR = Path(__file__).resolve().parent / 'builtin_exams'
WRITTEN_TYPES = ('fill_blank', 'rewrite')
CHOICE_TYPES = ('reading_choice', 'listening_choice')


def audio_url(filename):
    if not isinstance(filename, str) or not re.fullmatch(
        r'[a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*)*\.wav', filename, re.IGNORECASE,
    ):
        raise ValueError('Invalid built-in audio filename')
    # Refresh existing attempt snapshots onto the current rendition as well.
    for previous, current in (
        ('shanghai-grade4-final/', 'shanghai-grade4-final-normal/'),
        ('shanghai-grade4-midterm/', 'shanghai-grade4-midterm-normal/'),
    ):
        if filename.startswith(previous):
            filename = current + filename[len(previous):]
            break
    return '/static/exam-preparation/audio/' + filename


def _pdf_url(filename):
    if (not isinstance(filename, str) or Path(filename).name != filename
            or not filename.lower().endswith('.pdf')
            or any(character in filename for character in '\\/:?#%')
            or any(ord(character) < 32 or ord(character) == 127 for character in filename)):
        raise ValueError('Invalid built-in handbook filename')
    return '/static/exam-preparation/' + filename


@lru_cache(maxsize=1)
def _catalog():
    papers = {}
    for path in sorted(CATALOG_DIR.glob('*.json')):
        paper = json.loads(path.read_text(encoding='utf-8'))
        paper['grade'] = normalize_grade(paper.get('grade'))
        paper['term'] = normalize_term(paper.get('term'))
        if paper['grade'] is None:
            raise ValueError(f'Missing grade for built-in paper: {path.name}')
        paper_id = paper['id']
        if not paper_id.startswith('builtin-') or paper_id in papers or not paper['questions']:
            raise ValueError(f'Invalid built-in paper: {path.name}')
        _pdf_url(paper['source_file'])
        if 'exam_file' in paper:
            _pdf_url(paper['exam_file'])
        seen = set()
        for question in paper['questions']:
            # Public audio locations are always derived from validated local files.
            question.pop('audio_url', None)
            if question['id'] in seen or not question['prompt'].strip():
                raise ValueError(f'Invalid built-in question: {path.name}')
            seen.add(question['id'])
            if question['type'] in WRITTEN_TYPES:
                if not question.get('accepted_answers') or question['answer'] not in question['accepted_answers']:
                    raise ValueError(f'Missing accepted answers: {question["id"]}')
            elif question['type'] in CHOICE_TYPES:
                ids = [option['id'] for option in question['options']]
                if len(ids) != len(set(ids)) or question['answer'] not in ids:
                    raise ValueError(f'Invalid choice answer: {question["id"]}')
            else:
                raise ValueError(f'Unsupported built-in question: {question["type"]}')
            if question['type'] == 'listening_choice':
                transcript = question.get('listening_text')
                if not isinstance(transcript, str) or not transcript.strip():
                    raise ValueError(f'Missing listening text: {question["id"]}')
                question['audio_url'] = audio_url(question.get('audio_file'))
        papers[paper_id] = paper
    return papers


def is_builtin(paper_id):
    return paper_id in _catalog()


def public_paper(paper_id):
    paper = _catalog()[paper_id]
    return {
        **{key: paper[key] for key in ('id', 'name', 'grade', 'term', 'edition', 'description', 'source_note', 'time_minutes')},
        'builtin': True, 'question_count': len(paper['questions']),
        'listening_count': sum(question['type'] == 'listening_choice' for question in paper['questions']),
        'word_count': 0, 'sentence_count': 0, 'words': [], 'sentences': [], 'created_at': '',
    }


def list_papers():
    return [public_paper(paper_id) for paper_id in _catalog()]


def questions(paper_id):
    return deepcopy(_catalog()[paper_id]['questions'])


def knowledge(paper_id):
    paper = _catalog()[paper_id]
    result = {
        'paper_id': paper_id, 'paper_name': paper['name'],
        'knowledge': deepcopy(paper['knowledge']),
        'pdf_url': _pdf_url(paper['source_file']),
    }
    if 'exam_file' in paper:
        result['exam_pdf_url'] = _pdf_url(paper['exam_file'])
    return result
