const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const prefix = '/subjects/english/dictation';
const bookId = 'english-custom-dictation';
const wordPath = (lesson = 'default-list') => `${prefix}/wordlists/${bookId}/${lesson}`;
const settle = () => new Promise(setImmediate);
const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function englishDictationUI({ storage = new Map(), isParent = false, voices, words = ['apple', 'Where is my schoolbag?'], revision = 1, mistakes = [], mistakeStore = new Map() } = {}) {
    const html = fs.readFileSync(path.join(__dirname, 'static/index.html'), 'utf8');
    const source = fs.readFileSync(path.join(__dirname, 'static/js/english-dictation.js'), 'utf8');
    const elements = new Map(), timers = new Map();
    let timerId = 0, now = 0;
    const state = { calls: [], routes: {}, listeners: {}, clipboard: '', section: 'dictation', spoken: [], cancelled: 0, speechListeners: {}, audioRequests: [], audios: [], audioPlayed: [], objectUrls: new Map(), revoked: [] };
    state.session = { token: 'alice-token', username: isParent ? 'alice_parent' : 'alice',
        student: 'alice', isParent, generation: 1 };
    state.book = { id: bookId, title: '我的英语默写', grade: null, term: null, edition: '自建清单',
        lessons: [
            { id: 'default-list', title: '我的生词清单', number: 1, unit: '我的清单', custom: false },
            { id: 'custom-2', title: '重点句子', number: 2, unit: '我的清单', custom: true },
            { id: 'custom-3', title: '课外积累', number: 3, unit: '我的清单', custom: true },
        ] };
    state.books = [state.book];
    state.wordlists = new Map();
    state.wordlistsByBook = new Map([[bookId, state.wordlists]]);
    state.wordlistsByStudent = new Map([['alice', state.wordlistsByBook]]);
    const listsFor = (student, selectedBook) => {
        if (!state.wordlistsByStudent.has(student)) state.wordlistsByStudent.set(student, new Map());
        const books = state.wordlistsByStudent.get(student);
        if (!books.has(selectedBook)) books.set(selectedBook, new Map());
        return books.get(selectedBook);
    };
    function wordlist(lesson, entries = [], base = 0, selectedBook = bookId) {
        return { book_id: selectedBook, lesson_id: lesson, words: entries.map((item) => typeof item === 'string' ? { text: item, spoken_text: item } : clone(item)),
            word_count: entries.length, revision: base, updated_at: base ? '2026-11-13T12:00:00+08:00' : null, updated_by: base ? 'alice' : null };
    }
    state.wordlists.set('default-list', wordlist('default-list', words, revision));
    state.wordlists.set('custom-2', wordlist('custom-2'));
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
        { name: 'Chinese default', lang: 'zh-CN', voiceURI: 'chinese', default: true },
        { name: 'English US', lang: 'en-US', voiceURI: 'english-us' },
        { name: 'English UK', lang: 'en-GB', voiceURI: 'english-uk' },
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
    assert.ok(field('english-dictation-panel'), 'The dictation panel must be integrated into the real subject page');
    field('main-page').classList.add('active');
    field('english-dictation-section').classList.add('active');
    field('login-page').classList.remove('active');
    field('english-dictation-panel').hidden = false;
    
    const document = { getElementById: field, createElement: (tag) => new Element(tag), createTextNode: (value) => Object.assign(new Element('text'), { textContent: value }),
        querySelectorAll: (query) => root.querySelectorAll(query), querySelector: (query) => root.querySelector(query),
        addEventListener(name, callback) { if (name === 'DOMContentLoaded') callback(); else (state.listeners[name] ||= []).push(callback); }, activeElement: null,
        body: root.querySelector('body'), hidden: false, visibilityState: 'visible' };

    state.defaultReply = (call) => {
        if (call.url === prefix + '/books') return { items: clone(state.books), count: state.books.length };
        if (call.url === prefix + '/lessons' && call.method === 'POST') {
            const lesson = { id: 'custom-' + (state.book.lessons.length + 1), title: call.body.title, number: call.body.number ?? null, custom: true, unit: '我的其他清单' };
            state.book.lessons.push(lesson);
            return { lesson: clone(lesson), duplicate: false };
        }
        const query = call.url.match(new RegExp(`^${prefix}/(lessons|wordlists|mistakes)\\?book_id=([^&]+)$`));
        if (query) {
            const selectedBook = decodeURIComponent(query[2]), book = state.books.find((item) => item.id === selectedBook);
            assert.ok(book, 'Unknown book: ' + selectedBook);
            const lists = listsFor(call.student, selectedBook);
            if (query[1] === 'lessons') { const items = call.student === 'alice' ? book.lessons : book.lessons.filter((lesson) => !lesson.custom); return { items: clone(items), count: items.length }; }
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
                    if (previous) { previous.spoken_text = word.spoken_text || word.text; previous.updated_by = call.username; existing += 1; continue; }
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
        const lists = listsFor(call.student, selectedBook);
        const current = lists.get(lesson) || wordlist(lesson, [], 0, selectedBook);
        if (call.method === 'GET') return { wordlist: clone(current), lesson: clone(state.books.find((item) => item.id === selectedBook)?.lessons.find((item) => item.id === lesson)) };
        assert.equal(call.method, 'PUT');
        assert.equal(call.body.base_revision, current.revision, 'Saves must use the current server revision');
        const entries = call.body.words.filter((word, index, list) => list.findIndex((other) => other.text === word.text) === index);
        const saved = wordlist(lesson, entries, current.revision + 1, selectedBook);
        lists.set(lesson, saved);
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
        static createObjectURL(blob) { assert.ok(blob instanceof Blob); const value = 'blob:english-dictation-' + (state.objectUrls.size + state.revoked.length + 1); state.objectUrls.set(value, blob); return value; }
        static revokeObjectURL(value) { state.revoked.push(value); state.objectUrls.delete(value); }
    }
    function audioResponse(status = 200, { type = 'audio/wav', blob = new Blob(['RIFF0123WAVEdata'], { type }) } = {}) {
        return { status, ok: status >= 200 && status < 300, headers: { get: (name) => name.toLowerCase() === 'content-type' ? type : null },
            blob: async () => blob, json: async () => ({ error: 'Server audio unavailable' }) };
    }
    state.audioResponse = audioResponse;
    const fetchAudio = async (url, options = {}) => {
        assert.equal(url, '/api/subjects/english/dictation/audio', 'Only the dedicated English speech endpoint should be fetched directly');
        const call = { url, method: options.method || 'GET', headers: options.headers || {}, body: options.body ? JSON.parse(options.body) : null, signal: options.signal, credentials: options.credentials };
        state.audioRequests.push(call);
        return state.audioFetch ? state.audioFetch(call) : audioResponse(state.audioStatus || 200);
    };
    const window = { Audio: FakeAudio, speechSynthesis, SpeechSynthesisUtterance, addEventListener(name, callback) { (state.listeners[name] ||= []).push(callback); } };
    window.englishDictationHost = {
        getSession: () => clone(state.session), stopOtherSpeech() { state.otherSpeechStops = (state.otherSpeechStops || 0) + 1; },
        async apiRequest(url, options = {}) {
            url = url.replace(/^\/api/, '');
            const call = { url, method: options.method || 'GET', body: typeof options.body === 'string' ? JSON.parse(options.body) : clone(options.body), signal: options.signal, username: state.session.username,
                student: state.session.student };
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
    return { state, storage, context, field, root, document, api: window.englishDictation, speechSynthesis, Element,
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
            const item = state.books[0].lessons[number - 1];
            const button = field('english-dictation-lessons').querySelectorAll('button').find((button) => button.dataset.lessonId === item?.id);
            assert.ok(button, `Missing lesson ${number}`); await button.fire('click');
        },
        async openLesson(number = 1) { await window.englishDictation.open(); await this.chooseLesson(number); },
        async start() { await field('english-dictation-start').fire('click'); },
        async finishRun(count) {
            await field('english-dictation-play').fire('click');
            for (let index = 0; index < count; index += 1) {
                await this.finishSpeech();
                await field('english-dictation-next').fire('click');
            }
            assert.equal(field('english-dictation-finish').hidden, false);
        },
        async openMistakes() { await field('english-dictation-mode-mistakes').fire('click'); },
        async chooseMistakeLessons(ids) {
            for (const input of field('english-dictation-mistakes-groups').querySelectorAll('input')) {
                input.checked = ids.includes(input.value); await input.fire('change');
            }
        },
        async markReviewWords(words) {
            const labels = field('english-dictation-review-list').querySelectorAll('label');
            for (const label of labels) {
                const input = label.querySelector('input');
                input.checked = words.some((word) => label.textContent.includes(word)); await input.fire('change');
            }
        },
        async finishAudio() { const audio = state.currentAudio; assert.ok(audio, 'No audio to finish'); state.currentAudio = null; audio.paused = true; audio.emit('ended'); await settle(); await settle(); return audio; },
        async finishItem() { for (let count = 0; count < Number(field('english-dictation-repetitions').value || 2); count += 1) { if (state.currentAudio) await this.finishAudio(); else await this.finishSpeech(); if (count + 1 < Number(field('english-dictation-repetitions').value || 2)) await this.tick(700); } },
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
        visibleText(node = field('english-dictation-panel')) {
            if (node.hidden || node.getAttribute('aria-hidden') === 'true') return '';
            return [node.ownText, node.value, node.getAttribute('aria-label') || '', node.getAttribute('title') || '', ...node.children.map((child) => this.visibleText(child))].join(' ');
        },
    };
}


test('English defaults open a saved list with an authenticated audio-first player', async () => {
    const ui = englishDictationUI();
    await ui.api.open();
    assert.equal(ui.field('english-dictation-words').value, 'apple\nWhere is my schoolbag?');
    await ui.start();
    assert.equal(ui.state.audioRequests.length, 0);
    await ui.space();
    assert.equal(ui.state.audioRequests.length, 1);
    assert.equal(ui.state.audioRequests[0].headers.Authorization, 'Bearer alice-token');
    assert.deepEqual(ui.state.audioRequests[0].body, { text: 'apple' });
    assert.equal(ui.state.audioPlayed.length, 1);
    assert.equal(ui.state.spoken.length, 0);
});

async function finishRound(ui, count) {
    await ui.field('english-dictation-play').fire('click');
    for (let index = 0; index < count; index += 1) {
        await ui.finishItem();
        await ui.field('english-dictation-next').fire('click');
    }
    assert.equal(ui.field('english-dictation-finish').hidden, false);
}

test('one line remains one case-sensitive word, phrase or punctuated sentence and the 500-character boundary is not truncated', async () => {
    const ui = englishDictationUI();
    await ui.api.open();
    const long = "Let's " + 'a'.repeat(494);
    assert.equal(long.length, 500);
    const expected = ['I', 'i', "Let's play, Tom.", 'Where is my schoolbag?', 'look after', 'one | two', 'Tom’s book.', long];
    await ui.type('english-dictation-words', [...expected, "Let's play, Tom."].join('\n'));
    await ui.field('english-dictation-save').fire('click');
    const save = ui.state.calls.find((call) => call.method === 'PUT');
    assert.deepEqual(save.body.words.map((word) => word.text), expected);
    assert.deepEqual(ui.state.wordlists.get('default-list').words.map((word) => word.text), expected);
    const saves = ui.state.calls.filter((call) => call.method === 'PUT').length;
    await ui.type('english-dictation-words', long + 'x');
    await ui.field('english-dictation-save').fire('click');
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, saves);
    assert.equal(ui.field('english-dictation-words').value.length, 501);
    assert.match(ui.field('english-dictation-message').textContent, /500/);
    await ui.type('english-dictation-words', Array.from({ length: 201 }, (_, index) => 'entry ' + index).join('\n'));
    await ui.field('english-dictation-save').fire('click');
    assert.equal(ui.state.calls.filter((call) => call.method === 'PUT').length, saves);
    assert.match(ui.field('english-dictation-message').textContent, /200/);
});

test('linked parents can create lists and prepare child-scoped words without initializing learning or exams', async () => {
    const ui = englishDictationUI({ isParent: true }); ui.state.session.token = 'parent-token';
    await ui.api.open();
    assert.equal(ui.field('english-dictation-words').disabled, false);
    await ui.type('english-dictation-custom-title', 'Unit 2 sentences');
    await ui.type('english-dictation-custom-number', '4');
    await ui.field('english-dictation-add-lesson').fire('click');
    await ui.type('english-dictation-words', "I'm ready.\nLet's go!");
    await ui.field('english-dictation-save').fire('click');
    const creation = ui.state.calls.find((call) => call.method === 'POST' && call.url === prefix + '/lessons');
    assert.deepEqual(creation.body, { book_id: bookId, title: 'Unit 2 sentences', number: 4 });
    const saved = ui.state.calls.find((call) => call.method === 'PUT');
    assert.equal(saved.username, 'alice_parent'); assert.equal(saved.student, 'alice');
    assert.deepEqual(saved.body.words.map((word) => word.text), ["I'm ready.", "Let's go!"]);
    assert.ok(ui.state.calls.every((call) => call.url.startsWith(prefix + '/')));
    await ui.start(); await ui.field('english-dictation-play').fire('click');
    assert.equal(ui.state.audioRequests[0].headers.Authorization, 'Bearer parent-token');
    assert.ok(ui.state.otherSpeechStops > 0);
});

test('first Space reads the current item twice at normal speed, waits while busy and releases its object URL', async () => {
    const ui = englishDictationUI(); await ui.api.open(); await ui.start();
    assert.doesNotMatch(ui.visibleText(), /apple|Where is my schoolbag/);
    await ui.space();
    const audio = ui.state.currentAudio;
    assert.equal(ui.state.audioPlayed.length, 1); assert.equal(ui.state.audioPlayed[0].playbackRate, 1);
    assert.match(ui.field('english-dictation-position').textContent, /1/);
    await ui.space(); await ui.field('english-dictation-next').fire('click');
    assert.equal(ui.state.audioRequests.length, 1);
    await ui.finishAudio(); await ui.tick(699);
    assert.equal(ui.state.audioPlayed.length, 1);
    await ui.space(); await ui.tick(1);
    assert.equal(ui.state.audioPlayed.length, 2); assert.equal(ui.state.currentAudio, audio);
    assert.equal(ui.state.audioRequests.length, 1, 'Repeat the fetched recording rather than issuing the text again');
    await ui.finishAudio(); await ui.tick(1000);
    assert.equal(ui.state.audioPlayed.length, 2, 'Finishing an item must wait for the learner');
    assert.equal(ui.state.objectUrls.size, 0); assert.equal(ui.state.revoked.length, 1);
    await ui.space();
    assert.equal(ui.state.audioRequests.length, 2);
    assert.deepEqual(ui.state.audioRequests[1].body, { text: 'Where is my schoolbag?' });
});

test('native button Space advances once and typing, IME and held or modified shortcuts are not consumed', async () => {
    const ui = englishDictationUI(); await ui.api.open(); await ui.type('english-dictation-repetitions', '1', 'change'); await ui.start();
    await ui.nativeSpace(ui.field('english-dictation-play'));
    assert.equal(ui.state.audioPlayed.length, 1);
    await ui.finishAudio();
    await ui.space(ui.document.body, { repeat: true });
    assert.equal(ui.state.audioPlayed.length, 1);
    await ui.nativeSpace(ui.field('english-dictation-next'));
    assert.equal(ui.state.audioPlayed.length, 2);
    for (const tag of ['input', 'textarea', 'select']) {
        const input = new ui.Element(tag); ui.field('english-dictation-player').appendChild(input);
        assert.equal((await ui.space(input)).defaultPrevented, false, tag);
    }
    const editor = new ui.Element('div', { contenteditable: 'true' }); const child = new ui.Element('span'); editor.appendChild(child); ui.field('english-dictation-player').appendChild(editor);
    assert.equal((await ui.space(child)).defaultPrevented, false);
    for (const extra of [{ isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { altKey: true }, { metaKey: true }, { shiftKey: true }]) {
        assert.equal((await ui.space(ui.document.body, extra)).defaultPrevented, false, JSON.stringify(extra));
    }
    assert.equal(ui.state.audioPlayed.length, 2);
    ui.field('english-dictation-section').classList.remove('active');
    assert.equal((await ui.space()).defaultPrevented, false);
});

test('server audio works without browser voices and only a failed server falls back to an English device voice', async () => {
    const server = englishDictationUI({ voices: [] }); await server.api.open(); await server.start(); await server.field('english-dictation-play').fire('click');
    assert.equal(server.state.audioPlayed.length, 1); assert.equal(server.state.spoken.length, 0);
    const fallback = englishDictationUI({ voices: [
        { name: 'Albert', lang: 'en-US', voiceURI: 'albert', default: true, localService: true },
        { name: 'Samantha', lang: 'en-US', voiceURI: 'samantha', localService: true },
        { name: 'Mandarin default', lang: 'zh-CN', voiceURI: 'zh', default: true },
    ] }); fallback.state.audioStatus = 503;
    await fallback.api.open(); await fallback.start(); await fallback.field('english-dictation-play').fire('click');
    assert.equal(fallback.state.audioRequests.length, 1); assert.equal(fallback.state.audioPlayed.length, 0);
    assert.equal(fallback.state.spoken.length, 1);
    assert.equal(fallback.state.spoken[0].text, 'apple'); assert.equal(fallback.state.spoken[0].rate, 1);
    assert.equal(fallback.state.spoken[0].voice.name, 'Samantha', 'Default fallback should prefer a natural voice over a novelty voice');
    assert.match(fallback.state.spoken[0].voice.lang, /^en(?:-|$)/i);
    await fallback.finishSpeech(); await fallback.tick(700); await fallback.finishSpeech();
    assert.equal(fallback.state.spoken.length, 2);
    const none = englishDictationUI({ voices: [{ name: 'Chinese default', lang: 'zh-CN', default: true }] }); none.state.audioStatus = 503;
    await none.api.open(); await none.start(); await none.field('english-dictation-play').fire('click');
    assert.equal(none.state.spoken.length, 0);
    assert.match(none.field('english-dictation-message').textContent, /English|英语/);
    await none.voicesChanged([{ name: 'British English', lang: 'en-GB', voiceURI: 'gb' }]);
    await none.field('english-dictation-play').fire('click');
    assert.equal(none.state.spoken[0].voice.lang, 'en-GB');
});

test('bad WAV responses fall back, whereas expired authentication cannot play through a device fallback', async () => {
    for (const options of [{ type: 'application/json' }, { blob: new Blob([], { type: 'audio/wav' }) }]) {
        const ui = englishDictationUI(); ui.state.audioFetch = () => ui.state.audioResponse(200, options);
        await ui.api.open(); await ui.start(); await ui.field('english-dictation-play').fire('click');
        assert.equal(ui.state.audioPlayed.length, 0); assert.equal(ui.state.spoken.length, 1);
    }
    const auth = englishDictationUI(); auth.state.audioStatus = 401;
    await auth.api.open(); await auth.start(); await auth.field('english-dictation-play').fire('click');
    assert.equal(auth.state.audioPlayed.length, 0); assert.equal(auth.state.spoken.length, 0);
    assert.match(auth.field('english-dictation-message').textContent, /登录/);
    assert.equal(auth.field('english-dictation-next').disabled, true);
    auth.state.audioStatus = 200; await auth.field('english-dictation-play').fire('click');
    assert.deepEqual(auth.state.audioRequests.at(-1).body, { text: 'apple' });
    assert.match(auth.field('english-dictation-position').textContent, /1/);
});

test('leaving aborts a pending audio request and a late successful response cannot start playback', async () => {
    const ui = englishDictationUI(); const gate = deferred(); ui.state.audioFetch = () => gate.promise;
    await ui.api.open(); await ui.start(); await ui.field('english-dictation-play').fire('click');
    const pending = ui.state.audioRequests[0];
    ui.api.leaveView();
    assert.equal(pending.signal.aborted, true);
    gate.resolve(ui.state.audioResponse()); await ui.flush();
    assert.equal(ui.state.audioPlayed.length, 0); assert.equal(ui.state.spoken.length, 0);
    assert.equal(ui.state.objectUrls.size, 0);
    ui.state.audioFetch = null; await ui.api.open(); await ui.field('english-dictation-resume').fire('click');
    await ui.field('english-dictation-play').fire('click');
    assert.deepEqual(ui.state.audioRequests.at(-1).body, { text: 'apple' });
    const interrupted = ui.state.currentAudio; const endedBeforeOpen = interrupted.onended;
    await ui.api.open();
    assert.equal(ui.field('english-dictation-home').hidden, false);
    assert.equal(ui.field('english-dictation-player').hidden, true);
    assert.ok(interrupted.pauseCount > 0, 'Reopening the module must stop its active recording');
    const beforeResume = ui.state.audioPlayed.length;
    endedBeforeOpen(); await ui.tick(1000);
    assert.equal(ui.state.audioPlayed.length, beforeResume);
    await ui.field('english-dictation-resume').fire('click'); await ui.field('english-dictation-play').fire('click');
    const ended = ui.state.currentAudio.onended; const beforeReset = ui.state.audioPlayed.length;
    ui.api.reset(); ui.state.session = { token: 'bob-token', username: 'bob', student: 'bob', isParent: false, generation: 2 };
    ended(); await ui.tick(1000);
    assert.equal(ui.state.audioPlayed.length, beforeReset);
    assert.equal(ui.state.objectUrls.size, 0);
    assert.equal(ui.field('english-dictation-review-list').children.length, 0);
});

test('late audio blob and rejected play promises cannot revive speech after reset or navigation', async () => {
    const blobGate = deferred(); const ui = englishDictationUI();
    ui.state.audioFetch = () => ({ ...ui.state.audioResponse(), blob: () => blobGate.promise });
    await ui.api.open(); await ui.start(); await ui.field('english-dictation-play').fire('click');
    ui.api.reset(); blobGate.resolve(new Blob(['RIFF-new-WAVE'], { type: 'audio/wav' })); await ui.flush();
    assert.equal(ui.state.audios.length, 0); assert.equal(ui.state.spoken.length, 0);
    const playing = deferred(); const second = englishDictationUI(); second.state.audioPlay = () => playing.promise;
    await second.api.open(); await second.start(); await second.field('english-dictation-play').fire('click');
    const audio = second.state.currentAudio;
    const oldError = audio.onerror;
    second.api.leaveView(); playing.reject(new Error('Late playback failure')); oldError(); await second.flush();
    assert.equal(second.state.spoken.length, 0);
    assert.equal(second.state.objectUrls.size, 0);
    assert.ok(audio.pauseCount > 0);
});

test('only a fully heard item can be manually marked and saved as a mistake', async () => {
    const ui = englishDictationUI(); await ui.api.open(); await ui.start();
    await ui.field('english-dictation-play').fire('click'); await ui.finishItem();
    await ui.field('english-dictation-next').fire('click'); await ui.finishAudio();
    await ui.field('english-dictation-player-exit').fire('click'); await ui.field('english-dictation-end-run').fire('click');
    const labels = ui.field('english-dictation-review-list').querySelectorAll('label');
    assert.equal(labels[0].querySelector('input').disabled, false);
    assert.equal(labels[1].querySelector('input').disabled, true);
    for (const label of labels) { const box = label.querySelector('input'); box.checked = true; await box.fire('change'); }
    await ui.field('english-dictation-save-mistakes').fire('click');
    const saved = ui.state.calls.find((call) => call.method === 'POST' && call.url === prefix + '/mistakes');
    assert.deepEqual(saved.body.items.map((item) => item.words.map((word) => word.text)), [['apple']]);
    assert.deepEqual(ui.state.wordlists.get('default-list').words.map((word) => word.text), ['apple', 'Where is my schoolbag?']);
    assert.equal(ui.state.calls.some((call) => call.body && Object.hasOwn(call.body, 'score')), false);
});

test('multi-list mistake review reads exact duplicates once, saves every source and removes mistakes without editing the original lists', async () => {
    const ui = englishDictationUI({ mistakes: [
        { lesson_id: 'default-list', words: ['apple', "Let's go!"] },
        { lesson_id: 'custom-2', words: ['apple', 'Apple'] },
    ] });
    ui.state.wordlists.set('custom-2', { book_id: bookId, lesson_id: 'custom-2', words: ['apple', 'Apple'].map((text) => ({ text, spoken_text: text })), word_count: 2, revision: 1 });
    await ui.api.open(); await ui.type('english-dictation-repetitions', '1', 'change'); await ui.openMistakes();
    await ui.chooseMistakeLessons(['default-list', 'custom-2']); await ui.field('english-dictation-mistakes-start').fire('click');
    await finishRound(ui, 3);
    assert.deepEqual(ui.state.audioRequests.map((call) => call.body.text), ['apple', "Let's go!", 'Apple']);
    await ui.markReviewWords(['apple', "Let's go!", 'Apple']); await ui.field('english-dictation-save-mistakes').fire('click');
    const saved = ui.state.calls.find((call) => call.method === 'POST' && call.url === prefix + '/mistakes');
    assert.equal(saved.body.items.length, 2);
    assert.deepEqual(saved.body.items.map((item) => [item.lesson_id, item.words.map((word) => word.text)]), [
        ['default-list', ['apple', "Let's go!"]], ['custom-2', ['apple', 'Apple']],
    ]);
    await ui.field('english-dictation-finish-back').fire('click'); await ui.openMistakes();
    const remove = ui.field('english-dictation-mistakes-groups').querySelectorAll('button').find((button) => button.getAttribute('aria-label')?.includes('我的生词清单') && button.getAttribute('aria-label')?.endsWith('移除 apple'));
    assert.ok(remove); await remove.fire('click');
    assert.deepEqual(ui.state.mistakeGroups()[0].words.map((word) => word.text), ["Let's go!"]);
    assert.deepEqual(ui.state.wordlists.get('default-list').words.map((word) => word.text), ['apple', 'Where is my schoolbag?']);
    assert.deepEqual(ui.state.wordlists.get('custom-2').words.map((word) => word.text), ['apple', 'Apple']);
});

test('mistake saving retains checked choices after an unknown delivery and retries without duplicate entries', async () => {
    const ui = englishDictationUI({ words: ['apple'] }); await ui.api.open(); await ui.type('english-dictation-repetitions', '1', 'change'); await ui.start();
    await finishRound(ui, 1); await ui.markReviewWords(['apple']);
    let failed = false;
    ui.state.routes[prefix + '/mistakes'] = (call) => {
        const result = ui.state.defaultReply(call);
        if (!failed) { failed = true; throw new Error('保存回执丢失'); }
        return result;
    };
    await ui.field('english-dictation-save-mistakes').fire('click');
    assert.match(ui.field('english-dictation-save-mistakes').textContent, /重试/);
    assert.equal(ui.field('english-dictation-review-list').querySelector('input').checked, true);
    await ui.field('english-dictation-save-mistakes').fire('click');
    assert.equal(ui.state.mistakeGroups()[0].words.length, 1);
    assert.equal(ui.field('english-dictation-save-mistakes').disabled, true);
});

test('a list revision conflict keeps raw local lines until the explicit two-click cloud choice', async () => {
    const storage = new Map(); const ui = englishDictationUI({ storage }); await ui.api.open();
    const local = "Let's keep this, please.\none | two\nI";
    ui.state.routes[wordPath()] = (call) => {
        if (call.method === 'PUT') throw Object.assign(new Error('新版冲突'), { status: 409 });
        return ui.state.defaultReply(call);
    };
    await ui.type('english-dictation-words', local); await ui.field('english-dictation-save').fire('click');
    assert.equal(ui.field('english-dictation-conflict').hidden, false);
    assert.equal(ui.field('english-dictation-words').value, local);
    await ui.field('english-dictation-copy-local').fire('click'); assert.equal(ui.state.clipboard, local);
    ui.state.wordlists.get('default-list').words = [{ text: 'Cloud sentence.', spoken_text: 'Cloud sentence.' }]; ui.state.wordlists.get('default-list').revision = 3;
    await ui.field('english-dictation-load-cloud').fire('click');
    assert.equal(ui.field('english-dictation-words').value, local);
    await ui.field('english-dictation-keep-cloud').fire('click'); assert.equal(storage.size, 1);
    ui.state.wordlists.get('default-list').words = [{ text: 'Newest cloud sentence.', spoken_text: 'Newest cloud sentence.' }]; ui.state.wordlists.get('default-list').revision = 4;
    await ui.field('english-dictation-confirm-cloud').fire('click');
    assert.equal(ui.field('english-dictation-words').value, 'Newest cloud sentence.'); assert.equal(storage.size, 0);
});

test('account reset aborts pending list loads and cannot expose the previous learner when a late response arrives', async () => {
    const ui = englishDictationUI({ words: ['Alice private phrase.'] }); const gate = deferred(); let original;
    ui.state.routes[wordPath()] = (call) => { original = call; return gate.promise; };
    const opening = ui.api.open(); await ui.flush();
    assert.ok(original);
    const aliceCloud = ui.state.defaultReply(original);
    ui.api.reset(); ui.state.session = { token: 'bob-token', username: 'bob', student: 'bob', isParent: false, generation: 2 };
    delete ui.state.routes[wordPath()]; await ui.api.open();
    gate.resolve(aliceCloud); await opening; await ui.flush();
    assert.equal(original.signal.aborted, true);
    assert.equal(ui.field('english-dictation-words').value, '');
    assert.doesNotMatch(ui.field('english-dictation-panel').textContent, /Alice private phrase/);
    assert.equal(ui.state.audioPlayed.length, 0);

    const leaving = englishDictationUI(); await leaving.api.open();
    const saveGate = deferred(); let saving;
    leaving.state.routes[wordPath()] = (call) => {
        if (call.method === 'PUT') { saving = call; return saveGate.promise; }
        return leaving.state.defaultReply(call);
    };
    await leaving.type('english-dictation-words', 'A new sentence before leaving.');
    const starting = leaving.start(); await leaving.flush();
    assert.ok(saving);
    leaving.api.leaveView();
    assert.equal(saving.signal.aborted, true);
    saveGate.resolve(leaving.state.defaultReply(saving)); await starting; await leaving.flush();
    assert.equal(leaving.field('english-dictation-player').hidden, true, 'A late save may not force the departed learner into the player');
    assert.equal(leaving.state.audioPlayed.length, 0);
});

test('a full 500-character sentence reaches the authenticated speech request unchanged', async () => {
    const text = "Let's " + 'a'.repeat(481) + ', I am ready!';
    assert.equal(text.length, 500);
    const ui = englishDictationUI({ words: [text] }); await ui.api.open(); await ui.start(); await ui.field('english-dictation-play').fire('click');
    assert.equal(ui.state.audioRequests[0].body.text, text);
    assert.equal(ui.state.audioRequests[0].body.text.length, 500);
    assert.equal(ui.state.audioPlayed[0].playbackRate, 1);
});
