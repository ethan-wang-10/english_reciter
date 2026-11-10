"""Versioned built-in papers; answer keys stay on the server."""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path


CATALOG_DIR = Path(__file__).resolve().parent / 'builtin_exams'
WRITTEN_TYPES = ('fill_blank', 'rewrite')
CHOICE_TYPES = ('reading_choice',)


@lru_cache(maxsize=1)
def _catalog():
    papers = {}
    for path in sorted(CATALOG_DIR.glob('*.json')):
        paper = json.loads(path.read_text(encoding='utf-8'))
        paper_id = paper['id']
        if not paper_id.startswith('builtin-') or paper_id in papers or not paper['questions']:
            raise ValueError(f'Invalid built-in paper: {path.name}')
        seen = set()
        for question in paper['questions']:
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
        papers[paper_id] = paper
    return papers


def is_builtin(paper_id):
    return paper_id in _catalog()


def public_paper(paper_id):
    paper = _catalog()[paper_id]
    return {
        **{key: paper[key] for key in ('id', 'name', 'edition', 'description', 'source_note', 'time_minutes')},
        'builtin': True, 'question_count': len(paper['questions']),
        'word_count': 0, 'sentence_count': 0, 'words': [], 'sentences': [], 'created_at': '',
    }


def list_papers():
    return [public_paper(paper_id) for paper_id in _catalog()]


def questions(paper_id):
    return deepcopy(_catalog()[paper_id]['questions'])


def knowledge(paper_id):
    paper = _catalog()[paper_id]
    filename = paper['source_file']
    if Path(filename).name != filename or not filename.endswith('.pdf'):
        raise ValueError('Invalid built-in handbook filename')
    return {
        'paper_id': paper_id, 'paper_name': paper['name'],
        'knowledge': deepcopy(paper['knowledge']),
        'pdf_url': '/static/exam-preparation/' + filename,
    }
