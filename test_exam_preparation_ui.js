const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/js/exam-preparation.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, 'static/index.html'), 'utf8');

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
        apiRequest: async (url, options = {}) => {
            const body = options.body ? JSON.parse(options.body) : null;
            state.calls.push({ url, body, method: options.method || 'GET' });
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
                if (state.failCreation) throw new Error('未找到中文释义：unknownword');
                if (state.save) return state.save(body);
                Object.assign(paper, {
                    name: body.name,
                    sentences: body.sentences,
                    sentence_count: body.sentences.length,
                });
                return { paper, duplicates_removed: 1 };
            }
            if (state.list) return state.list();
            return { papers: [paper] };
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
});

test('saving edits uses the original paper id and retains changes after failure', async () => {
    const ui = examUI();
    await ui.api.open();
    await ui.field('exam-edit-paper').fire('click');
    ui.field('exam-paper-name').value = '修改后的期中考试';
    ui.field('exam-words').value = 'look after';
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
});
