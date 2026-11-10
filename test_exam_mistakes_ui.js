const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/js/exam-preparation.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, 'static/index.html'), 'utf8');

function mistakeUI() {
    class Element {
        value = '';
        hidden = false;
        disabled = false;
        checked = false;
        textContent = '';
        children = [];
        attributes = {};
        listeners = {};
        style = {};
        dataset = {};
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
            await this.listeners[name]?.({ preventDefault() {}, target: this, ...extra });
            await new Promise(setImmediate);
        }
    }
    const elements = new Map(Array.from(html.matchAll(/id="(exam-[^"]+)"/g), (match) => [match[1], new Element()]));
    for (const id of ['exam-review-mistakes', 'exam-review-paper-mistakes', 'exam-paper-mistake-count']) {
        assert.ok(elements.has(id), `Missing visible mistake-review entry: ${id}`);
    }
    const storage = new Map();
    const calls = [];
    const paper = {
        id: 'paper-1', name: '期中考试', word_count: 2, sentence_count: 0,
        words: [{ english: 'apple' }, { english: 'look after' }], sentences: [],
        created_at: '2026-11-10T12:00:00', attempt_count: 1, last_score: 50, best_score: 50,
        mistake_count: 2,
    };
    const normal = {
        id: 'exam-1', paper_id: paper.id, paper_name: paper.name, word_count: 2,
        sentence_count: 0, choice_count: 0, include_choices: false,
        questions: [
            { id: 'q1', type: 'spelling', prompt: '苹果' },
            { id: 'q2', type: 'spelling', prompt: '照顾' },
        ],
    };
    const review = {
        id: 'review-1', paper_id: paper.id, paper_name: paper.name, mode: 'wrong_review',
        word_count: 1, sentence_count: 1, choice_count: 0, include_choices: false,
        questions: [
            { id: 'retry-word', type: 'spelling', prompt: '照顾' },
            { id: 'retry-sentence', type: 'translation', prompt: '我喜欢苹果。' },
        ],
    };
    const state = { paper, normal, review, calls, failReview: false, failSubmit: false };
    const context = vm.createContext({
        window: {},
        document: {
            getElementById: (id) => elements.get(id) || null,
            createElement: () => new Element(),
            addEventListener: (_, callback) => { state.ready = callback; },
        },
        sessionStorage: {
            setItem: (key, value) => storage.set(key, value),
            getItem: (key) => storage.get(key) || null,
            removeItem: (key) => storage.delete(key),
        },
        captureAccountContext: () => ({ token: 'alice-token', username: 'alice' }),
        isAccountContextCurrent: () => true,
        sessionStudentUsername: () => 'alice',
        isParentSession: false,
        apiRequest: async (url, options = {}) => {
            const body = options.body ? JSON.parse(options.body) : null;
            calls.push({ url, body });
            if (url.endsWith('/mistake-reviews')) {
                if (state.failReview) throw new Error('错题加载失败，请重试');
                return { attempt: review };
            }
            if (url.endsWith('/submit')) {
                if (state.failSubmit) throw new Error('网络中断');
                const isReview = url.includes('/review-1/');
                if (isReview) paper.mistake_count = 0;
                return {
                    mistake_count: paper.mistake_count,
                    result: {
                        attempt_id: isReview ? review.id : normal.id,
                        paper_id: paper.id, paper_name: paper.name,
                        mode: isReview ? 'wrong_review' : 'exam',
                        score: isReview ? 100 : 50, total: 2,
                        correct_count: isReview ? 2 : 1, wrong_count: isReview ? 0 : 1,
                        questions: isReview ? review.questions.map((question) => ({
                            ...question, correct: true, user_answer: body.answers[question.id] || '',
                            correct_answer: question.type === 'spelling' ? 'look after' : 'I like apples.',
                        })) : [{
                            id: 'q2', type: 'spelling', prompt: '照顾', english: 'look after',
                            user_answer: 'wrong', correct_answer: 'look after', correct: false,
                        }],
                    },
                };
            }
            if (url.endsWith('/attempts')) return { attempt: normal };
            if (url.includes('/attempts/')) return { attempt: review, result: null, mistake_count: paper.mistake_count };
            return { papers: [paper] };
        },
    });
    vm.runInContext(source, context);
    state.ready();
    return { state, context, storage, api: context.window.examPreparation, field: (id) => elements.get(id) };
}

async function finishNormalExam(ui) {
    await ui.api.open();
    await ui.field('exam-start').fire('click');
    ui.field('exam-spelling-answer').value = 'apple';
    await ui.field('exam-spelling-answer').fire('input');
    await ui.field('exam-next').fire('click');
    ui.field('exam-spelling-answer').value = 'wrong';
    await ui.field('exam-spelling-answer').fire('input');
    await ui.field('exam-submit').fire('click');
    assert.equal(ui.field('exam-result-view').hidden, false);
}

test('result-page mistake review starts a new blank attempt without reusing revealed answers', async () => {
    const ui = mistakeUI();
    await finishNormalExam(ui);
    assert.equal(ui.field('exam-review-mistakes').hidden, false);
    await ui.field('exam-review-mistakes').fire('click');
    const call = ui.state.calls.find((row) => row.url.endsWith('/mistake-reviews'));
    assert.equal(call.url, '/exam-preparation/papers/paper-1/mistake-reviews');
    assert.deepEqual(call.body, {});
    assert.equal(ui.field('exam-result-view').hidden, true);
    assert.equal(ui.field('exam-attempt-view').hidden, false);
    assert.equal(ui.field('exam-question-prompt').textContent, '照顾');
    assert.equal(ui.field('exam-spelling-answer').value, '');
    const draft = JSON.parse(ui.storage.get('english-reciter-exam:alice'));
    assert.equal(draft.id, 'review-1');
    assert.deepEqual(draft.answers, {});
});

test('a fresh page exposes durable pending mistakes from the selected paper', async () => {
    const ui = mistakeUI();
    await ui.api.open();
    assert.match(ui.field('exam-paper-mistake-count').textContent, /2/);
    assert.equal(ui.field('exam-review-paper-mistakes').disabled, false);
    await ui.field('exam-review-paper-mistakes').fire('click');
    assert.equal(ui.field('exam-attempt-view').hidden, false);
    assert.equal(ui.state.calls.filter((row) => row.url.endsWith('/mistake-reviews')).length, 1);
});

test('clearing the remaining mistakes hides the result action and disables the paper action', async () => {
    const ui = mistakeUI();
    await ui.api.open();
    await ui.field('exam-review-paper-mistakes').fire('click');
    ui.field('exam-spelling-answer').value = 'look after';
    await ui.field('exam-spelling-answer').fire('input');
    await ui.field('exam-next').fire('click');
    ui.field('exam-spelling-answer').value = 'I like apples.';
    await ui.field('exam-spelling-answer').fire('input');
    await ui.field('exam-submit').fire('click');
    assert.equal(ui.field('exam-result-heading').textContent, '错题复习完成');
    assert.equal(ui.field('exam-review-mistakes').hidden, true);
    assert.equal(ui.storage.size, 0);
    await ui.field('exam-back-to-papers').fire('click');
    assert.match(ui.field('exam-paper-mistake-count').textContent, /0|暂无|没有|全部/);
    assert.equal(ui.field('exam-review-paper-mistakes').disabled, true);
});

test('a failed review request keeps the exam result and can be retried', async () => {
    const ui = mistakeUI();
    await finishNormalExam(ui);
    ui.state.failReview = true;
    await ui.field('exam-review-mistakes').fire('click');
    assert.equal(ui.field('exam-result-view').hidden, false);
    assert.equal(ui.field('exam-result-score').textContent, '50 分');
    assert.equal(ui.field('exam-review-mistakes').disabled, false);
    assert.match(ui.field('exam-preparation-message').textContent, /失败|重试/);
    ui.state.failReview = false;
    await ui.field('exam-review-mistakes').fire('click');
    assert.equal(ui.field('exam-attempt-view').hidden, false);
});

test('a failed review submission retains the exact draft for retry', async () => {
    const ui = mistakeUI();
    await ui.api.open();
    await ui.field('exam-review-paper-mistakes').fire('click');
    ui.field('exam-spelling-answer').value = 'look after';
    await ui.field('exam-spelling-answer').fire('input');
    const draft = ui.storage.get('english-reciter-exam:alice');
    ui.state.failSubmit = true;
    await ui.field('exam-submit').fire('click');
    assert.equal(ui.field('exam-attempt-view').hidden, false);
    assert.equal(ui.field('exam-submit').disabled, false);
    assert.equal(ui.storage.get('english-reciter-exam:alice'), draft);
    ui.state.failSubmit = false;
    await ui.field('exam-submit').fire('click');
    const submitted = ui.state.calls.filter((row) => row.url.endsWith('/submit'));
    assert.equal(submitted.length, 2);
    assert.deepEqual(submitted[0].body, submitted[1].body);
    assert.equal(ui.field('exam-result-view').hidden, false);
});

test('parents can see the mistake count but cannot begin a review', async () => {
    const ui = mistakeUI();
    ui.context.isParentSession = true;
    await ui.api.open();
    assert.match(ui.field('exam-paper-mistake-count').textContent, /2/);
    assert.equal(ui.field('exam-review-paper-mistakes').disabled, true);
    await ui.field('exam-review-paper-mistakes').fire('click');
    assert.equal(ui.state.calls.some((row) => row.url.endsWith('/mistake-reviews')), false);
});
