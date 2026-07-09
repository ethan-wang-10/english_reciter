(function () {
    "use strict";

    const SESSION_KEY = "perf_session_id";
    const DEFAULT_CONFIG = {
        enabled: true,
        sample_rate: 1,
        slow_api_ms: 1000,
        max_events: 60,
    };
    const state = {
        config: DEFAULT_CONFIG,
        enabled: true,
        sampled: true,
        queue: [],
        flushTimer: null,
        startedAt: Date.now(),
        sessionId: getSessionId(),
        pageId: randomId(),
        currentSection: "",
    };

    const originalFetch = window.fetch ? window.fetch.bind(window) : null;
    function randomId() {
        try {
            const bytes = new Uint8Array(12);
            window.crypto.getRandomValues(bytes);
            return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
        } catch (_) {
            return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
        }
    }

    function getSessionId() {
        try {
            let sid = sessionStorage.getItem(SESSION_KEY);
            if (!sid) {
                sid = randomId();
                sessionStorage.setItem(SESSION_KEY, sid);
            }
            return sid;
        } catch (_) {
            return randomId();
        }
    }

    function nowMs() {
        return Math.round(performance.now() * 10) / 10;
    }

    function pagePath() {
        return `${location.pathname || "/"}${location.hash || ""}`.slice(0, 240);
    }

    function normalizeUrl(input) {
        try {
            const raw = typeof input === "string" ? input : input && input.url ? input.url : "";
            const u = new URL(raw, location.href);
            return {
                origin: u.origin === location.origin ? "same-origin" : "cross-origin",
                path: `${u.pathname}${u.search ? scrubQuery(u.search) : ""}`.slice(0, 280),
            };
        } catch (_) {
            return { origin: "unknown", path: String(input || "").slice(0, 280) };
        }
    }

    function scrubQuery(search) {
        if (!search || search === "?") return "";
        const params = new URLSearchParams(search);
        const kept = [];
        params.forEach((value, key) => {
            const k = String(key || "").toLowerCase();
            if (/(token|password|secret|code|invite|session|auth)/.test(k)) {
                kept.push(`${encodeURIComponent(key)}=[redacted]`);
            } else {
                const v = String(value || "");
                kept.push(`${encodeURIComponent(key)}=${encodeURIComponent(v.slice(0, 80))}`);
            }
        });
        return kept.length ? `?${kept.join("&")}` : "";
    }

    function baseEvent(type) {
        return {
            type,
            page_id: state.pageId,
            session_id: state.sessionId,
            page: pagePath(),
            section: state.currentSection,
            at_ms: nowMs(),
            visibility: document.visibilityState,
            viewport: {
                width: window.innerWidth || 0,
                height: window.innerHeight || 0,
                dpr: window.devicePixelRatio || 1,
            },
            connection: getConnectionInfo(),
        };
    }

    function getConnectionInfo() {
        const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
        if (!c) return undefined;
        return {
            effective_type: c.effectiveType || "",
            downlink: typeof c.downlink === "number" ? c.downlink : undefined,
            rtt: typeof c.rtt === "number" ? c.rtt : undefined,
            save_data: !!c.saveData,
        };
    }

    function enqueue(type, payload) {
        if (!state.enabled || !state.sampled) return;
        const ev = Object.assign(baseEvent(type), payload || {});
        state.queue.push(ev);
        const max = Math.max(1, Number(state.config.max_events) || DEFAULT_CONFIG.max_events);
        if (state.queue.length >= max) {
            flush("batch-full");
        } else {
            scheduleFlush();
        }
    }

    function scheduleFlush() {
        if (state.flushTimer) return;
        state.flushTimer = window.setTimeout(() => flush("timer"), 5000);
    }

    function authHeaders() {
        const headers = { "Content-Type": "application/json" };
        try {
            const token = localStorage.getItem("token");
            if (token) headers.Authorization = `Bearer ${token}`;
        } catch (_) {
            /* ignore */
        }
        return headers;
    }

    function flush(reason) {
        if (state.flushTimer) {
            window.clearTimeout(state.flushTimer);
            state.flushTimer = null;
        }
        if (!state.queue.length || !state.enabled || !state.sampled) return;
        const events = state.queue.splice(0, state.config.max_events || DEFAULT_CONFIG.max_events);
        const payload = JSON.stringify({
            reason,
            sent_at: new Date().toISOString(),
            events,
        });
        if (navigator.sendBeacon) {
            try {
                const ok = navigator.sendBeacon(
                    "/api/performance/report",
                    new Blob([payload], { type: "application/json" }),
                );
                if (ok) return;
            } catch (_) {
                /* fall through */
            }
        }
        if (!originalFetch) return;
        originalFetch("/api/performance/report", {
            method: "POST",
            headers: authHeaders(),
            body: payload,
            keepalive: true,
            credentials: "same-origin",
        }).catch(() => {});
    }

    function installFetchObserver() {
        if (!originalFetch) return;
        window.fetch = async function observedFetch(input, init) {
            const started = performance.now();
            const url = normalizeUrl(input);
            const method =
                (init && init.method) ||
                (input && typeof input === "object" && input.method) ||
                "GET";
            let requestId = "";
            if (url.origin === "same-origin") {
                try {
                    const headers = new Headers((init && init.headers) || (input && input.headers) || undefined);
                    requestId = headers.get("X-Request-ID") || randomId();
                    headers.set("X-Request-ID", requestId);
                    init = Object.assign({}, init || {}, { headers });
                } catch (_) {
                    requestId = randomId();
                }
            }
            try {
                const response = await originalFetch(input, init);
                const duration = performance.now() - started;
                const slow = duration >= (Number(state.config.slow_api_ms) || DEFAULT_CONFIG.slow_api_ms);
                if (slow || response.status >= 400) {
                    enqueue("api", {
                        request_id: requestId,
                        method: String(method).toUpperCase(),
                        url,
                        status: response.status,
                        ok: response.ok,
                        duration_ms: Math.round(duration * 10) / 10,
                        transfer_size: response.headers.get("Content-Length") || undefined,
                    });
                }
                return response;
            } catch (error) {
                enqueue("api_error", {
                    request_id: requestId,
                    method: String(method).toUpperCase(),
                    url,
                    duration_ms: Math.round((performance.now() - started) * 10) / 10,
                    error_class: error && error.name ? error.name : "Error",
                    error: error && error.message ? String(error.message).slice(0, 300) : String(error).slice(0, 300),
                });
                throw error;
            }
        };
    }

    function installErrorObservers() {
        window.addEventListener("error", (event) => {
            const target = event.target;
            if (target && target !== window) {
                enqueue("resource_error", {
                    tag: target.tagName || "",
                    url: normalizeUrl(target.src || target.href || "").path,
                });
                return;
            }
            enqueue("js_error", {
                message: String(event.message || "").slice(0, 500),
                filename: normalizeUrl(event.filename || "").path,
                line: event.lineno || 0,
                column: event.colno || 0,
                error_class: event.error && event.error.name ? event.error.name : "",
            });
        }, true);

        window.addEventListener("unhandledrejection", (event) => {
            const reason = event.reason;
            enqueue("promise_rejection", {
                error_class: reason && reason.name ? reason.name : "",
                message: reason && reason.message ? String(reason.message).slice(0, 500) : String(reason || "").slice(0, 500),
            });
        });
    }

    function installPerformanceObservers() {
        if (!window.PerformanceObserver) return;
        try {
            const po = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    enqueue("long_task", {
                        name: entry.name || "self",
                        duration_ms: Math.round(entry.duration * 10) / 10,
                        start_ms: Math.round(entry.startTime * 10) / 10,
                    });
                }
            });
            po.observe({ type: "longtask", buffered: true });
        } catch (_) {
            /* unsupported */
        }

        try {
            const po = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    enqueue("layout_shift", {
                        value: Math.round((entry.value || 0) * 10000) / 10000,
                        had_recent_input: !!entry.hadRecentInput,
                        start_ms: Math.round(entry.startTime * 10) / 10,
                    });
                }
            });
            po.observe({ type: "layout-shift", buffered: true });
        } catch (_) {
            /* unsupported */
        }

        try {
            const po = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    enqueue("largest_contentful_paint", {
                        start_ms: Math.round(entry.startTime * 10) / 10,
                        size: entry.size || 0,
                        element: entry.element && entry.element.tagName ? entry.element.tagName : "",
                    });
                }
            });
            po.observe({ type: "largest-contentful-paint", buffered: true });
        } catch (_) {
            /* unsupported */
        }
    }

    function collectNavigationTiming() {
        window.addEventListener("load", () => {
            window.setTimeout(() => {
                const nav = performance.getEntriesByType("navigation")[0];
                if (!nav) {
                    enqueue("page_load", {
                        load_ms: Math.round(performance.now() * 10) / 10,
                    });
                    return;
                }
                enqueue("page_load", {
                    nav_type: nav.type,
                    dns_ms: roundDelta(nav.domainLookupStart, nav.domainLookupEnd),
                    connect_ms: roundDelta(nav.connectStart, nav.connectEnd),
                    ttfb_ms: roundDelta(nav.requestStart, nav.responseStart),
                    response_ms: roundDelta(nav.responseStart, nav.responseEnd),
                    dom_content_loaded_ms: roundDelta(nav.startTime, nav.domContentLoadedEventEnd),
                    load_ms: roundDelta(nav.startTime, nav.loadEventEnd),
                    transfer_size: nav.transferSize || 0,
                    encoded_body_size: nav.encodedBodySize || 0,
                    decoded_body_size: nav.decodedBodySize || 0,
                });
                collectSlowResources();
            }, 0);
        }, { once: true });
    }

    function collectSlowResources() {
        const entries = performance.getEntriesByType("resource") || [];
        const threshold = Math.max(1200, Number(state.config.slow_api_ms) || DEFAULT_CONFIG.slow_api_ms);
        entries
            .filter((entry) => entry.duration >= threshold)
            .slice(-30)
            .forEach((entry) => {
                enqueue("resource", {
                    url: normalizeUrl(entry.name).path,
                    initiator_type: entry.initiatorType || "",
                    duration_ms: Math.round(entry.duration * 10) / 10,
                    transfer_size: entry.transferSize || 0,
                    encoded_body_size: entry.encodedBodySize || 0,
                    decoded_body_size: entry.decodedBodySize || 0,
                });
            });
    }

    function roundDelta(start, end) {
        if (typeof start !== "number" || typeof end !== "number" || end < start) return 0;
        return Math.round((end - start) * 10) / 10;
    }

    function installSectionObserver() {
        try {
            Object.defineProperty(window, "__perfCurrentSection", {
                get() {
                    return state.currentSection;
                },
                set(v) {
                    state.currentSection = String(v || "").slice(0, 80);
                },
                configurable: true,
            });
        } catch (_) {
            /* ignore */
        }

        const install = () => {
            if (typeof window.showSection !== "function" || window.showSection.__perfWrapped) return false;
            const original = window.showSection;
            const wrapped = function perfShowSection(sectionId) {
                const started = performance.now();
                const from = state.currentSection;
                state.currentSection = String(sectionId || "").slice(0, 80);
                try {
                    return original.apply(this, arguments);
                } finally {
                    requestAnimationFrame(() => {
                        enqueue("section_switch", {
                            from,
                            to: state.currentSection,
                            duration_ms: Math.round((performance.now() - started) * 10) / 10,
                        });
                    });
                }
            };
            wrapped.__perfWrapped = true;
            window.showSection = wrapped;
            return true;
        };
        if (!install()) {
            document.addEventListener("DOMContentLoaded", () => {
                install();
                const active = document.querySelector(".section.active");
                if (active && active.id) {
                    state.currentSection = active.id.replace(/-section$/, "");
                }
            });
        }
    }

    async function loadConfig() {
        if (!originalFetch) return;
        try {
            const res = await originalFetch("/api/performance/config", {
                cache: "no-store",
                credentials: "same-origin",
            });
            if (!res.ok) return;
            const cfg = await res.json();
            state.config = Object.assign({}, DEFAULT_CONFIG, cfg || {});
            state.enabled = state.config.enabled !== false;
            const rate = Math.max(0, Math.min(1, Number(state.config.sample_rate)));
            state.sampled = rate >= 1 || Math.random() < rate;
        } catch (_) {
            /* keep defaults */
        }
    }

    function exposeDebugApi() {
        window.__perf = {
            flush,
            enqueue,
            state,
        };
    }

    function init() {
        installFetchObserver();
        installErrorObservers();
        installPerformanceObservers();
        installSectionObserver();
        collectNavigationTiming();
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "hidden") {
                flush("hidden");
            }
        });
        window.addEventListener("pagehide", () => flush("pagehide"));
        exposeDebugApi();
        void loadConfig().then(() => {
            if (!state.enabled || !state.sampled) {
                state.queue = [];
            }
        });
    }

    init();
})();
