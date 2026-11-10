const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/js/exam-preparation.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, 'static/index.html'), 'utf8');
const appSource = fs.readFileSync(path.join(__dirname, 'static/js/app.js'), 'utf8');

function examUI() {
    class Element {
        value = '';
        hidden = false;
        disabled = false;
        checked = false;
        textContent = '';
        children = [];
        attributes = {};
        listeners = {};
        classList = { toggle() {}, contains: () => true };
        append(...children) { this.children.push(...children); }
        appendChild(child) { this.append(child); }
        replaceChildren(...children) { this.children = children; }
        setAttribute(key, value) { this.attributes[key] = value; }
        querySelectorAll() { return this.children; }
        querySelector(selector) {
            return selector.includes('aria-pressed')
                ? this.children.find((child) => child.attributes['aria-pressed'] === 'true')
                : this.children[0];
        }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        focus() {}
        reset() {
            for (const id of ['exam-words', 'exam-paper-name']) elements.get(id).value = '';
        }
        async fire(name, extra = {}) {
            return this.listeners[name]?.({ preventDefault() {}, target: this, ...extra });
        }
    }
    const elements = new Map(Array.from(html.matchAll(/id="(exam-[^"]+)"/g), (match) => [match[1], new Element()]));
    elements.get('exam-import-panel').hidden = true;
    const storage = new Map();
    const account = { token: 'alice-token', username: 'alice' };
    const state = { calls: [], failCreation: false };
    const paper = { id: 'paper-1', name: '期中考试', word_count: 2, words: [{ english: 'apple' }, { english: 'look after' }], created_at: '2026-11-10T12:00:00', attempt_count: 0 };
    const attempt = {
        id: 'attempt-1', paper_id: paper.id, paper_name: paper.name, word_count: 2, choice_count: 0,
        questions: [
            { id: 'q1', type: 'spelling', prompt: '苹果' },
            { id: 'q2', type: 'spelling', prompt: '照顾' },
        ],
    };
    state.paper = paper;
    state.attempt = attempt;
    const context = vm.createContext({
        window: {},
        document: {
            getElementById: (id) => elements.get(id),
            createElement: () => new Element(),
            addEventListener: (_, callback) => { state.ready = callback; },
        },
        sessionStorage: {
            setItem: (key, value) => storage.set(key, value),
            getItem: (key) => storage.get(key) || null,
            removeItem: (key) => storage.delete(key),
        },
        captureAccountContext: () => ({ ...account }),
        isAccountContextCurrent: (ctx) => ctx.token === account.token && ctx.username === account.username,
        sessionStudentUsername: () => account.username,
        isParentSession: false,
        userPlan: 'paid',
        setTimeout: (callback) => setImmediate(callback),
        parseVocabImportTokens: (raw) => raw.split(/[,，;；、\r\n]+/).map((word) => word.trim()).filter(Boolean),
        vocabImportWordsForApi: (words) => words.join(', '),
        invalidateReviewDataAfterImport: () => { state.reviewInvalidated = true; },
        clearWordbankCsvDiscoveryCache: () => { state.wordbankInvalidated = true; },
        apiRequest: async (url, options = {}) => {
            const body = options.body ? JSON.parse(options.body) : null;
            state.calls.push({ url, body, method: options.method || 'GET' });
            if (url.endsWith('/knowledge')) return typeof state.knowledge === 'function' ? state.knowledge() : state.knowledge;
            if (url === '/wordbank/csv/import-words') {
                if (state.import) return state.import(body);
                state.failCreation = false;
                return { new_in_csv: 2 };
            }
            if (url.includes('/wordbank/import-jobs/')) return state.job();
            if (url.endsWith('/submit')) {
                if (state.submit) return state.submit(body);
                return { result: {
                    paper_id: paper.id, paper_name: paper.name, score: 50, total: 2, correct_count: 1, wrong_count: 1,
                    questions: [{ id: 'q2', type: 'spelling', prompt: '照顾', english: 'look after', user_answer: 'wrong', correct_answer: 'look after', correct: false }],
                } };
            }
            if (url.endsWith('/attempts')) {
                if (state.start) return state.start(body);
                const choices = body.include_choices ? [{ id: 'q3', type: 'recognition', prompt: 'apple', options: [{ id: 'a', text: '苹果' }, { id: 'b', text: '香蕉' }] }] : [];
                return { attempt: { ...attempt, include_choices: body.include_choices, choice_count: choices.length, questions: [...attempt.questions, ...choices] } };
            }
            if (url.includes('/attempts/')) return { attempt, result: null };
            if (options.method === 'POST' || options.method === 'PUT') {
                if (state.failCreation) {
                    const error = new Error('未找到中文释义：unknownword');
                    if (state.missingWords) error.missing_words = state.missingWords;
                    throw error;
                }
                if (state.save) return state.save(body);
                Object.assign(paper, {
                    name: body.name,
                    sentences: body.sentences,
                    sentence_count: body.sentences.length,
                });
                return { paper, duplicates_removed: 1 };
            }
            if (state.list) return state.list();
            return { papers: [paper], builtin_papers: state.builtinPapers || [] };
        },
    });
    vm.runInContext(source, context);
    state.ready();
    return { state, storage, account, context, api: context.window.examPreparation, field: (id) => elements.get(id) };
}

test('creating a paper sends both fields and retains input on failure', async () => {
    const ui = examUI();
    await ui.api.open();
    assert.equal(ui.field('exam-include-choices').checked, false);
    ui.field('exam-words').value = 'apple,look after,apple';
    ui.field('exam-paper-name').value = '期中考试';
    ui.state.failCreation = true;
    await ui.field('exam-create-form').fire('submit');
    assert.equal(ui.field('exam-words').value, 'apple,look after,apple');
    assert.match(ui.field('exam-preparation-message').textContent, /未找到中文释义/);
    ui.state.failCreation = false;
    await ui.field('exam-create-form').fire('submit');
    const call = ui.state.calls.find((call) => call.body?.name);
    assert.deepEqual(call.body, { name: '期中考试', words: 'apple,look after,apple', sentences: [] });
    assert.equal(ui.field('exam-words').value, '');
    assert.equal(ui.field('exam-include-choices').disabled, false);
    assert.match(ui.field('exam-preparation-message').textContent, /已合并 1 个重复单词/);
});

test('default exam preserves answers across navigation and submits the current answer', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-start').fire('click');
    assert.deepEqual(ui.state.calls.find((call) => call.url.endsWith('/attempts')).body, { include_choices: false });
    assert.equal(ui.field('exam-setup-view').hidden, true);
    assert.equal(ui.field('exam-question-prompt').textContent, '苹果');
    ui.field('exam-spelling-answer').value = 'apple';
    await ui.field('exam-spelling-answer').fire('input');
    await ui.field('exam-next').fire('click');
    ui.field('exam-spelling-answer').value = 'wrong';
    await ui.field('exam-spelling-answer').fire('input');
    await ui.field('exam-previous').fire('click');
    assert.equal(ui.field('exam-spelling-answer').value, 'apple');
    assert.equal(ui.field('exam-answer-count').textContent, '已答 2 / 2 题');
    await ui.field('exam-next').fire('click');
    await ui.field('exam-submit').fire('click');
    assert.deepEqual(ui.state.calls.find((call) => call.url.endsWith('/submit')).body.answers, { q1: 'apple', q2: 'wrong' });
    assert.equal(ui.field('exam-result-view').hidden, false);
    assert.equal(ui.field('exam-result-score').textContent, '50 分');
    assert.equal(ui.field('exam-wrong-list').children.length, 1);
    assert.equal(ui.storage.size, 0);
    await ui.field('exam-back-to-papers').fire('click');
    assert.equal(ui.field('exam-setup-view').hidden, false);
});

test('enabling choices adds option selection and retains it when revisiting', async () => {
    const ui = examUI();
    await ui.api.open();
    ui.field('exam-include-choices').checked = true;
    await ui.field('exam-start').fire('click');
    assert.match(ui.field('exam-preparation-message').textContent, /2 道默写题和 1 道选择题/);
    await ui.field('exam-next').fire('click');
    await ui.field('exam-next').fire('click');
    assert.equal(ui.field('exam-spelling-wrap').hidden, true);
    assert.equal(ui.field('exam-choice-options').hidden, false);
    await ui.field('exam-choice-options').children[1].fire('click');
    await ui.field('exam-previous').fire('click');
    await ui.field('exam-next').fire('click');
    assert.equal(ui.field('exam-choice-options').children[1].attributes['aria-pressed'], 'true');
    await ui.field('exam-submit').fire('click');
    assert.equal(ui.state.calls.find((call) => call.url.endsWith('/submit')).body.answers.q3, 'b');
});

test('unfinished attempt restores answers and position after a page reload', async () => {
    const ui = examUI();
    ui.storage.set('english-reciter-exam:alice', JSON.stringify({ id: 'attempt-1', answers: { q1: 'apple', q2: 'look after' }, index: 1 }));
    await ui.api.open();
    assert.equal(ui.field('exam-attempt-view').hidden, false);
    assert.equal(ui.field('exam-spelling-answer').value, 'look after');
    assert.equal(ui.field('exam-question-position').textContent, '第 2 / 2 题');
});

test('submission failure retains answers and allows retry', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-start').fire('click');
    ui.field('exam-spelling-answer').value = 'apple';
    await ui.field('exam-spelling-answer').fire('input');
    ui.state.submit = async () => { throw new Error('网络中断'); };
    await ui.field('exam-submit').fire('click');
    assert.equal(ui.field('exam-attempt-view').hidden, false);
    assert.equal(ui.field('exam-submit').disabled, false);
    assert.equal(ui.storage.size, 1);
    ui.state.submit = null;
    await ui.field('exam-submit').fire('click');
    const submissions = ui.state.calls.filter((call) => call.url.endsWith('/submit'));
    assert.equal(submissions.length, 2);
    assert.deepEqual(submissions[0].body, submissions[1].body);
});

test('account reset discards private draft and ignores a late response', async () => {
    const ui = examUI();
    let resolve;
    ui.state.list = () => new Promise((callback) => { resolve = callback; });
    const opening = ui.api.open();
    ui.api.reset();
    ui.account.token = 'bob-token';
    ui.account.username = 'bob';
    resolve({ papers: [{ id: 'private', name: 'Alice 私有卷', words: [], created_at: '2026-11-10' }] });
    await opening;
    assert.equal(ui.field('exam-paper-list').children.length, 0);
    assert.equal(ui.field('exam-start').disabled, true);
    assert.equal(ui.storage.size, 0);
});

test('parent can see preparation but cannot start an exam', async () => {
    const ui = examUI();
    ui.context.isParentSession = true;
    await ui.api.open();
    assert.equal(ui.field('exam-parent-hint').hidden, false);
    assert.equal(ui.field('exam-start').disabled, true);
    await ui.field('exam-start').fire('click');
    assert.equal(ui.state.calls.some((call) => call.url.endsWith('/attempts')), false);
});

test('editing loads the selected paper beside the list and cancellation restores a new paper draft', async () => {
    const ui = examUI();
    await ui.api.open();
    ui.field('exam-paper-name').value = '未保存的新卷';
    ui.field('exam-words').value = 'banana';
    await ui.field('exam-add-sentence').fire('click');
    const draftRow = ui.field('exam-sentence-list').children[0];
    draftRow.children[2].value = '我喜欢香蕉。';
    draftRow.children[4].value = 'I like bananas.';
    await ui.field('exam-edit-paper').fire('click');
    assert.equal(ui.field('exam-editor-title').textContent, '编辑考卷');
    assert.equal(ui.field('exam-paper-name').value, '期中考试');
    assert.equal(ui.field('exam-words').value, 'apple, look after');
    assert.equal(ui.field('exam-start').disabled, true);
    assert.equal(ui.field('exam-save-paper').textContent, '保存修改');
    assert.equal(ui.field('exam-cancel-edit').hidden, false);
    await ui.field('exam-start').fire('click');
    assert.equal(ui.state.calls.some((call) => call.url.endsWith('/attempts')), false);
    await ui.field('exam-cancel-edit').fire('click');
    assert.equal(ui.field('exam-editor-title').textContent, '新建考卷');
    assert.equal(ui.field('exam-paper-name').value, '未保存的新卷');
    assert.equal(ui.field('exam-words').value, 'banana');
    assert.equal(ui.field('exam-sentence-list').children[0].children[4].value, 'I like bananas.');
    assert.equal(ui.field('exam-start').disabled, false);
    assert.equal(ui.field('exam-word-chips').children.length, 0);
    assert.equal(ui.field('exam-add-words').value, '');
});

test('saving edits uses the original paper id and retains changes after failure', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    ui.field('exam-paper-name').value = '修改后的期中考试';
    await ui.field('exam-word-chips').children.find((chip) => chip.children[0].textContent === 'apple').children[1].fire('click');
    ui.state.failCreation = true;
    await ui.field('exam-create-form').fire('submit');
    assert.equal(ui.field('exam-editor-title').textContent, '编辑考卷');
    assert.equal(ui.field('exam-words').value, 'look after');
    assert.equal(ui.field('exam-save-paper').disabled, false);
    ui.state.failCreation = false;
    await ui.field('exam-create-form').fire('submit');
    const calls = ui.state.calls.filter((call) => call.method === 'PUT');
    assert.equal(calls.length, 2);
    assert.equal(calls[1].url, '/exam-preparation/papers/paper-1');
    assert.deepEqual(calls[1].body, { name: '修改后的期中考试', words: 'look after', sentences: [] });
    assert.match(ui.field('exam-preparation-message').textContent, /已更新/);
    assert.equal(ui.field('exam-editor-title').textContent, '新建考卷');
    assert.equal(ui.field('exam-paper-list').children.length, 1);
    assert.equal(ui.field('exam-paper-list').children[0].children[0].textContent, '修改后的期中考试');
});

test('sentence questions load for editing, accept multiple reference answers and can be removed', async () => {
    const ui = examUI();
    ui.state.paper.sentences = [{ chinese: '我每天步行去学校。', answers: ['I walk to school every day.'] }];
    ui.state.paper.sentence_count = 1;
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    assert.equal(ui.field('exam-sentences-section').open, true);
    const row = ui.field('exam-sentence-list').children[0];
    assert.equal(row.children[2].value, '我每天步行去学校。');
    row.children[4].value += '\nI go to school on foot every day.\n';
    await ui.field('exam-add-sentence').fire('click');
    const unwanted = ui.field('exam-sentence-list').children[1];
    await unwanted.children[0].children[1].fire('click');
    assert.equal(ui.field('exam-sentence-list').children.length, 1);
    await ui.field('exam-create-form').fire('submit');
    const call = ui.state.calls.find((call) => call.method === 'PUT');
    assert.deepEqual(call.body.sentences, [{
        chinese: '我每天步行去学校。', answers: ['I walk to school every day.', 'I go to school on foot every day.'],
    }]);
});

test('sentence-only exams provide written answers and keep answers across navigation', async () => {
    const ui = examUI();
    ui.state.paper.word_count = 0;
    ui.state.paper.words = [];
    ui.state.paper.sentence_count = 2;
    ui.state.paper.sentences = [{ chinese: '我喜欢苹果。', answers: ['I like apples.'] }];
    ui.state.attempt.questions = [
        { id: 's1', type: 'translation', prompt: '我喜欢苹果。' },
        { id: 's2', type: 'translation', prompt: '这是我的学校。' },
    ];
    ui.state.start = async () => ({ attempt: { ...ui.state.attempt, word_count: 0, sentence_count: 2 } });
    await ui.api.open();
    ui.field('exam-include-choices').checked = true;
    assert.equal(ui.field('exam-include-choices').disabled, true);
    await ui.field('exam-start').fire('click');
    assert.equal(ui.state.calls.find((call) => call.url.endsWith('/attempts')).body.include_choices, false);
    assert.equal(ui.field('exam-question-type').textContent, '句子中译英');
    assert.equal(ui.field('exam-spelling-wrap').hidden, false);
    assert.equal(ui.field('exam-choice-options').hidden, true);
    assert.equal(ui.field('exam-spelling-answer').maxLength, 500);
    assert.match(ui.field('exam-answer-label').textContent, /译成英文/);
    ui.field('exam-spelling-answer').value = 'I like apples.';
    await ui.field('exam-spelling-answer').fire('input');
    await ui.field('exam-next').fire('click');
    await ui.field('exam-previous').fire('click');
    assert.equal(ui.field('exam-spelling-answer').value, 'I like apples.');
    await ui.field('exam-submit').fire('click');
    assert.equal(ui.state.calls.find((call) => call.url.endsWith('/submit')).body.answers.s1, 'I like apples.');
});

test('account reset clears sentence answers in the editor and ignores an in-flight edit response', async () => {
    const ui = examUI();
    ui.state.paper.sentences = [{ chinese: '私有中文题干', answers: ['Private reference answer.'] }];
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    let resolve;
    ui.state.save = () => new Promise((callback) => { resolve = callback; });
    const saving = ui.field('exam-create-form').fire('submit');
    ui.api.reset();
    ui.account.token = 'bob-token';
    ui.account.username = 'bob';
    resolve({ paper: ui.state.paper, duplicates_removed: 0 });
    await saving;
    assert.equal(ui.field('exam-sentence-list').children.length, 0);
    assert.equal(ui.field('exam-paper-list').children.length, 0);
    assert.equal(ui.field('exam-paper-name').value, '');
    assert.equal(ui.field('exam-editor-title').textContent, '新建考卷');
    assert.equal(ui.field('exam-word-chips').children.length, 0);
    assert.equal(ui.field('exam-add-words').value, '');
});

async function missingWordEditor(ui) {
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    ui.field('exam-paper-name').value = '四上期中考试';
    await ui.field('exam-word-chips').children.find((chip) => chip.children[0].textContent === 'look after').children[1].fire('click');
    ui.field('exam-add-words').value = 'seventy, school building';
    await ui.field('exam-add-word-button').fire('click');
    ui.state.failCreation = true;
    ui.state.missingWords = ['seventy', 'school building'];
    await ui.field('exam-create-form').fire('submit');
}

test('missing words open an inline import window and successful import saves the original paper', async () => {
    const ui = examUI();
    await missingWordEditor(ui);
    assert.equal(ui.field('exam-import-panel').hidden, false);
    assert.equal(ui.field('exam-import-words').value, 'seventy\nschool building');
    assert.equal(ui.field('exam-import-add-to-queue').checked, false);
    assert.equal(ui.field('exam-paper-name').value, '四上期中考试');
    assert.equal(ui.field('exam-words').value, 'apple, school building, seventy');
    ui.field('exam-import-level').value = '小学';
    await ui.field('exam-import-submit').fire('click');
    const imported = ui.state.calls.find((call) => call.url === '/wordbank/csv/import-words');
    assert.deepEqual(imported.body, { words: 'seventy, school building', level: '小学', also_add_to_queue: false });
    const saves = ui.state.calls.filter((call) => call.method === 'PUT');
    assert.equal(saves.length, 2);
    assert.equal(saves[1].url, '/exam-preparation/papers/paper-1');
    assert.deepEqual(saves[1].body, saves[0].body);
    assert.equal(ui.field('exam-import-panel').hidden, true);
    assert.equal(ui.field('exam-editor-title').textContent, '新建考卷');
    assert.equal(ui.state.wordbankInvalidated, true);
});

test('background import waits for completion before retrying the paper save', async () => {
    const ui = examUI();
    await missingWordEditor(ui);
    ui.field('exam-import-add-to-queue').checked = true;
    ui.state.import = async () => ({ job_id: 'private-job' });
    let polls = 0;
    ui.state.job = async () => {
        polls += 1;
        assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 1);
        if (polls === 1) return { status: 'running' };
        ui.state.failCreation = false;
        return { status: 'succeeded', result: { queue_result: { added: 2 } } };
    };
    await ui.field('exam-import-submit').fire('click');
    assert.equal(polls, 2);
    assert.equal(ui.state.calls.find((call) => call.url === '/wordbank/csv/import-words').body.also_add_to_queue, true);
    assert.equal(ui.state.reviewInvalidated, true);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 2);
});

test('partial imports retain the paper draft and show only the still-missing words', async () => {
    const ui = examUI();
    await missingWordEditor(ui);
    ui.state.import = async () => {
        ui.state.missingWords = ['school building'];
        return { new_in_csv: 1, invalid_surfaces: ['school building'] };
    };
    await ui.field('exam-import-submit').fire('click');
    assert.equal(ui.field('exam-import-panel').hidden, false);
    assert.equal(ui.field('exam-import-words').value, 'school building');
    assert.equal(ui.field('exam-words').value, 'apple, school building, seventy');
    assert.equal(ui.field('exam-paper-name').value, '四上期中考试');
    assert.match(ui.field('exam-import-status').textContent, /未能导入：school building/);
    assert.equal(ui.field('exam-import-submit').disabled, false);
});

test('a failed background import preserves inputs and allows retry', async () => {
    const ui = examUI();
    await missingWordEditor(ui);
    ui.state.import = async () => ({ job_id: 'private-job' });
    ui.state.job = async () => ({ status: 'failed', error: '导入服务暂不可用' });
    await ui.field('exam-import-submit').fire('click');
    assert.match(ui.field('exam-import-status').textContent, /导入服务暂不可用/);
    assert.equal(ui.field('exam-import-words').value, 'seventy\nschool building');
    assert.equal(ui.field('exam-import-submit').disabled, false);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 1);
});

test('free accounts see the inline import explanation without bypassing VIP access', async () => {
    const ui = examUI();
    ui.context.userPlan = 'free';
    await missingWordEditor(ui);
    assert.equal(ui.field('exam-import-panel').hidden, false);
    assert.equal(ui.field('exam-import-locked').hidden, false);
    assert.equal(ui.field('exam-import-submit').disabled, true);
    await ui.field('exam-import-submit').fire('click');
    assert.equal(ui.state.calls.some((call) => call.url === '/wordbank/csv/import-words'), false);
});

test('account switches stop background polling and prevent saving another account paper', async () => {
    const ui = examUI();
    await missingWordEditor(ui);
    ui.state.import = async () => ({ job_id: 'private-job' });
    ui.state.job = async () => {
        ui.api.reset();
        ui.account.token = 'bob-token';
        ui.account.username = 'bob';
        return { status: 'succeeded', result: {} };
    };
    await ui.field('exam-import-submit').fire('click');
    assert.equal(ui.state.calls.filter((call) => call.url.includes('/import-jobs/')).length, 1);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 1);
    assert.equal(ui.field('exam-import-panel').hidden, true);
    assert.equal(ui.field('exam-import-words').value, '');
    assert.equal(ui.field('exam-paper-name').value, '');
});

test('API errors preserve the complete missing-word list as structured data', async () => {
    const start = appSource.indexOf('async function apiRequest(');
    const end = appSource.indexOf('/** 单词学习：', start);
    const words = Array.from({ length: 20 }, (_, index) => `missing${index}`);
    const context = vm.createContext({
        token: 'alice-token', API_BASE: '/api', parseApiJsonBody: JSON.parse,
        captureAccountContext: () => ({ token: 'alice-token', username: 'alice' }),
        isAccountContextCurrent: () => true,
        fetch: async () => ({ ok: false, status: 400, text: async () => JSON.stringify({ error: '缺少释义', missing_words: [...words, null, 42, ''] }) }),
    });
    vm.runInContext(appSource.slice(start, end), context);
    await assert.rejects(context.apiRequest('/exam-preparation/papers'), (error) => {
        assert.equal(error.status, 400);
        assert.deepEqual(Array.from(error.missing_words), words);
        return true;
    });
});

const chipWords = (ui) => ui.field('exam-word-chips').children.map((chip) => chip.children[0].textContent);

test('editing shows alphabetically sorted word chips and preserves complete phrases', async () => {
    const ui = examUI();
    ui.state.paper.words = ['school building', 'Banana', 'apple', 'APPLE', 'look after'].map((english) => ({ english }));
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    assert.equal(ui.field('exam-words-raw').hidden, true);
    assert.equal(ui.field('exam-word-editor').hidden, false);
    assert.deepEqual(chipWords(ui), ['apple', 'Banana', 'look after', 'school building']);
    assert.equal(ui.field('exam-words').value, 'apple, Banana, look after, school building');
    assert.equal(ui.field('exam-word-chips').children[0].children[1].attributes['aria-label'], '删除 apple');
    assert.match(ui.field('exam-word-count').textContent, /已选 4 个/);
});

test('adding a batch deduplicates case and whitespace and inserts words in alphabetical order', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    ui.field('exam-add-words').value = 'zoo，ART ROOM\n Apple ;computer   room';
    await ui.field('exam-add-word-button').fire('click');
    assert.deepEqual(chipWords(ui), ['apple', 'ART ROOM', 'computer room', 'look after', 'zoo']);
    assert.equal(ui.field('exam-add-words').value, '');
    assert.match(ui.field('exam-word-editor-status').textContent, /3.*1 个重复/);
    assert.match(ui.field('exam-word-count').textContent, /已选 5 个/);
});

test('removing a chip updates the draft and saves the remaining words only', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    await ui.field('exam-word-chips').children[0].children[1].fire('click');
    assert.deepEqual(chipWords(ui), ['look after']);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 0);
    await ui.field('exam-create-form').fire('submit');
    assert.equal(ui.state.calls.find((call) => call.method === 'PUT').body.words, 'look after');
});

test('saving includes words still pending in the add field', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    ui.field('exam-add-words').value = 'banana, art room';
    await ui.field('exam-create-form').fire('submit');
    assert.equal(ui.state.calls.find((call) => call.method === 'PUT').body.words, 'apple, art room, banana, look after');
});

test('Enter adds words while composition, held Enter and Shift Enter preserve the pending text', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    const input = ui.field('exam-add-words');
    input.value = 'banana';
    for (const event of [{ isComposing: true }, { repeat: true }, { shiftKey: true }]) {
        await input.fire('keydown', { key: 'Enter', ...event });
        assert.equal(input.value, 'banana');
        assert.deepEqual(chipWords(ui), ['apple', 'look after']);
    }
    await input.fire('keydown', { key: 'Enter' });
    assert.equal(input.value, '');
    assert.deepEqual(chipWords(ui), ['apple', 'banana', 'look after']);
});

test('invalid input and the 300 word limit preserve the pending words without saving', async () => {
    const ui = examUI();
    ui.state.paper.words = Array.from({ length: 300 }, (_, index) => ({ english: `word${index}` }));
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    ui.field('exam-add-words').value = 'extra';
    await ui.field('exam-create-form').fire('submit');
    assert.equal(chipWords(ui).length, 300);
    assert.equal(ui.field('exam-add-words').value, 'extra');
    assert.match(ui.field('exam-word-editor-status').textContent, /最多 300/);
    ui.field('exam-add-words').value = 'word/invalid';
    await ui.field('exam-add-word-button').fire('click');
    assert.equal(ui.field('exam-add-words').value, 'word/invalid');
    assert.match(ui.field('exam-word-editor-status').textContent, /请检查英文/);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 0);
});

test('removing a missing word closes the outdated import window', async () => {
    const ui = examUI();
    await missingWordEditor(ui);
    const chip = ui.field('exam-word-chips').children.find((item) => item.children[0].textContent === 'school building');
    await chip.children[1].fire('click');
    assert.equal(ui.field('exam-import-panel').hidden, true);
    assert.deepEqual(chipWords(ui), ['apple', 'seventy']);
});

test('saving disables chip removal and the add field until the request finishes', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    let resolve;
    ui.state.save = () => new Promise((callback) => { resolve = callback; });
    const remove = ui.field('exam-word-chips').children[0].children[1];
    const saving = ui.field('exam-create-form').fire('submit');
    assert.equal(remove.disabled, true);
    assert.equal(ui.field('exam-add-words').disabled, true);
    await remove.fire('click');
    assert.deepEqual(chipWords(ui), ['apple', 'look after']);
    resolve({ paper: ui.state.paper, duplicates_removed: 0 });
    await saving;
    assert.equal(ui.field('exam-add-words').disabled, false);
    assert.equal(ui.field('exam-word-chips').children.length, 0);
});

function addBuiltinFixture(ui) {
    const paper = {
        id: 'builtin-shanghai-grade4-u1-u4', builtin: true,
        name: '上海四年级上册英语 · U1-U4 期中自测', question_count: 16,
        word_count: 0, sentence_count: 0, words: [], sentences: [],
        time_minutes: 20, description: '8 道填空、4 道句型转换和 4 道阅读单选。',
        edition: '上海四年级上册', attempt_count: 0,
    };
    ui.state.builtinPapers = [paper];
    ui.state.knowledge = {
        paper_name: paper.name, pdf_url: '/static/exam-preparation/shanghai-grade4-u1-u4.pdf',
        knowledge: { title: 'U1-U4 知识点梳理', intro: '学校、同学、动物与生日。', sections: [
            { title: 'U1 学校', blocks: [{ title: '核心语法', items: ["doesn't 后用 have。"] }] },
            { title: 'U2 同学', blocks: [{ title: '常用结构', items: ['help sb do sth'] }] },
        ] },
    };
    return paper;
}

test('built-in papers share the selection list and have a neighboring knowledge button', async () => {
    const ui = examUI();
    addBuiltinFixture(ui);
    await ui.api.open();
    assert.equal(ui.field('exam-paper-list').children.length, 2);
    const builtinRow = ui.field('exam-paper-list').children[1];
    assert.equal(builtinRow.children[1].textContent, '知识点梳理');
    assert.match(builtinRow.children[0].children[1].textContent, /16 道题/);
    await builtinRow.children[0].fire('click');
    assert.equal(ui.field('exam-edit-paper').hidden, true);
    assert.equal(ui.field('exam-choice-toggle').hidden, true);
    assert.equal(ui.field('exam-builtin-hint').hidden, false);
    assert.equal(ui.field('exam-start').disabled, false);
    await ui.field('exam-edit-paper').fire('click');
    assert.equal(ui.field('exam-editor-title').textContent, '新建考卷');
});

test('knowledge opens for reading and returning keeps the personal editor draft', async () => {
    const ui = examUI();
    addBuiltinFixture(ui);
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    ui.field('exam-paper-name').value = '未保存的修改';
    ui.field('exam-add-words').value = 'banana';
    await ui.field('exam-paper-list').children[1].children[1].fire('click');
    assert.equal(ui.field('exam-knowledge-view').hidden, false);
    assert.equal(ui.field('exam-setup-view').hidden, true);
    assert.equal(ui.field('exam-knowledge-title').textContent, 'U1-U4 知识点梳理');
    assert.equal(ui.field('exam-knowledge-content').children.length, 2);
    assert.equal(ui.field('exam-knowledge-content').children[0].open, true);
    assert.equal(ui.field('exam-knowledge-pdf').href, '/static/exam-preparation/shanghai-grade4-u1-u4.pdf');
    await ui.field('exam-knowledge-back').fire('click');
    assert.equal(ui.field('exam-setup-view').hidden, false);
    assert.equal(ui.field('exam-paper-name').value, '未保存的修改');
    assert.equal(ui.field('exam-add-words').value, 'banana');
});

test('fixed built-in questions use written fields and reading choices with the right labels', async () => {
    const ui = examUI();
    const paper = addBuiltinFixture(ui);
    ui.state.start = async () => ({ attempt: {
        id: 'builtin-attempt', paper_id: paper.id, paper_name: paper.name, builtin: true,
        word_count: 0, sentence_count: 0, choice_count: 1, include_choices: true, time_minutes: 20,
        questions: [
            { id: 'b1', type: 'fill_blank', prompt: 'He often ____ (help) me.' },
            { id: 'b2', type: 'rewrite', prompt: 'The building has no walls.（改为含 any 的同义句）' },
            { id: 'b3', type: 'reading_choice', prompt: 'Where is the party?', options: [{ id: 'a', text: 'Hall' }, { id: 'b', text: 'Library' }] },
        ],
    } });
    await ui.api.open();
    await ui.field('exam-paper-list').children[1].children[0].fire('click');
    await ui.field('exam-start').fire('click');
    assert.match(ui.field('exam-preparation-message').textContent, /内置固定考卷/);
    assert.equal(ui.field('exam-answer-label').textContent, '请填写空缺部分');
    assert.equal(ui.field('exam-spelling-wrap').hidden, false);
    ui.field('exam-spelling-answer').value = 'helps';
    await ui.field('exam-spelling-answer').fire('input');
    await ui.field('exam-next').fire('click');
    assert.equal(ui.field('exam-answer-label').textContent, '请写出转换后的完整句子');
    await ui.field('exam-next').fire('click');
    assert.equal(ui.field('exam-question-type').textContent, '阅读选择');
    assert.equal(ui.field('exam-spelling-wrap').hidden, true);
    assert.equal(ui.field('exam-choice-options').hidden, false);
    await ui.field('exam-choice-options').children[0].fire('click');
    await ui.field('exam-previous').fire('click');
    await ui.field('exam-previous').fire('click');
    assert.equal(ui.field('exam-spelling-answer').value, 'helps');
});

test('parents may read built-in knowledge while the exam remains disabled', async () => {
    const ui = examUI();
    addBuiltinFixture(ui);
    ui.context.isParentSession = true;
    await ui.api.open();
    await ui.field('exam-paper-list').children[1].children[0].fire('click');
    assert.equal(ui.field('exam-start').disabled, true);
    await ui.field('exam-paper-list').children[1].children[1].fire('click');
    assert.equal(ui.field('exam-knowledge-view').hidden, false);
});

test('account reset ignores a late knowledge response and clears study content', async () => {
    const ui = examUI();
    addBuiltinFixture(ui);
    const knowledge = ui.state.knowledge;
    let resolve;
    ui.state.knowledge = () => new Promise((callback) => { resolve = callback; });
    await ui.api.open();
    const opening = ui.field('exam-paper-list').children[1].children[1].fire('click');
    ui.api.reset();
    ui.account.token = 'bob-token';
    ui.account.username = 'bob';
    resolve(knowledge);
    await opening;
    assert.equal(ui.field('exam-knowledge-view').hidden, true);
    assert.equal(ui.field('exam-knowledge-content').children.length, 0);
    assert.equal(ui.field('exam-knowledge-pdf').href, '');
});
