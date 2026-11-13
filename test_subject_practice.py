"""Math practice is private, exact, resumable and safe to retry."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from subject_practice import PracticeError, SubjectPracticeStore, grade_paper, load_private_paper, normalized_answer, public_question
from test_subject_portal import subject_app, login, write_records, sample_record


ROOT = Path(__file__).resolve().parent
CATALOG = json.loads((ROOT/'subject_content/math/catalog.json').read_text())
PAPER_IDS = [paper['id'] for paper in CATALOG['papers']]


def private_paper(index=0):
    metadata = CATALOG['papers'][index]
    return load_private_paper(ROOT/'subject_content','math',metadata)


def correct_answers(paper):
    return {q['id']: q['answer'] if q['type']=='choice' else {field['id']: field['answer'] for field in q['fields']}
            for q in paper['questions']}


@pytest.fixture
def math_state(subject_app):
    state=subject_app
    shutil.copytree(ROOT/'subject_content/math',state.content_dir/'math',dirs_exist_ok=True)
    return state


@pytest.mark.parametrize('value',['12500','12,500','１２，５００','12 500','12500.0','12500元'])
def test_exact_numeric_notation_and_declared_unit_are_accepted(value):
    assert normalized_answer(value,{'format':'integer','unit':'元'})==Fraction(12500)


@pytest.mark.parametrize('value',['NaN','Infinity','1e1000000','12,50','1/0','3/2','12米','__import__("os").system("false")'])
def test_student_answers_cannot_execute_expressions_or_smuggle_wrong_units(value):
    assert normalized_answer(value,{'format':'integer','unit':'元'}) is None


@pytest.mark.parametrize('value',['2/4','１／２','0.5',' 1 / 2 米 '])
def test_equal_fractions_and_numeric_units_remain_exact(value):
    assert normalized_answer(value,{'format':'fraction','unit':'米'})==Fraction(1,2)


@pytest.mark.parametrize('index',[0,1])
def test_two_curriculum_papers_have_private_keys_and_split_base_and_challenge_scores(index):
    paper=private_paper(index)
    assert len(paper['questions'])==32
    assert sum(q['points'] for q in paper['questions'][:30])==90
    assert sum(q['points'] for q in paper['questions'][30:])==10
    assert all(q['difficulty']=='challenge' and len(q['hints'])>=2 for q in paper['questions'][30:])
    for q in paper['questions']:
        public=public_question(q)
        assert 'explanation' not in public and 'answer' not in public
        for f in public.get('fields',[]):
            assert not {'answer','accepted_answers','unit_aliases'} & set(f)
    answers=correct_answers(paper)
    full=grade_paper(paper,answers)
    assert (full['score'],full['base_score'],full['challenge_score'])==(100,90,10)
    for q in paper['questions'][30:]:answers.pop(q['id'])
    base=grade_paper(paper,answers)
    assert (base['score'],base['base_max_score'],base['challenge_max_score'])==(90,90,10)


def test_challenge_answers_and_strict_transfer_boundaries_are_independently_correct():
    mid=private_paper(0);final=private_paper(1)
    for paper,dividend,incorrect,difference,expected_divisor,expected_remainder in [
        (mid,527,587,5,12,11),(final,873,789,6,14,5),
    ]:
        q=paper['questions'][30]
        values={field['id']:int(field['answer']) for field in q['fields']}
        assert values=={'divisor':expected_divisor,'remainder':expected_remainder}
        assert abs(dividend-incorrect)==expected_divisor*difference
        assert dividend%expected_divisor==incorrect%expected_divisor==expected_remainder
        assert expected_remainder<expected_divisor
    for paper,total,original_a,needed in [(mid,420,256,47),(final,504,336,85)]:
        fields={field['id']:int(field['answer']) for field in paper['questions'][31]['fields']}
        assert fields=={'original_a':original_a,'transfer':needed}
        original_b=total-original_a
        assert original_a-needed < original_b+needed
        assert not original_a-(needed-1) < original_b+(needed-1)
    assert (420-36)//3*2==256
    assert (272//2-24)*3==336


def test_multifield_partial_credit_is_not_confused_with_full_question_correctness():
    paper=private_paper()
    answers=correct_answers(paper)
    answers['q25']['remainder']='4'
    result=grade_paper(paper,answers,{'q25':'随意书写过程，不额外加分'})
    assert (result['score'],result['correct_count'],result['partial_count'])==(98,31,1)
    row=result['questions'][24]
    assert row['earned_points']==3 and not row['correct']
    assert row['fields'][0]['correct'] and not row['fields'][1]['correct']


def test_counting_unit_is_distinguished_from_the_name_of_a_digit_position():
    paper=private_paper();answers=correct_answers(paper)
    answers['q14']['counting_unit']='千万位'
    result=grade_paper(paper,answers)
    assert result['score']==99
    assert result['questions'][13]['fields'][0]['correct']
    assert not result['questions'][13]['fields'][1]['correct']
    assert result['questions'][13]['fields'][1]['correct_answer']=='千万'


def test_four_digit_grouping_and_rounding_carries_match_the_new_scope():
    mid=private_paper();final=private_paper(1)
    assert mid['questions'][7]['answer']=='b'
    assert mid['questions'][16]['fields'][0]['answer']=='4000'
    assert final['questions'][11]['fields'][0]['answer']=='40905'
    assert final['questions'][12]['fields'][0]['answer']=='8100'


@pytest.mark.parametrize('bad_answers',[[],{'not-a-question':'1'},{'q01':'not-an-option'},{'q09':'724'},{'q09':{'not-a-field':'724'}},{'q09':{'answer':123}},{'q09':{'answer':'1'*121}}])
def test_malformed_answers_are_rejected_without_partial_submission(bad_answers):
    with pytest.raises(PracticeError):grade_paper(private_paper(),bad_answers)


def issue(state,paper_id=None,user='alice'):
    headers=login(state,user)
    response=state.client.post(f'/api/subjects/math/papers/{paper_id or PAPER_IDS[0]}/attempts',headers=headers,json={})
    assert response.status_code==201,response.get_json()
    return headers,response.get_json()['attempt']


def test_bootstrap_and_empty_active_lookup_do_not_create_learning_directories(math_state):
    state=math_state;headers=login(state)
    for url in ['/api/subjects/math/bootstrap','/api/subjects/math/records','/api/subjects/math/attempts/current']:
        response=state.client.get(url,headers=headers);assert response.status_code==200
    assert not (state.data_dir/'alice').exists()
    data=state.client.get('/api/subjects/math/bootstrap',headers=headers).get_json()
    assert data['summary']=={'paper_count':2,'knowledge_count':2,'record_count':0}
    assert all(item['interactive'] for item in data['papers'])
    assert state.client.get('/api/subjects/chinese/bootstrap',headers=headers).get_json()['papers']==[]


def test_pending_attempt_returns_only_student_answers_and_is_owned_by_user_and_subject(math_state):
    state=math_state;headers,attempt=issue(state)
    assert attempt['answers']=={} and attempt['status']=='in_progress'
    payload=json.dumps(attempt,ensure_ascii=False)
    assert 'accepted_answers' not in payload and 'explanation' not in payload
    assert '60÷5=12' not in payload
    bob=login(state,'bob')
    assert state.client.get(f"/api/subjects/math/attempts/{attempt['id']}",headers=bob).status_code==404
    assert state.client.get(f"/api/subjects/chinese/attempts/{attempt['id']}",headers=headers).status_code==404
    assert not (state.data_dir/'bob').exists()
    assert state.client.post(f'/api/subjects/chinese/papers/{PAPER_IDS[0]}/attempts',headers=headers,json={}).status_code==404


def test_draft_is_persistent_revision_checked_and_resume_is_an_explicit_action(math_state):
    state=math_state;headers,attempt=issue(state)
    endpoint=f"/api/subjects/math/attempts/{attempt['id']}"
    body={'answers':{'q01':'b'},'workings':{},'index':8,'revision':2}
    assert state.client.put(endpoint,headers=headers,json=body).status_code==200
    stale={**body,'revision':1,'answers':{'q01':'a'}}
    assert state.client.put(endpoint,headers=headers,json=stale).status_code==409
    assert state.client.put(endpoint,headers=headers,json=body).status_code==200
    assert state.client.put(endpoint,headers=headers,json={**body,'answers':{'q01':'a'}}).status_code==409
    draft=state.client.get('/api/subjects/math/attempts/current',headers=headers).get_json()['attempt']
    assert draft['id']==attempt['id'] and draft['answers']=={'q01':'b'} and draft['index']==8
    response=state.client.post(f'/api/subjects/math/papers/{PAPER_IDS[0]}/attempts',headers=headers,json={})
    assert response.get_json()['attempt']['id']==attempt['id']
    assert state.client.post(f'/api/subjects/math/papers/{PAPER_IDS[1]}/attempts',headers=headers,json={}).status_code==409
    assert state.client.get('/api/subjects/math/records',headers=headers).get_json()['items']==[]


def test_submit_is_idempotent_saved_to_records_and_parent_can_review_only_after_submission(math_state):
    state=math_state;headers,attempt=issue(state)
    endpoint=f"/api/subjects/math/attempts/{attempt['id']}"
    parent=login(state,'alice_parent')
    assert state.client.get('/api/subjects/math/attempts/current',headers=parent).status_code==403
    assert state.client.get(endpoint,headers=parent).status_code==403
    assert state.client.get(f"/api/subjects/math/records/{attempt['id']}",headers=parent).status_code==404
    paper=private_paper()
    body={'answers':correct_answers(paper),'workings':{}}
    first=state.client.post(endpoint+'/submit',headers=headers,json=body)
    assert first.status_code==200
    result=first.get_json()['result'];assert result['score']==100
    repeat=state.client.post(endpoint+'/submit',headers=headers,json={'answers':{},'workings':{}})
    assert repeat.get_json()['result']==result
    assert state.client.put(endpoint,headers=headers,json={'answers':{},'index':0,'revision':99}).status_code==409
    assert state.client.post(endpoint+'/abandon',headers=headers,json={}).status_code==409
    assert state.client.get('/api/subjects/math/attempts/current',headers=headers).get_json()['attempt'] is None
    rows=state.client.get('/api/subjects/math/records',headers=parent).get_json()['items']
    assert len(rows)==1 and rows[0]['score']==100 and 'questions' not in rows[0]
    assert state.client.get(f"/api/subjects/math/records/{attempt['id']}",headers=parent).get_json()['result']==result
    assert state.client.get(f"/api/subjects/math/records/{attempt['id']}",headers=login(state,'bob')).status_code==404
    assert state.client.get('/api/subjects/math/bootstrap',headers=headers).get_json()['summary']['record_count']==1


def test_abandon_clears_recovery_without_a_score_and_blocks_late_save_or_submit(math_state):
    state=math_state;headers,attempt=issue(state)
    endpoint=f"/api/subjects/math/attempts/{attempt['id']}"
    body={'answers':{'q01':'a'},'workings':{},'index':0,'revision':1}
    assert state.client.put(endpoint,headers=headers,json=body).status_code==200
    assert state.client.post(endpoint+'/abandon',headers=headers,json={}).status_code==200
    assert state.client.post(endpoint+'/abandon',headers=headers,json={}).status_code==200
    assert state.client.put(endpoint,headers=headers,json={**body,'revision':2}).status_code==409
    assert state.client.post(endpoint+'/submit',headers=headers,json={'answers':{}}).status_code==409
    assert state.client.get('/api/subjects/math/attempts/current',headers=headers).get_json()['attempt'] is None
    assert state.client.get('/api/subjects/math/records',headers=headers).get_json()['items']==[]
    _,fresh=issue(state)
    assert fresh['id']!=attempt['id'] and fresh['answers']=={} and fresh['index']==0


def test_snapshot_survives_catalog_answer_changes_and_legacy_records_are_preserved(math_state):
    state=math_state;headers,attempt=issue(state)
    file=state.content_dir/'math/papers'/f'{PAPER_IDS[0]}.json'
    data=json.loads(file.read_text());data['questions'][0]['answer']='a';file.write_text(json.dumps(data))
    write_records(state.data_dir,'alice','math',[sample_record('old-record','old-paper')])
    response=state.client.post(f"/api/subjects/math/attempts/{attempt['id']}/submit",headers=headers,json={'answers':correct_answers(private_paper())})
    assert response.get_json()['result']['score']==100
    records=state.client.get('/api/subjects/math/records',headers=headers).get_json()['items']
    assert {row['id'] for row in records}=={'old-record',attempt['id']}
    assert json.loads((state.data_dir/'alice/subjects/math/records.json').read_text())[0]['id']=='old-record'


def test_concurrent_start_and_double_submit_create_only_one_active_attempt_and_one_record(tmp_path):
    paper=private_paper()
    path=tmp_path/'practice.sqlite3'
    with ThreadPoolExecutor(max_workers=2) as pool:
        started=list(pool.map(lambda _:SubjectPracticeStore(path).start(paper),range(2)))
    assert started[0]['id']==started[1]['id']
    body={'answers':correct_answers(paper),'workings':{}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted=list(pool.map(lambda _:SubjectPracticeStore(path).submit(started[0]['id'],body),range(2)))
    assert submitted[0]==submitted[1]
    assert len(SubjectPracticeStore(path).records())==1


def test_corrupt_practice_database_returns_unavailable_without_overwriting_it(math_state):
    state=math_state;headers=login(state)
    path=state.data_dir/'alice/subjects/math/practice.sqlite3';path.parent.mkdir(parents=True);path.write_bytes(b'broken database retained')
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    assert state.client.get('/api/subjects/math/records',headers=headers).status_code==503
    assert state.client.get('/api/subjects/math/attempts/current',headers=headers).status_code==503
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


@pytest.mark.parametrize('mutation',[
    lambda p:p['questions'][0].update(points=1),
    lambda p:p['questions'][0].update(answer='missing'),
    lambda p:p['questions'][8]['fields'][0].update(accepted_answers=[]),
    lambda p:p['questions'][2].update(diagram_url='https://other.example/secret.svg'),
])
def test_invalid_private_content_is_not_issued(tmp_path,mutation):
    paper=deepcopy(private_paper());mutation(paper)
    folder=tmp_path/'math/papers';folder.mkdir(parents=True)
    (folder/(paper['id']+'.json')).write_text(json.dumps(paper))
    with pytest.raises(PracticeError) as error:load_private_paper(tmp_path,'math',CATALOG['papers'][0])
    assert error.value.status==503


def test_published_pdfs_and_original_diagrams_are_complete():
    for item in [*CATALOG['papers'],*CATALOG['knowledge']]:
        content=(ROOT/item['pdf_url'].lstrip('/')).read_bytes()
        assert content.startswith(b'%PDF-') and b'%%EOF' in content[-100:]
        assert len(content)>20000
    for paper in [private_paper(0),private_paper(1)]:
        for q in paper['questions']:
            if q.get('diagram_url'):
                asset=ROOT/q['diagram_url'].lstrip('/')
                assert asset.is_file() and '<svg' in asset.read_text()


def test_pdf_text_has_math_scope_and_challenge_explanations():
    pypdf=pytest.importorskip('pypdf')
    for item in CATALOG['papers']:
        text='\n'.join(page.extract_text() or '' for page in pypdf.PdfReader(ROOT/item['pdf_url'].lstrip('/')).pages)
        assert '数学' in text and '2026' in text and '英语' not in text
        assert '思维拓展' in text and '参考答案与解析' in text
        assert '余数' in text and '少于' in text
