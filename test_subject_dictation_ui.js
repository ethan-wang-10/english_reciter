const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const prefix = '/subjects/chinese/dictation';
const bookId = 'shanghai-chinese-2026-grade4-term1';
const wordPath = (lesson = 'lesson-01') => `${prefix}/wordlists/${bookId}/${lesson}`;
const settle = () => new Promise(setImmediate);
const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));

test('the published 2026 grade-four catalog has the confirmed 27 lessons in eight units', () => {
    const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, 'subject_content/chinese/dictation/books.json'), 'utf8'));
    const book = catalog.books.find((item) => item.grade === 4 && item.term === 1);
    assert.ok(book, 'The first release needs grade four, first term');
    assert.match(book.edition, /2026/);
    assert.equal(book.lessons.length, 27);
    assert.deepEqual(book.lessons.map((lesson) => lesson.number), Array.from({ length: 27 }, (_, index) => index + 1));
    assert.deepEqual(book.lessons.slice(0, 2).map((lesson) => lesson.title), ['观潮', '繁星']);
    assert.equal(book.lessons[13].title, '女娲补天', 'Use the standard title rather than the source metadata typo');
    const units = [...new Set(book.lessons.map((lesson) => lesson.unit))];
    assert.equal(units.length, 8);
    assert.deepEqual(units.map((unit) => book.lessons.filter((lesson) => lesson.unit === unit).map((lesson) => lesson.number)), [
        [1, 2, 3], [4, 5, 6, 7], [8, 9, 10], [11, 12, 13, 14], [15, 16], [17, 18, 19], [20, 21, 22, 23], [24, 25, 26, 27],
    ]);
});

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function dictationUI({ storage = new Map(), isParent = false, voices, capabilities = { kokoro: false, prepare: false }, words = ['潮水', '繁星'], revision = 1, mistakes = [], mistakeStore = new Map() } = {}) {
    const html = fs.readFileSync(path.join(__dirname, 'static/subject.html'), 'utf8');
    const source = fs.readFileSync(path.join(__dirname, 'static/js/subject-dictation.js'), 'utf8');
    const elements = new Map(), timers = new Map();
    let timerId = 0, now = 0;
    const state = { calls: [], routes: {}, listeners: {}, clipboard: '', section: 'dictation', spoken: [], cancelled: 0, speechListeners: {}, audioRequests: [], audios: [], audioPlayed: [], objectUrls: new Map(), revoked: [] };
    state.session = { token: 'alice-token', username: isParent ? 'alice_parent' : 'alice', subject: 'chinese',
        account: { username: isParent ? 'alice_parent' : 'alice', is_parent: isParent, child_username: isParent ? 'alice' : null } };
    state.book = { id: bookId, title: '四年级上册 · 2026版', grade: 4, term: 1, edition: '2026',
        lessons: [
            { id: 'lesson-01', title: '观潮', number: 1, unit: '第一单元', custom: false },
            { id: 'lesson-02', title: '繁星', number: 2, unit: '第一单元', custom: false },
            { id: 'lesson-03', title: '现代诗二首', number: 3, unit: '第一单元', custom: false },
        ] };
    state.books = [state.book];
    state.wordlists = new Map();
    state.wordlistsByBook = new Map([[bookId, state.wordlists]]);
    function wordlist(lesson, entries = [], base = 0, selectedBook = bookId) {
        return { book_id: selectedBook, lesson_id: lesson, words: entries.map((item) => typeof item === 'string' ? { text: item, spoken_text: item } : clone(item)),
            word_count: entries.length, revision: base, updated_at: base ? '2026-11-13T12:00:00+08:00' : null, updated_by: base ? 'alice' : null };
    }
    state.wordlists.set('lesson-01', wordlist('lesson-01', words, revision));
    state.wordlists.set('lesson-02', wordlist('lesson-02'));
    state.mistakes = mistakeStore;
    const mistakeKey = (student, selectedBook, lesson) => `${student}/${selectedBook}/${lesson}`;
    state.seedMistakes = (groups, student = 'alice') => {
        for (const group of groups) {
            const selectedBook = group.book_id || bookId;
            const entries = group.words.map((word) => ({ ...(typeof word === 'string' ? { text: word, spoken_text: word } : clone(word)),
                added_at: '2026-11-13T13:00:00+08:00', updated_at: '2026-11-13T13:00:00+08:00', updated_by: student }));
            state.mistakes.set(mistakeKey(student, selectedBook, group.lesson_id), { book_id: selectedBook, lesson_id: group.lesson_id, words: entries });
        }
    };
    state.seedMistakes(mistakes);
    state.mistakeGroups = (selectedBook = bookId, student = 'alice') => [...state.mistakes.entries()]
        .filter(([key, group]) => key.startsWith(student + '/') && group.book_id === selectedBook && group.words.length)
        .map(([, group]) => ({ ...clone(group), lesson: clone(state.books.find((item) => item.id === selectedBook)?.lessons.find((item) => item.id === group.lesson_id)), word_count: group.words.length }));
    state.voices = voices ?? [
        { name: 'English', lang: 'en-US', voiceURI: 'english', default: true },
        { name: 'Cantonese', lang: 'zh-HK', voiceURI: 'cantonese' },
        { name: 'Mandarin Taiwan', lang: 'zh-TW', voiceURI: 'mandarin-tw' },
        { name: 'Mandarin Mainland', lang: 'zh-CN', voiceURI: 'mandarin-cn' },
    ];

    class Element {
        constructor(tag = 'div', attributes = {}) {
            this.tagName = tag.toUpperCase(); this.attributes = { ...attributes }; this.children = []; this.parentElement = null;
            this.value = attributes.value || ''; this.hidden = Object.hasOwn(attributes, 'hidden'); this.disabled = Object.hasOwn(attributes, 'disabled');
            this.checked = Object.hasOwn(attributes, 'checked'); this.open = Object.hasOwn(attributes, 'open'); this.listeners = {}; this.ownText = '';
            this.dataset = new Proxy({}, { set: (target, name, value) => {
                target[name] = String(value); this.attributes['data-' + String(name).replace(/[A-Z]/g, (letter) => '-' + letter.toLowerCase())] = String(value); return true;
            } });
            this._classes = new Set((attributes.class || '').split(/\s+/).filter(Boolean));
            this.classList = { contains: (name) => this._classes.has(name),
                add: (...names) => { names.forEach((name) => this._classes.add(name)); this.syncClasses(); },
                remove: (...names) => { names.forEach((name) => this._classes.delete(name)); this.syncClasses(); },
                toggle: (name, enabled) => { enabled = enabled ?? !this._classes.has(name); if (enabled) this._classes.add(name); else this._classes.delete(name); this.syncClasses(); return enabled; } };
            for (const [name, value] of Object.entries(attributes)) if (name.startsWith('data-')) this.dataset[this.dataName(name)] = value;
            if (attributes.id) this.id = attributes.id;
        }
        dataName(name) { return name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()); }
        syncClasses() { this.attributes.class = [...this._classes].join(' '); }
        set className(value) { this._classes = new Set(String(value).split(/\s+/).filter(Boolean)); this.syncClasses(); }
        get className() { return this.attributes.class || ''; }
        set id(value) { this.attributes.id = String(value); elements.set(String(value), this); }
        get id() { return this.attributes.id || ''; }
        set type(value) { this.attributes.type = String(value); }
        get type() { return this.attributes.type || ''; }
        set maxLength(value) { this.attributes.maxlength = String(value); }
        get maxLength() { return Number(this.attributes.maxlength || -1); }
        set textContent(value) { this.ownText = String(value); this.children.forEach((child) => { child.parentElement = null; }); this.children = []; }
        get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(''); }
        get innerText() { return this.textContent; }
        set innerHTML(_value) { throw new Error('Dictation content must use safe DOM nodes'); }
        get isContentEditable() { return this.getAttribute('contenteditable') === 'true' || this.parentElement?.isContentEditable || false; }
        get options() { return this.querySelectorAll('option'); }
        append(...children) { children.forEach((child) => this.appendChild(typeof child === 'string' ? Object.assign(new Element('text'), { textContent: child }) : child)); }
        appendChild(child) { child.parentElement?.removeChild(child); this.children.push(child); child.parentElement = this; return child; }
        removeChild(child) { const index = this.children.indexOf(child); if (index >= 0) this.children.splice(index, 1); child.parentElement = null; return child; }
        replaceChildren(...children) { this.children.forEach((child) => { child.parentElement = null; }); this.children = []; this.ownText = ''; this.append(...children); }
        remove() { this.parentElement?.removeChild(this); }
        setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'id') this.id = value; if (name === 'class') this.className = value; if (name.startsWith('data-')) this.dataset[this.dataName(name)] = String(value); }
        getAttribute(name) { return this.attributes[name] ?? null; }
        removeAttribute(name) { delete this.attributes[name]; }
        matches(selector) {
            const attr = [...selector.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)];
            if (attr.some(([, name, value]) => !Object.hasOwn(this.attributes, name) || value !== undefined && this.attributes[name] !== value)) return false;
            let simple = selector.replace(/\[[^\]]+\]/g, '');
            if (simple.includes(':checked')) { if (!this.checked) return false; simple = simple.replace(':checked', ''); }
            const id = simple.match(/#([\w-]+)/)?.[1]; if (id && this.id !== id) return false;
            const classes = [...simple.matchAll(/\.([\w-]+)/g)].map((match) => match[1]); if (classes.some((name) => !this._classes.has(name))) return false;
            const tag = simple.match(/^[a-z][\w-]*/i)?.[0]; return !tag || this.tagName === tag.toUpperCase();
        }
        querySelectorAll(selector) { const queries = selector.split(',').map((value) => value.trim()); return this.children.flatMap((child) => [...(queries.some((query) => child.matches(query)) ? [child] : []), ...child.querySelectorAll(selector)]); }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        closest(selector) { return selector.split(',').some((query) => this.matches(query.trim())) ? this : this.parentElement?.closest(selector) || null; }
        contains(other) { return other === this || this.children.some((child) => child.contains(other)); }
        addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
        focus() { state.focused = this; document.activeElement = this; }
        scrollIntoView() {}
        select() { state.selected = this; }
        setSelectionRange() {}
        async fire(name, extra = {}) {
            let stopped = false;
            const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { stopped = true; }, target: this, ...extra };
            for (let node = this; node; node = node.parentElement) {
                event.currentTarget = node;
                for (const callback of node.listeners[name] || []) await callback(event);
                if (stopped) break;
            }
            if (!stopped) for (const callback of state.listeners[name] || []) await callback(event);
            await settle(); await settle();
            return event;
        }
    }

    const root = new Element('document'), stack = [root];
    const voidTags = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
    for (const token of html.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || []) {
        if (token.startsWith('<!--') || token.startsWith('<!')) continue;
        if (token.startsWith('</')) { stack.pop(); continue; }
        if (token.startsWith('<')) {
            const tag = token.match(/^<([a-z0-9-]+)/i)?.[1].toLowerCase(); if (!tag) continue;
            const attributes = {};
            for (const match of token.slice(tag.length + 1, -1).matchAll(/([a-zA-Z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) attributes[match[1]] = match[2] ?? match[3] ?? match[4] ?? '';
            const node = new Element(tag, attributes); stack.at(-1).appendChild(node);
            if (!voidTags.has(tag) && !token.endsWith('/>')) stack.push(node);
        } else stack.at(-1).ownText += token.replace(/\s+/g, ' ');
    }
    for (const select of root.querySelectorAll('select')) select.value = select.options.find((option) => option.getAttribute('selected') !== null)?.value || select.options[0]?.value || '';
    const field = (id) => { const node = elements.get(id); return node && root.contains(node) ? node : null; };
    assert.ok(field('dictation-panel'), 'The dictation panel must be integrated into the real subject page');
    field('learning-view').hidden = false;
    field('dictation-panel').hidden = false;
    field('subject-site-config').textContent = JSON.stringify({ id: 'chinese' });
    const document = { getElementById: field, createElement: (tag) => new Element(tag), createTextNode: (value) => Object.assign(new Element('text'), { textContent: value }),
        querySelectorAll: (query) => root.querySelectorAll(query), querySelector: (query) => root.querySelector(query),
        addEventListener(name, callback) { if (name === 'DOMContentLoaded') callback(); else (state.listeners[name] ||= []).push(callback); }, activeElement: null,
        body: root.querySelector('body'), hidden: false, visibilityState: 'visible' };

    state.capabilities = clone(capabilities);
    state.defaultReply = (call) => {
        if (call.url === prefix + '/audio/capabilities') {
            assert.equal(call.method, 'GET');
            return clone(state.capabilities);
        }
        if (call.url === prefix + '/audio/prepare') {
            assert.equal(call.method, 'POST');
            assert.ok(Array.isArray(call.body.texts));
            assert.ok(call.body.texts.length <= 200, 'Audio prewarming respects the subject batch limit');
            assert.equal(new Set(call.body.texts).size, call.body.texts.length, 'Prewarming sends distinct spoken text');
            return { enabled: true, accepted: call.body.texts.length, cached: 0, pending: call.body.texts.length, rejected: 0 };
        }
        if (call.url === prefix + '/books') return { items: clone(state.books), count: state.books.length };
        const query = call.url.match(new RegExp(`^${prefix}/(lessons|wordlists|mistakes)\\?book_id=([^&]+)$`));
        if (query) {
            const selectedBook = decodeURIComponent(query[2]), book = state.books.find((item) => item.id === selectedBook);
            assert.ok(book, 'Unknown book: ' + selectedBook);
            const lists = state.wordlistsByBook.get(selectedBook) || new Map();
            if (query[1] === 'lessons') return { items: clone(book.lessons), count: book.lessons.length };
            if (query[1] === 'wordlists') return { items: [...lists.values()].map(clone), count: lists.size };
            const items = state.mistakeGroups(selectedBook, call.student);
            return { items, count: items.length, word_count: items.reduce((total, group) => total + group.words.length, 0) };
        }
        if (call.url === prefix + '/mistakes' && call.method === 'POST') {
            let added = 0, existing = 0;
            for (const item of call.body.items) {
                const key = mistakeKey(call.student, item.book_id, item.lesson_id);
                const group = state.mistakes.get(key) || { book_id: item.book_id, lesson_id: item.lesson_id, words: [] };
                for (const word of item.words) {
                    const previous = group.words.find((saved) => saved.text === word.text);
                    if (previous) {
                        if (!call.body.preserve_existing) { previous.spoken_text = word.spoken_text || word.text; previous.updated_by = call.username; }
                        existing += 1; continue;
                    }
                    group.words.push({ ...clone(word), spoken_text: word.spoken_text || word.text, added_at: '2026-11-13T13:00:00+08:00', updated_at: '2026-11-13T13:00:00+08:00', updated_by: call.username });
                    added += 1;
                }
                state.mistakes.set(key, group);
            }
            return { added_count: added, existing_count: existing };
        }
        const deletion = call.url.match(new RegExp(`^${prefix}/mistakes/([^/?]+)/([^/?]+)$`));
        if (deletion && call.method === 'DELETE') {
            const key = mistakeKey(call.student, decodeURIComponent(deletion[1]), decodeURIComponent(deletion[2]));
            const group = state.mistakes.get(key);
            const before = group?.words.length || 0;
            if (group) group.words = group.words.filter((word) => !call.body.words.includes(word.text));
            return { removed_count: before - (group?.words.length || 0) };
        }
        const match = call.url.match(new RegExp(`^${prefix}/wordlists/([^/?]+)/([^/?]+)$`));
        assert.ok(match, 'Unexpected dictation request: ' + call.url);
        const selectedBook = decodeURIComponent(match[1]), lesson = decodeURIComponent(match[2]);
        const lists = state.wordlistsByBook.get(selectedBook) || new Map();
        const current = lists.get(lesson) || wordlist(lesson, [], 0, selectedBook);
        if (call.method === 'GET') return { wordlist: clone(current), lesson: clone(state.books.find((item) => item.id === selectedBook)?.lessons.find((item) => item.id === lesson)) };
        assert.equal(call.method, 'PUT');
        assert.equal(call.body.base_revision, current.revision, 'Saves must use the current server revision');
        const entries = call.body.words.filter((word, index, list) => list.findIndex((other) => other.text === word.text) === index);
        const saved = wordlist(lesson, entries, current.revision + 1, selectedBook);
        lists.set(lesson, saved); state.wordlistsByBook.set(selectedBook, lists);
        return { wordlist: clone(saved), duplicates_removed: call.body.words.length - entries.length };
    };
    const speechSynthesis = {
        speaking: false, pending: false, paused: false,
        getVoices: () => [...state.voices],
        addEventListener(name, callback) { (state.speechListeners[name] ||= []).push(callback); },
        removeEventListener(name, callback) { state.speechListeners[name] = (state.speechListeners[name] || []).filter((item) => item !== callback); },
        speak(utterance) { state.spoken.push(utterance); state.currentUtterance = utterance; this.speaking = true; utterance.emit('start', { utterance }); },
        cancel() { state.cancelled += 1; this.speaking = false; this.pending = false; state.currentUtterance = null; },
        resume() { this.paused = false; },
    };
    class SpeechSynthesisUtterance {
        constructor(text) { this.text = text; this.listeners = {}; }
        addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
        removeEventListener(name, callback) { this.listeners[name] = (this.listeners[name] || []).filter((item) => item !== callback); }
        emit(name, event = {}) { this[`on${name}`]?.(event); for (const callback of this.listeners[name] || []) callback(event); }
    }
    class FakeAudio extends Element {
        constructor() { super('audio'); this.src = ''; this.currentTime = 0; this.playbackRate = 1; this.defaultPlaybackRate = 1; this.paused = true; this.readyState = 4; state.audios.push(this); }
        get currentSrc() { return this.src; }
        play() {
            if (state.audioPlayError) return Promise.reject(state.audioPlayError);
            this.paused = false; state.currentAudio = this;
            state.audioPlayed.push({ audio: this, src: this.src, playbackRate: this.playbackRate, currentTime: this.currentTime });
            this.emit('play');
            return state.audioPlay ? state.audioPlay(this) : Promise.resolve();
        }
        pause() { this.paused = true; this.pauseCount = (this.pauseCount || 0) + 1; if (state.currentAudio === this) state.currentAudio = null; }
        load() { this.loadCount = (this.loadCount || 0) + 1; }
        emit(name, event = {}) { this['on' + name]?.({ target: this, ...event }); for (const callback of this.listeners[name] || []) callback({ target: this, ...event }); }
        removeAttribute(name) { super.removeAttribute(name); if (name === 'src') this.src = ''; }
    }
    class ObjectURL extends URL {
        static createObjectURL(blob) { assert.ok(blob instanceof Blob); const value = 'blob:chinese-dictation-' + (state.objectUrls.size + state.revoked.length + 1); state.objectUrls.set(value, blob); return value; }
        static revokeObjectURL(value) { state.revoked.push(value); state.objectUrls.delete(value); }
    }
    function audioResponse(status = 200, { type = 'audio/wav', blob = new Blob(['RIFF0123WAVEdata'], { type }) } = {}) {
        return { status, ok: status >= 200 && status < 300, headers: { get: (name) => name.toLowerCase() === 'content-type' ? type : null },
            blob: async () => blob, json: async () => ({ error: 'Server audio unavailable' }) };
    }
    state.audioResponse = audioResponse;
    const fetchAudio = async (url, options = {}) => {
        if (url === '/api/subjects/chinese/dictation/audio/capabilities') {
            const call = { url: prefix + '/audio/capabilities', rawUrl: url, raw: true, method: options.method || 'GET',
                headers: options.headers || {}, signal: options.signal, credentials: options.credentials, cache: options.cache,
                username: state.session.username,
                student: state.session.account?.is_parent ? state.session.account.child_username : state.session.account?.username || state.session.username };
            state.calls.push(call);
            if (state.capabilityFetch) return state.capabilityFetch(call);
            try {
                const result = state.routes[call.url] ? await state.routes[call.url](call) : state.defaultReply(call);
                return { status: 200, ok: true, json: async () => clone(result) };
            } catch (error) {
                if (error.status) return { status: error.status, ok: false, json: async () => ({ error: error.message }) };
                throw error;
            }
        }
        assert.equal(url, '/api/subjects/chinese/dictation/audio', 'Only Chinese dictation speech and capability endpoints should be fetched directly');
        const call = { url, method: options.method || 'GET', headers: options.headers || {}, body: options.body ? JSON.parse(options.body) : null, signal: options.signal, credentials: options.credentials };
        state.audioRequests.push(call);
        return state.audioFetch ? state.audioFetch(call) : audioResponse(state.audioStatus || 200);
    };
    const window = { Audio: FakeAudio, speechSynthesis, SpeechSynthesisUtterance, addEventListener(name, callback) { (state.listeners[name] ||= []).push(callback); } };
    window.subjectPortal = {
        getSession: () => clone(state.session), showSection: (section) => { state.section = section; },
        async apiRequest(url, options = {}) {
            url = url.replace(/^\/api/, '');
            const call = { url, method: options.method || 'GET', body: typeof options.body === 'string' ? JSON.parse(options.body) : clone(options.body), signal: options.signal, username: state.session.username,
                student: state.session.account?.is_parent ? state.session.account.child_username : state.session.account?.username || state.session.username };
            state.calls.push(call);
            return clone(state.routes[url] ? await state.routes[url](call) : state.defaultReply(call));
        },
    };
    const context = vm.createContext({ window, document, speechSynthesis, SpeechSynthesisUtterance, Element, HTMLElement: Element,
        localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: (key) => storage.delete(key) },
        navigator: { clipboard: { writeText: async (value) => { state.clipboard = value; } } },
        URL: ObjectURL, URLSearchParams, Blob, AbortController, fetch: fetchAudio, Audio: FakeAudio, structuredClone, console, queueMicrotask,
        Date: class extends Date { static now() { return now; } },
        setTimeout(callback, delay = 0, ...args) { const id = ++timerId; timers.set(id, { at: now + Number(delay), callback: () => callback(...args) }); return id; },
        clearTimeout(id) { timers.delete(id); }, requestAnimationFrame: (callback) => callback(),
    });
    vm.runInContext(source, context);
    return { state, storage, context, field, root, document, api: window.subjectDictation, speechSynthesis, Element,
        async flush() { await settle(); await settle(); },
        async tick(milliseconds) {
            const target = now + milliseconds;
            for (let count = 0; count < 1000; count += 1) {
                const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
                if (!next) { now = target; await settle(); await settle(); return; }
                now = next[1].at; timers.delete(next[0]); next[1].callback(); await settle(); await settle();
            }
            throw new Error('Fake timer loop did not settle');
        },
        async type(id, value, event = 'input') { const node = field(id); assert.ok(node, 'Missing field: ' + id); node.value = value; await node.fire(event); },
        async chooseLesson(number = 1) {
            const button = field('dictation-lessons').querySelectorAll('button').find((item) => item.getAttribute('aria-label')?.includes(`第${number}课`));
            assert.ok(button, `Missing lesson ${number}`); await button.fire('click');
        },
        async openLesson(number = 1) { await window.subjectDictation.open(); await this.chooseLesson(number); },
        async start() { await field('dictation-start').fire('click'); },
        async finishRun(count) {
            await field('dictation-play').fire('click');
            for (let index = 0; index < count; index += 1) {
                await this.finishSpeech();
                await field('dictation-next').fire('click');
            }
            assert.equal(field('dictation-finish').hidden, false);
        },
        async openMistakes() { await field('dictation-mode-mistakes').fire('click'); },
        async chooseMistakeLessons(ids) {
            for (const input of field('dictation-mistakes-groups').querySelectorAll('input')) {
                input.checked = ids.includes(input.value); await input.fire('change');
            }
        },
        async markReviewWords(words) {
            const labels = field('dictation-review-list').querySelectorAll('label');
            for (const label of labels) {
                const input = label.querySelector('input');
                input.checked = words.some((word) => label.textContent.includes(word)); await input.fire('change');
            }
        },
        async finishAudio() { const audio = state.currentAudio; assert.ok(audio, 'No audio to finish'); state.currentAudio = null; audio.paused = true; audio.emit('ended'); await settle(); await settle(); return audio; },
        async finishItem() { for (let count = 0; count < Number(field('dictation-repetitions').value || 2); count += 1) { if (state.currentAudio) await this.finishAudio(); else await this.finishSpeech(); if (count + 1 < Number(field('dictation-repetitions').value || 2)) await this.tick(700); } },
        async finishSpeech() { const utterance = state.currentUtterance; assert.ok(utterance, 'No speech to finish'); speechSynthesis.speaking = false; state.currentUtterance = null; utterance.emit('end', { utterance }); await settle(); await settle(); return utterance; },
        async failSpeech(error = 'synthesis-failed') { const utterance = state.currentUtterance; assert.ok(utterance, 'No speech to fail'); speechSynthesis.speaking = false; state.currentUtterance = null; utterance.emit('error', { error, utterance }); await settle(); await settle(); return utterance; },
        async voicesChanged(next) { state.voices = next; for (const callback of state.speechListeners.voiceschanged || []) callback(); speechSynthesis.onvoiceschanged?.(); await settle(); await settle(); },
        async space(target = document.body, extra = {}) { return target.fire('keydown', { key: ' ', code: 'Space', keyCode: 32, repeat: false, ...extra }); },
        async nativeSpace(target, extra = {}) {
            const event = await this.space(target, extra);
            await target.fire('keyup', { key: ' ', code: 'Space', keyCode: 32, ...extra });
            if (!event.defaultPrevented && !extra.repeat && !target.disabled && target.tagName === 'BUTTON') await target.fire('click');
            return event;
        },
        visibleText(node = field('dictation-panel')) {
            if (node.hidden || node.getAttribute('aria-hidden') === 'true') return '';
            return [node.ownText, node.value, node.getAttribute('aria-label') || '', node.getAttribute('title') || '', ...node.children.map((child) => this.visibleText(child))].join(' ');
        },
    };
}

test('lesson groups start collapsed, saved words load per lesson, and linked parents may prepare them', async () => {
    const ui = dictationUI({ isParent: true });
    await ui.api.open();
    const groups = ui.field('dictation-lessons').querySelectorAll('details');
    assert.ok(groups.length > 0, 'Lessons should be grouped by textbook unit');
    assert.ok(groups.every((group) => !group.open), 'Unit groups should be collapsed by default');
    await ui.chooseLesson();
    assert.match(ui.field('dictation-words').value, /潮水/);
    assert.equal(ui.field('dictation-words').disabled, false);
    await ui.type('dictation-words', '观潮\n潮水');
    await ui.field('dictation-save').fire('click');
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 1);
    assert.deepEqual(ui.state.wordlists.get('lesson-01').words.map((word) => word.text), ['观潮', '潮水']);
    await ui.chooseLesson(2);
    assert.equal(ui.field('dictation-words').value, '');
});

test('start hides the answers and waits for the first play or Space, which never skips word one', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.start();
    assert.equal(ui.state.spoken.length, 0);
    assert.equal(ui.field('dictation-player').hidden, false);
    assert.equal(ui.field('dictation-home').hidden, true);
    assert.doesNotMatch(ui.visibleText(), /潮水|繁星/);
    await ui.space();
    assert.deepEqual(ui.state.spoken.map((utterance) => utterance.text), ['潮水']);
    assert.equal(ui.state.spoken[0].rate, 1);
    assert.equal(ui.state.spoken[0].lang, 'zh-CN');
    assert.equal(ui.state.spoken[0].voice.lang, 'zh-CN');
});

test('two repetitions have a pause and wait for manual Next after the second reading', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.start();
    await ui.field('dictation-play').fire('click');
    await ui.finishSpeech();
    assert.equal(ui.state.spoken.length, 1, 'The second reading must have a gap');
    await ui.space();
    assert.equal(ui.state.spoken.length, 1, 'Space during the repeat gap must not skip');
    await ui.tick(2000);
    assert.deepEqual(ui.state.spoken.map((utterance) => utterance.text), ['潮水', '潮水']);
    await ui.finishSpeech(); await ui.tick(30000);
    assert.equal(ui.state.spoken.length, 2, 'Finishing a word must not automatically read the next');
    await ui.field('dictation-next').fire('click');
    assert.deepEqual(ui.state.spoken.map((utterance) => utterance.text), ['潮水', '潮水', '繁星']);
});

test('Space advances once, ignores held repeats, and does not double-trigger a native button click', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.start();
    await ui.nativeSpace(ui.field('dictation-play'));
    assert.equal(ui.state.spoken.length, 1);
    await ui.finishSpeech();
    await ui.space(ui.document.body, { repeat: true });
    assert.equal(ui.state.spoken.length, 1);
    await ui.nativeSpace(ui.field('dictation-next'));
    assert.deepEqual(ui.state.spoken.map((utterance) => utterance.text), ['潮水', '繁星']);
    await ui.space(ui.document.body, { repeat: true });
    assert.equal(ui.state.spoken.length, 2);
});

test('typing, controls, composition, and modified shortcuts keep their ordinary Space behavior', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.start();
    for (const tag of ['input', 'textarea', 'select']) {
        const target = new ui.Element(tag); ui.field('dictation-player').appendChild(target);
        const event = await ui.space(target);
        assert.equal(event.defaultPrevented, false, `${tag} must retain Space`);
    }
    const editor = new ui.Element('div', { contenteditable: 'true' });
    const child = new ui.Element('span'); editor.appendChild(child); ui.field('dictation-player').appendChild(editor);
    assert.equal((await ui.space(child)).defaultPrevented, false);
    for (const extra of [{ isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { altKey: true }, { metaKey: true }, { shiftKey: true }]) {
        assert.equal((await ui.space(ui.document.body, extra)).defaultPrevented, false, JSON.stringify(extra));
    }
    assert.equal(ui.state.spoken.length, 0);
});

test('English and Cantonese voices are not used as a Mandarin fallback and late voices enable playback', async () => {
    const ui = dictationUI({ voices: [{ name: 'English', lang: 'en-US', voiceURI: 'en' }, { name: 'Cantonese', lang: 'zh-HK', voiceURI: 'hk' }] });
    await ui.openLesson(); await ui.start();
    await ui.field('dictation-play').fire('click');
    assert.equal(ui.state.spoken.length, 0);
    assert.match(ui.field('dictation-message').textContent + ui.field('dictation-play-status').textContent, /普通话|中文|语音/);
    await ui.voicesChanged([{ name: 'Mandarin', lang: 'zh-CN', voiceURI: 'cn' }]);
    await ui.field('dictation-play').fire('click');
    assert.equal(ui.state.spoken.length, 1);
    assert.equal(ui.state.spoken[0].voice.lang, 'zh-CN');
});

test('a browser without speech synthesis keeps the current word and explains how to recover', async () => {
    const ui = dictationUI();
    ui.context.window.speechSynthesis = undefined;
    ui.context.window.SpeechSynthesisUtterance = undefined;
    await ui.openLesson(); await ui.start(); await ui.field('dictation-play').fire('click');
    await ui.tick(60000);
    assert.equal(ui.state.spoken.length, 0);
    assert.equal(ui.field('dictation-finish').hidden, true);
    assert.match(ui.field('dictation-message').textContent, /普通话语音/);
});

test('a speech error retries the current word and stale callbacks after leaving cannot restart speech', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.start(); await ui.field('dictation-play').fire('click');
    const first = await ui.failSpeech();
    await ui.tick(10000);
    assert.equal(ui.state.spoken.length, 1);
    await ui.field('dictation-play').fire('click');
    assert.deepEqual(ui.state.spoken.map((utterance) => utterance.text), ['潮水', '潮水']);
    const active = ui.state.currentUtterance;
    ui.api.leaveView();
    active.emit('end'); first.emit('end'); await ui.tick(30000);
    assert.equal(ui.state.spoken.length, 2);
    assert.ok(ui.state.cancelled > 0);
    ui.api.reset(); active.emit('end'); await ui.tick(30000);
    assert.equal(ui.state.spoken.length, 2);
});

test('switching accounts while speech is active prevents its old completion from continuing', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.start(); await ui.field('dictation-play').fire('click');
    const old = ui.state.currentUtterance;
    ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    await ui.api.open();
    old.emit('end'); await ui.tick(30000);
    assert.equal(ui.state.spoken.length, 1);
    assert.equal(ui.field('dictation-player').hidden, true);
});

test('the last reading stays hidden until manual completion; selected retries do not change cloud words', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.start();
    await ui.field('dictation-play').fire('click'); await ui.finishSpeech();
    await ui.field('dictation-next').fire('click'); await ui.finishSpeech(); await ui.tick(30000);
    assert.equal(ui.field('dictation-finish').hidden, true);
    assert.doesNotMatch(ui.visibleText(), /潮水|繁星/);
    await ui.space();
    assert.equal(ui.field('dictation-finish').hidden, false);
    assert.match(ui.field('dictation-review-list').textContent, /潮水/);
    assert.match(ui.field('dictation-review-list').textContent, /繁星/);
    const checks = ui.field('dictation-review-list').querySelectorAll('input');
    assert.equal(checks.length, 2);
    checks[1].checked = true; await checks[1].fire('change');
    await ui.field('dictation-retry-selected').fire('click');
    await ui.field('dictation-play').fire('click');
    assert.equal(ui.state.spoken.at(-1).text, '繁星');
    assert.deepEqual(ui.state.wordlists.get('lesson-01').words.map((word) => word.text), ['潮水', '繁星']);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 0);
});

test('saving input updates the server revision and a failed save cannot start a run', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.type('dictation-words', '观潮\n繁星');
    await ui.field('dictation-save').fire('click');
    assert.equal(ui.state.wordlists.get('lesson-01').revision, 2);
    await ui.type('dictation-words', '潮水\n星星');
    ui.state.routes[wordPath()] = (call) => { if (call.method === 'PUT') throw new Error('网络暂不可用'); return ui.state.defaultReply(call); };
    await ui.start();
    assert.equal(ui.field('dictation-player').hidden, true);
    assert.equal(ui.field('dictation-words').value, '潮水\n星星');
    assert.equal(ui.state.spoken.length, 0);
    assert.match(ui.field('dictation-message').textContent, /网络暂不可用|保存/);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').at(-1).body.base_revision, 2);
});

test('save conflicts preserve the private input while cloud data is inspected', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.type('dictation-words', '孩子自己的词语\n新输入');
    ui.state.routes[wordPath()] = (call) => {
        if (call.method === 'PUT') throw Object.assign(new Error('词表已有更新'), { status: 409, code: 'conflict', current_revision: 2 });
        return { wordlist: { ...clone(ui.state.wordlists.get('lesson-01')), revision: 2, words: [{ text: '另一页词语', spoken_text: '另一页词语' }] }, lesson: ui.state.book.lessons[0] };
    };
    await ui.field('dictation-save').fire('click');
    assert.equal(ui.field('dictation-conflict').hidden, false);
    assert.equal(ui.field('dictation-words').value, '孩子自己的词语\n新输入');
    await ui.field('dictation-load-cloud').fire('click');
    assert.equal(ui.field('dictation-words').value, '孩子自己的词语\n新输入');
    assert.match(ui.field('dictation-cloud-preview').textContent, /另一页词语/);
    await ui.field('dictation-copy-local').fire('click');
    assert.match(ui.state.clipboard, /孩子自己的词语/);
});

test('unsaved drafts stay scoped to the student and lesson across switching or refreshing', async () => {
    const storage = new Map();
    const ui = dictationUI({ storage });
    await ui.openLesson(); await ui.type('dictation-words', '观潮草稿\n尚未保存');
    await ui.chooseLesson(2);
    assert.equal(ui.field('dictation-words').value, '');
    await ui.type('dictation-words', '繁星草稿');
    await ui.chooseLesson(1);
    assert.match(ui.field('dictation-words').value, /观潮草稿/);
    const fresh = dictationUI({ storage }); await fresh.openLesson(2);
    assert.equal(fresh.field('dictation-words').value, '繁星草稿');
    assert.ok([...storage.keys()].every((key) => key.includes('alice') && key.includes(bookId)));
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 0);
});

test('refreshing a conflicted local draft repeatedly never adopts the newer cloud revision silently', async () => {
    const storage = new Map();
    const original = dictationUI({ storage, revision: 1 });
    await original.openLesson(); await original.type('dictation-words', '我的尚未保存词语');
    original.api.leaveView();
    const fresh = dictationUI({ storage, revision: 2, words: ['另一页已经更新'] });
    await fresh.openLesson();
    assert.equal(fresh.field('dictation-conflict').hidden, false);
    assert.equal(fresh.field('dictation-words').value, '我的尚未保存词语');
    fresh.api.leaveView(); fresh.api.reset();
    const again = dictationUI({ storage, revision: 2, words: ['另一页已经更新'] });
    await again.openLesson();
    assert.equal(again.field('dictation-conflict').hidden, false, 'Reloading must keep the unresolved version conflict');
    assert.equal(again.field('dictation-words').value, '我的尚未保存词语');
    await again.field('dictation-save').fire('click');
    assert.equal(again.state.calls.filter((call) => call.method === 'PUT').length, 0, 'A stale draft must require explicit reconciliation before any save');
});

test('a late wordlist response after an account switch cannot expose the previous student input', async () => {
    const ui = dictationUI(); await ui.api.open();
    const gate = deferred();
    ui.state.routes[wordPath()] = () => gate.promise;
    const pending = ui.chooseLesson(); await ui.flush();
    ui.api.reset();
    ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    await ui.api.open();
    gate.resolve({ wordlist: { book_id: bookId, lesson_id: 'lesson-01', revision: 1, words: [{ text: '上个学生私有词语', spoken_text: '上个学生私有词语' }] }, lesson: ui.state.book.lessons[0] });
    await pending; await ui.flush();
    assert.doesNotMatch(ui.field('dictation-words').value, /上个学生/);
    assert.doesNotMatch(ui.visibleText(), /上个学生/);
});

test('reopening dictation retries a failed textbook directory request', async () => {
    const ui = dictationUI();
    const directoryPath = prefix + '/lessons?book_id=' + bookId;
    ui.state.routes[directoryPath] = () => { throw new Error('目录网络失败'); };
    await ui.api.open();
    assert.match(ui.field('dictation-message').textContent, /目录网络失败/);
    ui.api.leaveView();
    delete ui.state.routes[directoryPath];
    await ui.api.open(); await ui.chooseLesson();
    assert.match(ui.field('dictation-words').value, /潮水/);
});

test('a running practice uses its snapshot even when the cloud wordlist changes', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.start();
    await ui.field('dictation-play').fire('click'); await ui.finishSpeech();
    ui.state.wordlists.set('lesson-01', { ...clone(ui.state.wordlists.get('lesson-01')), revision: 2, words: [{ text: '云端新词', spoken_text: '云端新词' }] });
    ui.api.leaveView(); await ui.api.open();
    await ui.field('dictation-resume').fire('click');
    await ui.field('dictation-next').fire('click');
    assert.equal(ui.state.spoken.at(-1).text, '繁星');
});

test('selected incorrect words are saved only on explicit action and remain available on a fresh page', async () => {
    const store = new Map();
    const ui = dictationUI({ mistakeStore: store });
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.start(); await ui.finishRun(2);
    await ui.markReviewWords(['繁星']);
    assert.equal(ui.state.calls.filter((call) => call.method === 'POST' && call.url === prefix + '/mistakes').length, 0);
    assert.equal(ui.state.mistakeGroups().length, 0, 'Checking a word alone must not persist it');
    await ui.field('dictation-save-mistakes').fire('click');
    assert.deepEqual(ui.state.mistakeGroups().map((group) => [group.lesson_id, group.words.map((word) => word.text)]), [['lesson-01', ['繁星']]]);
    const fresh = dictationUI({ mistakeStore: store });
    await fresh.api.open(); await fresh.openMistakes();
    assert.match(fresh.field('dictation-mistakes-groups').textContent, /观潮/);
    assert.match(fresh.field('dictation-mistakes-groups').textContent, /繁星/);
    assert.equal(fresh.field('dictation-mistakes-count').textContent.includes('1'), true);
});

test('ending before all repetitions finish cannot turn an unheard word into a saved mistake', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.start(); await ui.field('dictation-play').fire('click');
    await ui.finishSpeech(); // The default second reading has not happened yet.
    await ui.field('dictation-player-exit').fire('click'); await ui.field('dictation-end-run').fire('click');
    const checks = ui.field('dictation-review-list').querySelectorAll('input');
    assert.ok(checks.every((input) => input.disabled));
    checks[0].checked = true; await checks[0].fire('change');
    await ui.field('dictation-save-mistakes').fire('click');
    assert.equal(ui.field('dictation-save-mistakes').disabled, true);
    assert.equal(ui.state.calls.filter((call) => call.method === 'POST' && call.url === prefix + '/mistakes').length, 0);
});

test('early completion can save a fully heard word while leaving later unheard words unavailable', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.start(); await ui.field('dictation-play').fire('click');
    await ui.finishSpeech(); await ui.tick(700); await ui.finishSpeech();
    await ui.field('dictation-player-exit').fire('click'); await ui.field('dictation-end-run').fire('click');
    const checks = ui.field('dictation-review-list').querySelectorAll('input');
    assert.equal(checks[0].disabled, false); assert.equal(checks[1].disabled, true);
    checks[0].checked = true; await checks[0].fire('change');
    checks[1].checked = true; await checks[1].fire('change');
    await ui.field('dictation-save-mistakes').fire('click');
    assert.deepEqual(ui.state.mistakeGroups()[0].words.map((word) => word.text), ['潮水']);
});

test('a failed mistake save preserves the checked words and allows retry without changing the original wordlist', async () => {
    const ui = dictationUI(); const original = clone(ui.state.wordlists.get('lesson-01'));
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.start(); await ui.finishRun(2);
    await ui.markReviewWords(['潮水']);
    ui.state.routes[prefix + '/mistakes'] = () => { throw new Error('保存错题网络失败'); };
    await ui.field('dictation-save-mistakes').fire('click');
    assert.equal(ui.field('dictation-review-list').querySelectorAll('input').filter((input) => input.checked).length, 1);
    assert.equal(ui.field('dictation-save-mistakes').disabled, false);
    assert.match(ui.field('dictation-mistakes-save-state').textContent + ui.field('dictation-message').textContent, /保存错题网络失败|失败|重试/);
    delete ui.state.routes[prefix + '/mistakes'];
    await ui.field('dictation-save-mistakes').fire('click');
    assert.deepEqual(ui.state.mistakeGroups()[0].words.map((word) => word.text), ['潮水']);
    assert.deepEqual(ui.state.wordlists.get('lesson-01'), original);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 0);
});

test('repeated clicks while a mistake save is pending send one request', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.start(); await ui.finishRun(2);
    await ui.markReviewWords(['潮水']);
    const gate = deferred(); let pendingCall;
    ui.state.routes[prefix + '/mistakes'] = (call) => { pendingCall = call; return gate.promise; };
    const first = ui.field('dictation-save-mistakes').fire('click'); await ui.flush();
    await ui.field('dictation-save-mistakes').fire('click');
    assert.equal(ui.field('dictation-save-mistakes').disabled, true);
    assert.equal(ui.state.calls.filter((call) => call.method === 'POST' && call.url === prefix + '/mistakes').length, 1);
    gate.resolve(ui.state.defaultReply(pendingCall)); await first; await ui.flush();
    assert.equal(ui.state.mistakeGroups()[0].words.length, 1);
});

test('the mistake notebook groups lessons and disables review until at least one lesson is selected', async () => {
    const ui = dictationUI({ mistakes: [{ lesson_id: 'lesson-01', words: ['潮水'] }, { lesson_id: 'lesson-02', words: ['星星'] }] });
    await ui.api.open(); await ui.openMistakes();
    const groups = ui.field('dictation-mistakes-groups').querySelectorAll('details');
    assert.equal(groups.length, 2); assert.ok(groups.every((group) => !group.open));
    assert.match(ui.field('dictation-mistakes-groups').textContent, /观潮/);
    assert.match(ui.field('dictation-mistakes-groups').textContent, /繁星/);
    assert.equal(ui.field('dictation-mistakes-start').disabled, true);
    await ui.field('dictation-mistakes-select-all').fire('click');
    assert.equal(ui.field('dictation-mistakes-start').disabled, false);
    await ui.field('dictation-mistakes-clear-selection').fire('click');
    assert.equal(ui.field('dictation-mistakes-start').disabled, true);
    await ui.field('dictation-mistakes-start').fire('click');
    assert.equal(ui.field('dictation-player').hidden, true);
});

test('multi-lesson review reads a shared word once, keeps all sources, and saves selected mistakes back to each source', async () => {
    const ui = dictationUI({ mistakes: [
        { lesson_id: 'lesson-01', words: ['潮水', '繁星'] },
        { lesson_id: 'lesson-02', words: ['潮水', '星星'] },
    ] });
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.openMistakes();
    await ui.chooseMistakeLessons(['lesson-01', 'lesson-02']);
    await ui.field('dictation-mistakes-start').fire('click');
    assert.equal(ui.state.spoken.length, 0, 'Combined review still waits for manual first play');
    await ui.finishRun(3);
    assert.deepEqual(ui.state.spoken.map((utterance) => utterance.text).sort(), ['潮水', '繁星', '星星'].sort());
    const shared = ui.field('dictation-review-list').querySelectorAll('label').find((label) => label.textContent.includes('潮水'));
    assert.match(shared.textContent, /观潮/); assert.match(shared.textContent, /繁星/);
    await ui.markReviewWords(['潮水']);
    await ui.field('dictation-save-mistakes').fire('click');
    const saved = ui.state.calls.findLast((call) => call.method === 'POST' && call.url === prefix + '/mistakes');
    assert.deepEqual(saved.body.items.map((item) => [item.book_id, item.lesson_id, item.words.map((word) => word.text)]).sort((a, b) => a[1].localeCompare(b[1])), [
        [bookId, 'lesson-01', ['潮水']], [bookId, 'lesson-02', ['潮水']],
    ]);
    await ui.field('dictation-retry-selected').fire('click'); await ui.finishRun(1);
    const retried = ui.field('dictation-review-list').querySelector('label');
    assert.match(retried.textContent, /观潮/); assert.match(retried.textContent, /繁星/);
    assert.equal(ui.state.calls.filter((call) => call.method === 'DELETE').length, 0, 'Reading a word correctly must not remove notebook entries');
});

test('deleting a notebook word removes only that lesson entry and leaves the original wordlist intact', async () => {
    const ui = dictationUI({ mistakes: [{ lesson_id: 'lesson-01', words: ['潮水', '繁星'] }, { lesson_id: 'lesson-02', words: ['潮水'] }] });
    const original = clone(ui.state.wordlists.get('lesson-01'));
    await ui.api.open(); await ui.openMistakes();
    const remove = ui.field('dictation-mistakes-groups').querySelectorAll('button').find((button) => button.getAttribute('aria-label')?.includes('第1课') && button.getAttribute('aria-label')?.endsWith('潮水'));
    assert.ok(remove, 'Each notebook word needs an accessible delete action');
    await remove.fire('click');
    assert.deepEqual(ui.state.mistakeGroups().map((group) => [group.lesson_id, group.words.map((word) => word.text)]), [['lesson-01', ['繁星']], ['lesson-02', ['潮水']]]);
    assert.deepEqual(ui.state.wordlists.get('lesson-01'), original);
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 0);
});

test('a normal run saves mistakes to its frozen textbook and lesson after the preparation selection changes', async () => {
    const ui = dictationUI();
    const otherBook = { id: 'another-textbook', title: '另一册课本', grade: 4, term: 2, edition: '目录测试', lessons: [{ id: 'lesson-99', number: 99, title: '另一篇课文', unit: '补充', custom: false }] };
    ui.state.books.push(otherBook);
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.start();
    await ui.field('dictation-player-exit').fire('click');
    await ui.chooseLesson(2); await ui.type('dictation-book', otherBook.id, 'change');
    await ui.field('dictation-resume').fire('click'); await ui.finishRun(2);
    await ui.markReviewWords(['潮水']); await ui.field('dictation-save-mistakes').fire('click');
    const saved = ui.state.calls.findLast((call) => call.method === 'POST' && call.url === prefix + '/mistakes');
    assert.deepEqual(saved.body.items.map((item) => [item.book_id, item.lesson_id]), [[bookId, 'lesson-01']]);
    assert.deepEqual(ui.state.mistakeGroups().map((group) => group.words.map((word) => word.text)), [['潮水']]);
    assert.equal(ui.state.mistakeGroups(otherBook.id).length, 0);
});

test('a late notebook read after switching accounts cannot replace the new student notebook', async () => {
    const ui = dictationUI();
    await ui.api.open(); await ui.openMistakes();
    const gate = deferred(), notebookPath = prefix + '/mistakes?book_id=' + bookId;
    ui.state.routes[notebookPath] = () => gate.promise;
    const pending = ui.field('dictation-mistakes-refresh').fire('click'); await ui.flush();
    assert.equal(ui.state.calls.findLast((call) => call.url === notebookPath).student, 'alice');
    ui.api.reset();
    ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    ui.state.seedMistakes([{ lesson_id: 'lesson-02', words: ['小明自己的错词'] }], 'bob');
    delete ui.state.routes[notebookPath];
    await ui.api.open(); await ui.openMistakes();
    gate.resolve({ items: [{ book_id: bookId, lesson_id: 'lesson-01', lesson: ui.state.book.lessons[0], words: [{ text: '前个学生私有错词', spoken_text: '前个学生私有错词' }], word_count: 1 }], count: 1, word_count: 1 });
    await pending; await ui.flush();
    assert.match(ui.field('dictation-mistakes-groups').textContent, /小明自己的错词/);
    assert.doesNotMatch(ui.field('dictation-mistakes-groups').textContent, /前个学生/);
});

test('a pending mistake save may finish for the old student without updating the next student page', async () => {
    const ui = dictationUI();
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.start(); await ui.finishRun(2);
    await ui.markReviewWords(['潮水']);
    const gate = deferred(); let oldCall;
    ui.state.routes[prefix + '/mistakes'] = (call) => { oldCall = call; return gate.promise; };
    const pending = ui.field('dictation-save-mistakes').fire('click'); await ui.flush();
    ui.api.reset();
    ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    await ui.api.open(); await ui.openMistakes();
    gate.resolve(ui.state.defaultReply(oldCall)); await pending; await ui.flush();
    assert.equal(ui.state.mistakeGroups(bookId, 'alice')[0].words[0].text, '潮水');
    assert.equal(ui.state.mistakeGroups(bookId, 'bob').length, 0);
    assert.doesNotMatch(ui.field('dictation-mistakes-groups').textContent, /潮水/);
    assert.equal(ui.field('dictation-finish').hidden, true);
});


const chineseVoiceKey = 'itorange-chinese-dictation-voice';

async function editChinesePronunciation(ui, text, lessonId = null) {
    const container = ui.field(lessonId ? 'dictation-mistakes-groups' : 'dictation-word-preview');
    const button = container.querySelectorAll('button').find((item) => item.dataset.word === text
        && (item.getAttribute('aria-label') || '').startsWith('修正')
        && (!lessonId || item.dataset.lessonId === lessonId));
    assert.ok(button, `Missing pronunciation editor for ${lessonId || 'wordlist'}/${text}`);
    await button.fire('click');
    const form = container.querySelector('.dictation-pronunciation-editor');
    assert.ok(form, 'Opening pronunciation correction exposes an inline form');
    return { form, input: form.querySelector('.dictation-pronunciation-input'),
        button: (action) => { const control = form.querySelector(`[data-action="${action}"]`); assert.ok(control, 'Missing correction action ' + action); return control; } };
}

test('Mandarin voice selection prioritizes Mainland pronunciation and then natural voice quality', async () => {
    const basic = { name: 'Mandarin basic', lang: 'zh-CN', voiceURI: 'cn-basic', default: true };
    const mainland = { name: 'Xiaoxiao Natural', lang: 'zh-CN', voiceURI: 'cn-natural' };
    const regional = { name: 'Taiwan Premium Natural', lang: 'zh-TW', voiceURI: 'tw-premium', default: true };
    const rejected = { name: 'Cantonese Enhanced', lang: 'zh-HK', voiceURI: 'hk-enhanced' };
    const ui = dictationUI({ voices: [regional, rejected, basic] }); await ui.openLesson();
    assert.equal(ui.field('dictation-voice').value, basic.voiceURI, 'Mainland Mandarin outranks a more natural regional voice');
    await ui.voicesChanged([regional, rejected, basic, mainland]);
    assert.equal(ui.field('dictation-voice').value, mainland.voiceURI, 'A newly loaded natural Mainland voice upgrades the default');
    assert.equal(ui.field('dictation-voice').options.some((option) => option.value === rejected.voiceURI), false);
    await ui.start(); await ui.field('dictation-play').fire('click');
    assert.strictEqual(ui.state.spoken[0].voice, mainland);
    assert.equal(ui.state.spoken[0].rate, 1);
});

test('the remembered Mandarin voice survives delayed loading, URI changes, resets and an active run', async () => {
    const saved = { voiceURI: 'old-uri', name: 'My Mandarin', lang: 'zh-CN' };
    const storage = new Map([[chineseVoiceKey, JSON.stringify(saved)]]);
    const basic = { name: 'Mainland basic', lang: 'zh-CN', voiceURI: 'basic' };
    const preferred = { name: saved.name, lang: 'zh_CN', voiceURI: 'new-uri' };
    const natural = { name: 'Xiaoxiao Natural', lang: 'zh-CN', voiceURI: 'best' };
    const ui = dictationUI({ storage, voices: [] }); await ui.openLesson();
    assert.equal(ui.field('dictation-voice').disabled, true);
    assert.deepEqual(JSON.parse(storage.get(chineseVoiceKey)), saved);
    await ui.voicesChanged([basic]);
    assert.equal(ui.field('dictation-voice').value, basic.voiceURI);
    assert.deepEqual(JSON.parse(storage.get(chineseVoiceKey)), saved, 'Missing saved voices must remain remembered');
    await ui.voicesChanged([basic, preferred, natural]);
    assert.equal(ui.field('dictation-voice').value, preferred.voiceURI, 'Name and normalized language recover a changed voice URI');
    await ui.type('dictation-voice', basic.voiceURI, 'change');
    assert.deepEqual(JSON.parse(storage.get(chineseVoiceKey)), { voiceURI: basic.voiceURI, name: basic.name, lang: basic.lang });
    await ui.type('dictation-repetitions', '1', 'change'); await ui.start();
    await ui.voicesChanged([natural]); await ui.field('dictation-play').fire('click');
    assert.strictEqual(ui.state.spoken[0].voice, basic, 'The started round keeps its selected voice even if the device list changes');
    ui.api.reset(); await ui.openLesson();
    assert.deepEqual(JSON.parse(storage.get(chineseVoiceKey)), { voiceURI: basic.voiceURI, name: basic.name, lang: basic.lang });
    await ui.voicesChanged([{ ...basic, voiceURI: 'basic-after-restart' }, natural]);
    assert.equal(ui.field('dictation-voice').value, 'basic-after-restart');
    assert.equal([...storage.keys()].some((key) => key === 'itorange-english-dictation-voice'), false);
});

test('inline Mandarin correction previews drafts, cancels safely and explicitly saves the full list or restores the original pronunciation', async () => {
    const ui = dictationUI({ words: ['重庆', '繁星'] }); await ui.openLesson();
    const raw = '重庆\n繁星\n潮水'; await ui.type('dictation-words', raw);
    let editor = await editChinesePronunciation(ui, '重庆');
    assert.equal(editor.input.maxLength, 200, 'The browser allows UTF-16 pairs while validation enforces 100 Unicode characters');
    editor.input.value = '重 庆, city'; await editor.input.fire('input');
    await editor.button('preview').fire('click');
    assert.equal(ui.state.spoken.at(-1).text, '重 庆, city');
    assert.equal(ui.field('dictation-words').value, raw, 'Unsaved pronunciation preview must not replace written text');
    await editor.button('cancel').fire('click');
    assert.equal(ui.state.calls.some((call) => call.method === 'PUT'), false);
    assert.equal(ui.field('dictation-words').value, raw);
    editor = await editChinesePronunciation(ui, '重庆');
    assert.match(editor.form.textContent, /整份|全部|整个|当前.*词表/);
    editor.input.value = 'chóng qìng'; await editor.input.fire('input'); await editor.button('save').fire('click');
    assert.deepEqual(ui.state.calls.findLast((call) => call.method === 'PUT').body.words, [
        { text: '重庆', spoken_text: 'chóng qìng' }, { text: '繁星', spoken_text: '繁星' }, { text: '潮水', spoken_text: '潮水' },
    ]);
    assert.match(ui.field('dictation-words').value, /重庆｜chóng qìng/);
    assert.deepEqual(ui.state.wordlists.get('lesson-01').words.map((word) => word.text), ['重庆', '繁星', '潮水']);
    editor = await editChinesePronunciation(ui, '重庆'); await editor.button('reset').fire('click');
    assert.equal(ui.state.wordlists.get('lesson-01').words[0].spoken_text, '重庆');
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 2);
});

test('Mandarin pronunciation validation and failed or conflicted saves retain a scoped recoverable correction draft', async () => {
    const storage = new Map(); const ui = dictationUI({ storage, words: ['重庆'] }); await ui.openLesson();
    let editor = await editChinesePronunciation(ui, '重庆');
    for (const value of ['甲|乙', '甲｜乙', '甲\n乙', '甲\u200b乙', '甲'.repeat(101)]) {
        editor.input.value = value; await editor.input.fire('input'); await editor.button('save').fire('click');
        assert.equal(ui.state.calls.some((call) => call.method === 'PUT'), false, 'Invalid hints cannot be sent');
        assert.equal(ui.field('dictation-words').value, '重庆');
    }
    ui.state.routes[wordPath()] = (call) => { if (call.method === 'PUT') throw new Error('读音保存网络失败'); return ui.state.defaultReply(call); };
    editor.input.value = 'chóng qìng'; await editor.input.fire('input'); await editor.button('save').fire('click');
    assert.equal(ui.root.querySelector('.dictation-pronunciation-input').value, 'chóng qìng');
    const key = `itorange-dictation-draft:alice:${bookId}:lesson-01`;
    assert.match(JSON.parse(storage.get(key)).raw, /重庆｜chóng qìng/);
    const restored = dictationUI({ storage, words: ['重庆'] }); await restored.openLesson();
    assert.match(restored.field('dictation-words').value, /重庆｜chóng qìng/);
    restored.state.wordlists.get('lesson-01').revision = 2;
    restored.state.routes[wordPath()] = (call) => { if (call.method === 'PUT') throw Object.assign(new Error('新版冲突'), { status: 409 }); return restored.state.defaultReply(call); };
    editor = await editChinesePronunciation(restored, '重庆'); await editor.button('save').fire('click');
    assert.equal(restored.field('dictation-conflict').hidden, false);
    assert.match(restored.field('dictation-words').value, /重庆｜chóng qìng/);
    assert.equal(JSON.parse(storage.get(key)).base_revision, 1);
    restored.api.reset(); restored.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    await restored.openLesson();
    assert.equal(restored.field('dictation-words').value, '重庆', 'Another learner does not inherit the private correction');
    assert.equal(restored.root.querySelector('.dictation-pronunciation-editor'), null);
    assert.match(JSON.parse(storage.get(key)).raw, /chóng qìng/);
});

test('correcting one Mandarin mistake source preserves other sources, original lists, timestamps and an existing round snapshot', async () => {
    const ui = dictationUI({ words: ['重庆'], mistakes: [
        { lesson_id: 'lesson-01', words: ['重庆'] }, { lesson_id: 'lesson-02', words: ['重庆'] },
    ] });
    const originalList = clone(ui.state.wordlists.get('lesson-01'));
    const originalGroups = ui.state.mistakeGroups();
    await ui.openLesson(); await ui.type('dictation-repetitions', '1', 'change'); await ui.openMistakes();
    await ui.chooseMistakeLessons(['lesson-01', 'lesson-02']); await ui.field('dictation-mistakes-start').fire('click');
    await ui.field('dictation-player-exit').fire('click');
    const editor = await editChinesePronunciation(ui, '重庆', 'lesson-01');
    editor.input.value = 'chóng qìng'; await editor.input.fire('input'); await editor.button('save').fire('click');
    const saved = ui.state.calls.findLast((call) => call.method === 'POST' && call.url === prefix + '/mistakes');
    assert.deepEqual(saved.body.items, [{ book_id: bookId, lesson_id: 'lesson-01', words: [{ text: '重庆', spoken_text: 'chóng qìng' }] }]);
    const groups = ui.state.mistakeGroups();
    assert.equal(groups[0].words[0].spoken_text, 'chóng qìng');
    assert.equal(groups[0].words[0].added_at, originalGroups[0].words[0].added_at);
    assert.deepEqual(groups[1], originalGroups[1]);
    assert.deepEqual(ui.state.wordlists.get('lesson-01'), originalList);
    assert.equal(ui.state.calls.some((call) => call.method === 'PUT'), false);
    await ui.field('dictation-resume').fire('click'); await ui.finishRun(1);
    assert.deepEqual(ui.state.spoken.map((utterance) => utterance.text), ['重庆'], 'A resumed round keeps the pronunciation and merged sources from its start');
});


test('Chinese speech uses device Mandarin when fresh capabilities are disabled, unavailable or exceed the short lookup deadline', { timeout: 3000 }, async () => {
    for (const mode of ['disabled', 'pending', 'failure']) {
        const ui = dictationUI(); const gate = deferred();
        if (mode === 'pending') ui.state.routes[prefix + '/audio/capabilities'] = () => gate.promise;
        if (mode === 'failure') ui.state.routes[prefix + '/audio/capabilities'] = () => { throw new Error('optional capabilities unavailable'); };
        await ui.openLesson(); await ui.start(); await ui.field('dictation-play').fire('click');
        if (mode === 'pending') {
            await ui.tick(1499); assert.equal(ui.state.spoken.length, 0, 'Playback waits briefly for a fresh admin setting');
            await ui.tick(1);
        }
        assert.equal(ui.state.spoken.length, 1, mode);
        assert.equal(ui.state.spoken[0].text, '潮水'); assert.equal(ui.state.spoken[0].voice.lang, 'zh-CN');
        assert.equal(ui.state.audioRequests.length, 0);
        assert.equal(ui.state.calls.some((call) => call.url === prefix + '/audio/prepare'), false);
        assert.doesNotMatch(ui.field('dictation-message').textContent, /optional capabilities unavailable/);
        if (mode === 'pending') { gate.resolve({ kokoro: false, prepare: false }); await ui.flush(); assert.equal(ui.state.spoken.length, 1); }
    }
});

test('enabled Chinese server audio fetches authenticated corrected speech, repeats one WAV twice and waits for manual advancement', async () => {
    const ui = dictationUI({ voices: [], capabilities: { kokoro: true, prepare: true }, words: [{ text: '重庆', spoken_text: 'chóng qìng' }, '繁星'] });
    await ui.openLesson(); await ui.flush(); await ui.start();
    assert.equal(ui.state.audioRequests.length, 0);
    assert.equal(ui.state.spoken.length, 0);
    assert.doesNotMatch(ui.visibleText(), /重庆|chóng qìng|繁星/);
    await ui.space();
    assert.equal(ui.state.audioRequests.length, 1);
    assert.deepEqual(ui.state.audioRequests[0].body, { text: 'chóng qìng' });
    assert.equal(ui.state.audioRequests[0].method, 'POST');
    assert.equal(ui.state.audioRequests[0].headers.Authorization, 'Bearer alice-token');
    assert.equal(ui.state.audioPlayed[0].playbackRate, 1);
    assert.equal(ui.state.spoken.length, 0, 'A successful server clip works without installed Mandarin voices');
    const audio = ui.state.currentAudio;
    await ui.space(); await ui.field('dictation-next').fire('click'); assert.equal(ui.state.audioRequests.length, 1);
    await ui.finishAudio(); await ui.tick(699); assert.equal(ui.state.audioPlayed.length, 1);
    await ui.tick(1); assert.equal(ui.state.audioPlayed.length, 2); assert.strictEqual(ui.state.currentAudio, audio);
    assert.equal(ui.state.audioRequests.length, 1, 'Both repetitions reuse one recording');
    await ui.finishAudio(); await ui.tick(1000);
    assert.equal(ui.state.audioPlayed.length, 2);
    assert.equal(ui.state.objectUrls.size, 0); assert.equal(ui.state.revoked.length, 1);
    await ui.space(); assert.deepEqual(ui.state.audioRequests.at(-1).body, { text: '繁星' });
});

test('Chinese server errors, invalid WAVs, rejected play and a 25-second timeout fall back to the round Mandarin voice snapshot', { timeout: 3000 }, async () => {
    for (const mode of ['network', 'mime', 'empty', 'play', 'timeout', 'late-response', 'late-blob']) {
        const voice = { name: 'Chosen Mainland', lang: 'zh-CN', voiceURI: 'chosen' };
        const ui = dictationUI({ voices: [voice], capabilities: { kokoro: true, prepare: true } }); const late = deferred();
        if (mode === 'late-response') ui.state.audioFetch = () => late.promise;
        if (mode === 'late-blob') ui.state.audioFetch = () => ({ ...ui.state.audioResponse(), blob: () => late.promise });
        if (mode === 'network') ui.state.audioFetch = () => { throw new Error('audio unavailable'); };
        if (mode === 'mime') ui.state.audioFetch = () => ui.state.audioResponse(200, { type: 'application/json' });
        if (mode === 'empty') ui.state.audioFetch = () => ui.state.audioResponse(200, { blob: new Blob([], { type: 'audio/wav' }) });
        if (mode === 'play') ui.state.audioPlayError = new Error('audio playback rejected');
        if (mode === 'timeout') ui.state.audioFetch = (call) => new Promise((resolve, reject) => call.signal.addEventListener('abort', () => reject(Object.assign(new Error('timed out'), { name: 'AbortError' }))));
        await ui.openLesson(); await ui.flush(); await ui.start();
        await ui.voicesChanged([{ name: 'Xiaoxiao Premium', lang: 'zh-CN', voiceURI: 'later' }]);
        await ui.field('dictation-play').fire('click');
        if (mode === 'timeout' || mode.startsWith('late-')) {
            await ui.tick(24999); assert.equal(ui.state.spoken.length, 0);
            await ui.tick(1); assert.equal(ui.state.audioRequests[0].signal.aborted, true);
        }
        if (mode.startsWith('late-')) {
            late.resolve(mode === 'late-response' ? ui.state.audioResponse() : new Blob(['RIFF-new-WAVE'], { type: 'audio/wav' })); await ui.flush();
            assert.equal(ui.state.audioPlayed.length, 0, 'Late server audio cannot overlap the device fallback');
        }
        assert.equal(ui.state.spoken.length, 1, mode);
        assert.strictEqual(ui.state.spoken[0].voice, voice, mode + ' keeps the original Mainland voice');
        assert.equal(ui.state.spoken[0].text, '潮水'); assert.equal(ui.state.spoken[0].rate, 1);
        assert.equal(ui.state.objectUrls.size, 0, mode + ' releases any server blob before fallback');
    }
    for (const status of [401, 403]) {
        const ui = dictationUI({ capabilities: { kokoro: true, prepare: true } }); ui.state.audioStatus = status;
        await ui.openLesson(); await ui.flush(); await ui.start(); await ui.field('dictation-play').fire('click');
        assert.equal(ui.state.spoken.length, 0, 'Expired authorization cannot fall back to device playback');
        assert.equal(ui.state.audioPlayed.length, 0); assert.equal(ui.field('dictation-next').disabled, true);
        assert.match(ui.field('dictation-message').textContent, /登录/);
        ui.state.audioStatus = 200; await ui.field('dictation-play').fire('click');
        assert.deepEqual(ui.state.audioRequests.at(-1).body, { text: '潮水' }); assert.equal(ui.state.audioPlayed.length, 1);
    }
});

test('Chinese audio requests, late blobs and stale playback failures cannot resume after leaving or account reset', { timeout: 3000 }, async () => {
    const capability = { kokoro: true, prepare: true };
    const gate = deferred(); const ui = dictationUI({ capabilities: capability }); ui.state.audioFetch = () => gate.promise;
    await ui.openLesson(); await ui.flush(); await ui.start(); await ui.field('dictation-play').fire('click');
    const pending = ui.state.audioRequests[0]; ui.api.leaveView(); assert.equal(pending.signal.aborted, true);
    gate.resolve(ui.state.audioResponse()); await ui.flush();
    assert.equal(ui.state.audioPlayed.length, 0); assert.equal(ui.state.spoken.length, 0); assert.equal(ui.state.objectUrls.size, 0);
    ui.state.audioFetch = null; await ui.api.open(); await ui.field('dictation-resume').fire('click'); await ui.field('dictation-play').fire('click');
    const audio = ui.state.currentAudio, ended = audio.onended; const playCount = ui.state.audioPlayed.length;
    ui.api.reset(); ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    ended(); await ui.tick(1000);
    assert.equal(ui.state.audioPlayed.length, playCount); assert.equal(ui.state.objectUrls.size, 0); assert.ok(audio.pauseCount > 0);
    const blobGate = deferred(); const blobUI = dictationUI({ capabilities: capability });
    blobUI.state.audioFetch = () => ({ ...blobUI.state.audioResponse(), blob: () => blobGate.promise });
    await blobUI.openLesson(); await blobUI.flush(); await blobUI.start(); await blobUI.field('dictation-play').fire('click');
    blobUI.api.reset(); blobGate.resolve(new Blob(['RIFF-new-WAVE'], { type: 'audio/wav' })); await blobUI.flush();
    assert.equal(blobUI.state.audios.length, 0); assert.equal(blobUI.state.spoken.length, 0);
    const playing = deferred(); const playUI = dictationUI({ capabilities: capability }); playUI.state.audioPlay = () => playing.promise;
    await playUI.openLesson(); await playUI.flush(); await playUI.start(); await playUI.field('dictation-play').fire('click');
    const oldAudio = playUI.state.currentAudio, oldError = oldAudio.onerror;
    playUI.api.leaveView(); playing.reject(new Error('late play failure')); oldError(); await playUI.flush();
    assert.equal(playUI.state.spoken.length, 0); assert.equal(playUI.state.objectUrls.size, 0); assert.ok(oldAudio.pauseCount > 0);
});

test('Chinese prewarming retains the newest saved or corrected snapshot and never waits for capabilities or the background queue', { timeout: 3000 }, async () => {
    const ui = dictationUI(); const caps = deferred(), queue = deferred();
    ui.state.routes[prefix + '/audio/capabilities'] = () => caps.promise;
    ui.state.routes[prefix + '/audio/prepare'] = () => queue.promise;
    await ui.openLesson();
    await ui.type('dictation-words', '重庆｜chóng qìng\n长大｜zhǎng dà'); await ui.field('dictation-save').fire('click');
    await ui.type('dictation-words', '重庆｜chóng qìng\n音乐｜yīn yuè'); await ui.field('dictation-save').fire('click');
    assert.equal(ui.state.calls.some((call) => call.url === prefix + '/audio/prepare'), false);
    caps.resolve({ kokoro: true, prepare: true }); await ui.flush();
    assert.deepEqual(ui.state.calls.findLast((call) => call.url === prefix + '/audio/prepare').body, { texts: ['chóng qìng', 'yīn yuè'] });
    assert.equal(ui.state.audioPlayed.length, 0); assert.equal(ui.state.spoken.length, 0);
    let editor = await editChinesePronunciation(ui, '音乐'); editor.input.value = 'yīn yuè revised'; await editor.input.fire('input');
    await editor.button('preview').fire('click');
    assert.deepEqual(ui.state.audioRequests.at(-1).body, { text: 'yīn yuè revised' }, 'Inline correction previews the unsaved pronunciation through the enabled server');
    assert.equal(ui.state.wordlists.get('lesson-01').words[1].spoken_text, 'yīn yuè');
    await editor.button('save').fire('click');
    assert.deepEqual(ui.state.calls.findLast((call) => call.url === prefix + '/audio/prepare').body, { texts: ['chóng qìng', 'yīn yuè revised'] });
    assert.equal(ui.state.wordlists.get('lesson-01').words[1].text, '音乐');
    await ui.start(); assert.equal(ui.field('dictation-player').hidden, false);
    queue.reject(new Error('预加载失败')); await ui.flush();
    assert.equal(ui.field('dictation-player').hidden, false); assert.doesNotMatch(ui.field('dictation-message').textContent, /预加载失败/);
    await ui.field('dictation-play').fire('click'); assert.deepEqual(ui.state.audioRequests.at(-1).body, { text: 'chóng qìng' });
    const words = Array.from({ length: 250 }, (_, index) => ({ text: '词语' + index, spoken_text: '提示' + (index === 1 ? 0 : index) }));
    const large = dictationUI({ capabilities: { kokoro: true, prepare: true }, mistakes: [{ lesson_id: 'lesson-01', words }] });
    await large.api.open(); await large.flush(); await large.openMistakes(); await large.chooseMistakeLessons(['lesson-01']); await large.field('dictation-mistakes-start').fire('click');
    assert.deepEqual(large.state.calls.findLast((call) => call.url === prefix + '/audio/prepare').body.texts, [...new Set(words.map((word) => word.spoken_text))].slice(0, 200));
    assert.match(large.field('dictation-position').textContent, /250/);
    assert.equal(large.state.audioRequests.length, 0);
});

test('Chinese server capabilities stay account-scoped and a late old capability reply cannot enable another learner', { timeout: 3000 }, async () => {
    const ui = dictationUI(); const gate = deferred();
    ui.state.routes[prefix + '/audio/capabilities'] = () => gate.promise;
    await ui.openLesson(); await ui.start(); await ui.api.open();
    assert.ok(ui.state.calls.some((call) => call.url === prefix + '/audio/capabilities' && call.student === 'alice'));
    ui.api.reset(); ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    delete ui.state.routes[prefix + '/audio/capabilities']; await ui.openLesson(); await ui.type('dictation-words', '小明词语'); await ui.start();
    gate.resolve({ kokoro: true, prepare: true }); await ui.flush();
    assert.ok(ui.state.calls.some((call) => call.url === prefix + '/audio/capabilities' && call.student === 'bob'));
    assert.equal(ui.state.calls.some((call) => call.url === prefix + '/audio/prepare'), false);
    assert.equal(ui.state.audioRequests.length, 0); assert.equal(ui.state.spoken.length, 0);
    await ui.field('dictation-play').fire('click');
    assert.equal(ui.state.spoken[0].text, '小明词语'); assert.equal(ui.state.audioRequests.length, 0);
});


test('Chinese playback observes the current admin off-on-off setting on every manual replay without logging out', async () => {
    const ui = dictationUI(); await ui.openLesson(); await ui.start();
    const capabilityCalls = () => ui.state.calls.filter((call) => call.raw && call.url === prefix + '/audio/capabilities').length;
    let before = capabilityCalls();
    await ui.field('dictation-play').fire('click');
    assert.ok(capabilityCalls() > before, 'The first click checks current capabilities even after opening and preparing the run');
    assert.equal(ui.state.spoken.length, 1); assert.equal(ui.state.audioRequests.length, 0);
    await ui.finishItem(); assert.equal(ui.state.spoken.length, 2);
    ui.state.capabilities = { kokoro: true, prepare: true }; before = capabilityCalls();
    await ui.field('dictation-play').fire('click');
    assert.ok(capabilityCalls() > before); assert.equal(ui.state.audioRequests.length, 1);
    assert.deepEqual(ui.state.audioRequests[0].body, { text: '潮水' });
    const checked = capabilityCalls(), audio = ui.state.currentAudio;
    await ui.finishItem();
    assert.equal(capabilityCalls(), checked, 'The two repetitions in one listening action share one capability decision');
    assert.equal(ui.state.audioPlayed.length, 2); assert.strictEqual(ui.state.audioPlayed[1].audio, audio);
    ui.state.capabilities = { kokoro: false, prepare: false }; before = capabilityCalls();
    await ui.field('dictation-play').fire('click');
    assert.ok(capabilityCalls() > before);
    assert.equal(ui.state.audioRequests.length, 1, 'Turning off cannot replay an earlier Kokoro blob');
    assert.equal(ui.state.spoken.length, 3); await ui.finishItem(); assert.equal(ui.state.spoken.length, 4);
    assert.equal(ui.state.calls.every((call) => call.student === 'alice'), true);
    assert.match(ui.field('dictation-position').textContent, /1/);
});

test('Chinese capability timeouts, stale replies and canceled lookups cannot switch or duplicate a listening action', { timeout: 3000 }, async () => {
    const ui = dictationUI(); await ui.openLesson(); await ui.start();
    const gate = deferred(); ui.state.routes[prefix + '/audio/capabilities'] = () => gate.promise;
    await ui.field('dictation-play').fire('click');
    const lookup = ui.state.calls.findLast((call) => call.url === prefix + '/audio/capabilities');
    assert.equal(lookup.raw, true); assert.equal(lookup.method, 'GET'); assert.equal(lookup.headers.Authorization, 'Bearer alice-token');
    assert.equal(lookup.credentials, 'same-origin'); assert.equal(lookup.cache, 'no-store');
    await ui.tick(1499); assert.equal(ui.state.spoken.length, 0); assert.equal(ui.state.audioRequests.length, 0);
    await ui.tick(1); assert.equal(ui.state.spoken.length, 1); assert.equal(lookup.signal.aborted, true);
    gate.resolve({ kokoro: true, prepare: true }); await ui.flush();
    assert.equal(ui.state.audioRequests.length, 0, 'A late enabled result cannot start server audio over the fallback');
    assert.equal(ui.state.spoken.length, 1); await ui.finishItem(); assert.equal(ui.state.spoken.length, 2);
    delete ui.state.routes[prefix + '/audio/capabilities']; await ui.field('dictation-play').fire('click');
    assert.equal(ui.state.audioRequests.length, 0); assert.equal(ui.state.spoken.length, 3, 'A stale true result cannot override the next fresh false result');
    ui.api.leaveView(); await ui.api.open(); await ui.field('dictation-resume').fire('click');
    const canceled = deferred(); ui.state.routes[prefix + '/audio/capabilities'] = () => canceled.promise;
    await ui.field('dictation-play').fire('click');
    const pending = ui.state.calls.findLast((call) => call.url === prefix + '/audio/capabilities'); const spoken = ui.state.spoken.length;
    ui.api.reset(); ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    assert.equal(pending.signal.aborted, true);
    canceled.resolve({ kokoro: true, prepare: true }); await ui.tick(2000); await ui.flush();
    assert.equal(ui.state.audioRequests.length, 0); assert.equal(ui.state.spoken.length, spoken);
    assert.equal(ui.field('dictation-player').hidden, true); assert.equal(ui.state.objectUrls.size, 0);
    const slowJSON = dictationUI(); await slowJSON.openLesson(); await slowJSON.start(); const jsonGate = deferred();
    slowJSON.state.capabilityFetch = () => ({ status: 200, ok: true, json: () => jsonGate.promise });
    await slowJSON.field('dictation-play').fire('click'); await slowJSON.tick(1500);
    assert.equal(slowJSON.state.spoken.length, 1, 'The short capability deadline also includes reading its JSON body');
    jsonGate.resolve({ kokoro: true, prepare: true }); await slowJSON.flush();
    assert.equal(slowJSON.state.spoken.length, 1); assert.equal(slowJSON.state.audioRequests.length, 0);
});

test('Chinese expired authorization during the fresh capability check requires login and never starts device or server speech', async () => {
    for (const status of [401, 403]) {
        const ui = dictationUI(); await ui.openLesson(); await ui.start();
        ui.state.routes[prefix + '/audio/capabilities'] = () => { throw Object.assign(new Error('session expired'), { status }); };
        await ui.field('dictation-play').fire('click'); await ui.tick(2000);
        assert.equal(ui.state.spoken.length, 0); assert.equal(ui.state.audioPlayed.length, 0); assert.equal(ui.state.audioRequests.length, 0);
        assert.equal(ui.field('dictation-next').disabled, true); assert.match(ui.field('dictation-message').textContent, /登录/);
        delete ui.state.routes[prefix + '/audio/capabilities']; await ui.field('dictation-play').fire('click');
        assert.equal(ui.state.spoken[0].text, '潮水'); assert.equal(ui.state.audioRequests.length, 0);
    }
});

test('Chinese word previews and unsaved correction previews also read the latest admin setting', async () => {
    const ui = dictationUI({ words: ['重庆'] }); await ui.openLesson();
    const preview = () => ui.field('dictation-word-preview').querySelectorAll('button').find((button) => button.getAttribute('aria-label') === '试听 重庆');
    await preview().fire('click'); assert.equal(ui.state.spoken.length, 1); assert.equal(ui.state.audioRequests.length, 0); await ui.finishSpeech();
    ui.state.capabilities = { kokoro: true, prepare: true };
    await preview().fire('click'); assert.equal(ui.state.audioRequests.length, 1); await ui.finishAudio();
    const editor = await editChinesePronunciation(ui, '重庆'); editor.input.value = 'chóng qìng'; await editor.input.fire('input');
    ui.state.capabilities = { kokoro: false, prepare: false }; await editor.button('preview').fire('click');
    assert.equal(ui.state.spoken.at(-1).text, 'chóng qìng'); assert.equal(ui.state.audioRequests.length, 1); await ui.finishSpeech();
    ui.state.capabilities = { kokoro: true, prepare: true }; await editor.button('preview').fire('click');
    assert.deepEqual(ui.state.audioRequests.at(-1).body, { text: 'chóng qìng' }); assert.equal(ui.state.audioRequests.length, 2);
    assert.equal(ui.field('dictation-words').value, '重庆'); assert.equal(ui.state.calls.some((call) => call.method === 'PUT'), false);
});


test('Chinese saving and starting refresh preparation capabilities after admin toggles without blocking the learner', async () => {
    const ui = dictationUI(); await ui.openLesson();
    const prepared = () => ui.state.calls.filter((call) => call.url === prefix + '/audio/prepare');
    await ui.type('dictation-words', '第一词语'); await ui.field('dictation-save').fire('click');
    assert.equal(prepared().length, 0);
    ui.state.capabilities = { kokoro: true, prepare: true };
    await ui.type('dictation-words', '第二词语'); await ui.field('dictation-save').fire('click');
    assert.deepEqual(prepared().at(-1)?.body, { texts: ['第二词语'] }, 'A fresh enabled capability result prewarms the newly saved content');
    const before = prepared().length;
    ui.state.capabilities = { kokoro: false, prepare: false };
    await ui.type('dictation-words', '第三词语'); await ui.field('dictation-save').fire('click');
    assert.equal(prepared().length, before, 'An old enabled result cannot enqueue after the current admin setting is off');
    assert.equal(ui.field('dictation-save').disabled, false);
    await ui.start(); assert.equal(ui.field('dictation-player').hidden, false); assert.equal(prepared().length, before);
    await ui.field('dictation-player-exit').fire('click'); ui.state.capabilities = { kokoro: true, prepare: true };
    await ui.start(); assert.deepEqual(prepared().at(-1)?.body, { texts: ['第三词语'] });
    assert.equal(ui.field('dictation-player').hidden, false); assert.equal(ui.state.audioRequests.length, 0); assert.equal(ui.state.spoken.length, 0);
});


test('parents can directly add saved words without starting dictation or saving the open raw draft', async () => {
    const ui = dictationUI({ isParent: true, words: [{ text: '薄', spoken_text: '单薄的薄' }, '繁星'],
        mistakes: [{ lesson_id: 'lesson-01', words: [{ text: '薄', spoken_text: '薄雾的薄' }] }] });
    const original = clone(ui.state.wordlists.get('lesson-01'));
    await ui.openLesson(); await ui.type('dictation-words', '本机尚未保存的词语');
    await ui.openMistakes();
    assert.equal(ui.field('dictation-mistakes-add-panel').hidden, true);
    await ui.field('dictation-mistakes-add').fire('click');
    assert.equal(ui.field('dictation-mistakes-add-panel').hidden, false);
    const inputs = ui.field('dictation-mistakes-add-words').querySelectorAll('input');
    assert.deepEqual(inputs.map((input) => input.value), ['薄', '繁星']);
    assert.equal(inputs[0].disabled, true); assert.match(inputs[0].parentElement.textContent, /已加入/);
    await ui.field('dictation-mistakes-add-select-all').fire('click');
    assert.match(ui.field('dictation-mistakes-add-submit').textContent, /1/);
    await ui.field('dictation-mistakes-add-submit').fire('click');
    const call = ui.state.calls.findLast((call) => call.method === 'POST' && call.url === prefix + '/mistakes');
    assert.equal(call.student, 'alice'); assert.equal(call.username, 'alice_parent');
    assert.deepEqual(call.body, { preserve_existing: true,
        items: [{ book_id: bookId, lesson_id: 'lesson-01', words: [{ text: '繁星', spoken_text: '繁星' }] }] });
    assert.equal(ui.state.mistakeGroups()[0].words.find((word) => word.text === '薄').spoken_text, '薄雾的薄');
    assert.match(ui.field('dictation-mistakes-status').textContent, /共 1 篇课文，2 条错词/);
    assert.deepEqual(ui.state.wordlists.get('lesson-01'), original);
    assert.equal(ui.field('dictation-words').value, '本机尚未保存的词语');
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, 0);
    assert.equal(ui.field('dictation-player').hidden, true); assert.equal(ui.state.spoken.length, 0);
    assert.ok(ui.field('dictation-mistakes-add-words').querySelectorAll('input').every((input) => input.disabled && !input.checked));
    await ui.field('dictation-mistakes-add-close').fire('click');
    assert.equal(ui.field('dictation-mistakes-add-panel').hidden, true);
});


test('direct addition refreshes saved snapshots, carries pronunciation hints and handles empty lists', async () => {
    const empty = dictationUI({ words: [] }); await empty.api.open(); await empty.openMistakes();
    await empty.field('dictation-mistakes-add').fire('click');
    assert.match(empty.field('dictation-mistakes-add-status').textContent, /录入.*保存/);
    assert.equal(empty.field('dictation-mistakes-add-submit').disabled, true);
    const ui = dictationUI(); await ui.api.open(); await ui.openMistakes();
    const second = clone(ui.state.wordlists.get('lesson-02'));
    second.words = [{ text: '长大', spoken_text: '慢慢长大' }]; second.word_count = 1; second.revision = 1;
    ui.state.wordlists.set('lesson-02', second);
    await ui.field('dictation-mistakes-add').fire('click');
    assert.equal(ui.field('dictation-mistakes-add-lesson').querySelectorAll('option').length, 2);
    await ui.type('dictation-mistakes-add-lesson', 'lesson-02', 'change');
    await ui.field('dictation-mistakes-add-select-all').fire('click');
    await ui.field('dictation-mistakes-add-clear').fire('click');
    assert.equal(ui.field('dictation-mistakes-add-submit').disabled, true);
    await ui.field('dictation-mistakes-add-select-all').fire('click');
    await ui.field('dictation-mistakes-add-submit').fire('click');
    const saved = ui.state.calls.findLast((call) => call.method === 'POST' && call.url === prefix + '/mistakes');
    assert.deepEqual(saved.body.items, [{ book_id: bookId, lesson_id: 'lesson-02', words: [{ text: '长大', spoken_text: '慢慢长大' }] }]);
    assert.equal(ui.field('dictation-editor').hidden, true, 'Picking a source must not change the main wordlist editor');
});


test('direct-add retries keep checks and atomically preserve a concurrently corrected existing word', async () => {
    const ui = dictationUI({ words: [{ text: '薄', spoken_text: '单薄的薄' }] });
    await ui.api.open(); await ui.openMistakes(); await ui.field('dictation-mistakes-add').fire('click');
    await ui.field('dictation-mistakes-add-select-all').fire('click');
    ui.state.routes[prefix + '/mistakes'] = () => { throw new Error('暂时无法加入'); };
    await ui.field('dictation-mistakes-add-submit').fire('click');
    assert.equal(ui.field('dictation-mistakes-add-words').querySelector('input').checked, true);
    assert.equal(ui.field('dictation-mistakes-add-submit').disabled, false);
    assert.match(ui.field('dictation-mistakes-add-status').textContent, /保留.*重试/);
    ui.state.seedMistakes([{ lesson_id: 'lesson-01', words: [{ text: '薄', spoken_text: '另一页已修好的读音' }] }]);
    const concurrent = clone(ui.state.mistakeGroups()[0].words[0]);
    delete ui.state.routes[prefix + '/mistakes'];
    await ui.field('dictation-mistakes-add-submit').fire('click');
    assert.deepEqual(ui.state.mistakeGroups()[0].words[0], concurrent);
    assert.equal(ui.field('dictation-mistakes-add-words').querySelector('input').disabled, true);
    assert.match(ui.field('dictation-mistakes-add-status').textContent, /已有 1/);
});


test('direct-add busy controls prevent duplicate submission and late completion cannot enter another account', async () => {
    const ui = dictationUI(); await ui.api.open(); await ui.openMistakes();
    await ui.field('dictation-mistakes-add').fire('click'); await ui.field('dictation-mistakes-add-select-all').fire('click');
    const gate = deferred(); let oldCall;
    ui.state.routes[prefix + '/mistakes'] = (call) => { oldCall = call; return gate.promise; };
    const pending = ui.field('dictation-mistakes-add-submit').fire('click'); await ui.flush();
    assert.equal(ui.field('dictation-book').disabled, true);
    assert.equal(ui.field('dictation-mode-lessons').disabled, true);
    assert.equal(ui.field('dictation-mistakes-add-close').disabled, true);
    await ui.field('dictation-mistakes-add-submit').fire('click');
    assert.equal(ui.state.calls.filter((call) => call.method === 'POST' && call.url === prefix + '/mistakes').length, 1);
    ui.api.reset(); ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    ui.state.seedMistakes([{ lesson_id: 'lesson-02', words: ['新账号错词'] }], 'bob');
    delete ui.state.routes[prefix + '/mistakes'];
    await ui.api.open(); await ui.openMistakes();
    gate.resolve(ui.state.defaultReply(oldCall)); await pending; await ui.flush();
    assert.match(ui.field('dictation-mistakes-groups').textContent, /新账号错词/);
    assert.doesNotMatch(ui.field('dictation-mistakes-groups').textContent, /潮水/);
    assert.equal(ui.field('dictation-mistakes-add-panel').hidden, true);
});


test('direct-add saved-list replies cannot revive a closed view or cross into another textbook', async () => {
    const ui = dictationUI();
    const secondBook = { id: 'grade4-next-book', title: '另一册', edition: '测试', grade: 4, term: 2,
        lessons: [{ id: 'lesson-next', title: '新课文', number: 1, unit: '另一单元', custom: false }] };
    ui.state.books.push(secondBook);
    await ui.api.open(); await ui.openMistakes();
    const path = prefix + '/wordlists?book_id=' + bookId, gate = deferred();
    ui.state.routes[path] = () => gate.promise;
    const pending = ui.field('dictation-mistakes-add').fire('click'); await ui.flush();
    const request = ui.state.calls.findLast((call) => call.url === path);
    assert.equal(ui.field('dictation-book').disabled, true);
    ui.api.leaveView(); assert.equal(request.signal.aborted, true);
    delete ui.state.routes[path];
    await ui.api.open(); await ui.type('dictation-book', secondBook.id, 'change');
    await ui.field('dictation-mistakes-add').fire('click');
    gate.resolve({ items: [{ book_id: bookId, lesson_id: 'lesson-01', revision: 2, words: [{ text: '迟到的旧册词语', spoken_text: '迟到的旧册词语' }] }], count: 1 });
    await pending; await ui.flush();
    assert.equal(ui.field('dictation-book').value, secondBook.id);
    assert.doesNotMatch(ui.field('dictation-mistakes-add-words').textContent, /迟到|潮水|繁星/);
    assert.match(ui.field('dictation-mistakes-add-status').textContent, /录入.*保存/);
});
