const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/js/app.js'), 'utf8');

function desktopNavigation() {
    let document;
    class Element {
        hidden = false;
        style = { display: '', visibility: 'visible' };
        dataset = {};
        attributes = {};
        listeners = new Map();
        children = [];
        parentElement = null;
        classes = new Set();
        classList = {
            add: (...names) => names.forEach((name) => this.classes.add(name)),
            remove: (...names) => names.forEach((name) => this.classes.delete(name)),
            contains: (name) => this.classes.has(name),
            toggle: (name, force) => {
                const on = force === undefined ? !this.classes.has(name) : force;
                if (on) this.classes.add(name);
                else this.classes.delete(name);
                return on;
            },
        };
        append(...children) {
            children.forEach((child) => { child.parentElement = this; });
            this.children.push(...children);
        }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return this.attributes[name] ?? null; }
        removeAttribute(name) { delete this.attributes[name]; }
        addEventListener(name, handler) {
            if (!this.listeners.has(name)) this.listeners.set(name, []);
            this.listeners.get(name).push(handler);
        }
        contains(target) {
            return this === target || this.children.some((child) => child.contains(target));
        }
        querySelectorAll(selector) {
            const descendants = this.children.flatMap((child) => [child, ...child.querySelectorAll('*')]);
            if (selector === '*') return descendants;
            if (selector.includes('desktop-more-link')) return descendants.filter((child) => child.classes.has('desktop-more-link'));
            if (selector.includes('data-page')) return descendants.filter((child) => child.dataset.page);
            return descendants;
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        closest(selector) {
            if (selector === '.desktop-more-link' && this.classes.has('desktop-more-link')) return this;
            return this.parentElement?.closest(selector) || null;
        }
        getClientRects() {
            if (this.hidden || this.style.display === 'none') return [];
            return [{ width: 100, height: 40 }];
        }
        focus() { document.activeElement = this; }
        fire(type, options = {}) {
            const event = {
                type, target: this, defaultPrevented: false, propagationStopped: false,
                preventDefault() { this.defaultPrevented = true; },
                stopPropagation() { this.propagationStopped = true; },
                ...options,
            };
            let target = this;
            while (target) {
                for (const handler of target.listeners.get(type) || []) handler(event);
                if (event.propagationStopped) break;
                target = target.parentElement || (target !== document ? document : null);
            }
            return event;
        }
    }
    const wrap = new Element();
    const button = new Element();
    const menu = new Element();
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    const links = ['discover', 'textbook', 'progress', 'mastered'].map((page) => {
        const link = new Element();
        link.dataset.page = page;
        link.classes.add('nav-item');
        link.classes.add('desktop-more-link');
        return link;
    });
    menu.append(...links);
    wrap.append(button, menu);
    const outside = new Element();
    const elements = new Map([
        ['desktop-more-wrap', wrap], ['desktop-more-btn', button], ['desktop-more-menu', menu],
    ]);
    document = new Element();
    document.activeElement = null;
    document.getElementById = (id) => elements.get(id) || null;
    document.querySelectorAll = (selector) => wrap.querySelectorAll(selector);
    document.querySelector = (selector) => document.querySelectorAll(selector)[0] || null;
    const window = new Element();
    window.location = { search: '' };
    window.matchMedia = () => ({ matches: false, addEventListener() {} });
    const context = vm.createContext({
        Element, URLSearchParams, window, document,
        localStorage: { getItem: () => null },
        getComputedStyle: (element) => element.style,
    });
    vm.runInContext(source, context);
    context.initDesktopMoreNavigation();
    return { context, document, button, menu, links, outside };
}

function assertOpen(ui, open) {
    assert.equal(ui.button.getAttribute('aria-expanded'), String(open));
    assert.equal(ui.menu.hidden, !open);
}

test('desktop More opens on click and closes on a second click', () => {
    const ui = desktopNavigation();
    assertOpen(ui, false);
    ui.button.fire('click');
    assertOpen(ui, true);
    ui.button.fire('click');
    assertOpen(ui, false);
});

test('clicking outside closes desktop More while clicking within the panel keeps it open', () => {
    const ui = desktopNavigation();
    ui.button.fire('click');
    ui.menu.fire('click');
    assertOpen(ui, true);
    ui.outside.fire('click');
    assertOpen(ui, false);
});

test('Escape closes desktop More and returns focus to its trigger', () => {
    const ui = desktopNavigation();
    ui.button.fire('click');
    ui.links[2].focus();
    ui.links[2].fire('keydown', { key: 'Escape' });
    assertOpen(ui, false);
    assert.equal(ui.document.activeElement, ui.button);
});

test('ArrowDown opens desktop More and focuses the first visible destination', () => {
    const ui = desktopNavigation();
    ui.links[0].style.display = 'none';
    ui.links[1].style.display = 'none';
    const event = ui.button.fire('keydown', { key: 'ArrowDown' });
    assert.equal(event.defaultPrevented, true);
    assertOpen(ui, true);
    assert.equal(ui.document.activeElement, ui.links[2]);
});

test('menu arrow keys cycle through visible destinations and skip hidden parent-mode items', () => {
    const ui = desktopNavigation();
    ui.links[0].style.display = 'none';
    ui.links[1].style.display = 'none';
    ui.button.fire('keydown', { key: 'ArrowDown' });
    ui.links[2].fire('keydown', { key: 'ArrowDown' });
    assert.equal(ui.document.activeElement, ui.links[3]);
    ui.links[3].fire('keydown', { key: 'ArrowDown' });
    assert.equal(ui.document.activeElement, ui.links[2]);
    ui.links[2].fire('keydown', { key: 'ArrowUp' });
    assert.equal(ui.document.activeElement, ui.links[3]);
});

test('desktop More stays highlighted for its four destinations only', () => {
    const ui = desktopNavigation();
    for (const page of ['discover', 'textbook', 'progress', 'mastered']) {
        ui.context.syncDesktopMoreActive(page);
        assert.equal(ui.button.classList.contains('active'), true, page);
    }
    for (const page of ['review', 'exam-preparation', 'import', 'leaderboard']) {
        ui.context.syncDesktopMoreActive(page);
        assert.equal(ui.button.classList.contains('active'), false, page);
    }
});
