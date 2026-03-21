// 全局状态
let token = localStorage.getItem('token');
let username = localStorage.getItem('username');
let currentReviewList = [];
let currentReviewIndex = 0;
let currentErrorCount = 0; // 当前单词错误次数
let currentRevealedCount = 0; // 当前单词已揭示字母数

// API 基础 URL
const API_BASE = '/api';

function escapeHtml(text) {
    if (text == null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

function escapeRegExp(str) {
    return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function showMainBanner(message) {
    const el = document.getElementById('main-error-banner');
    if (!el) return;
    el.textContent = message || '';
    el.style.display = message ? 'block' : 'none';
    if (message) {
        setTimeout(() => {
            el.style.display = 'none';
            el.textContent = '';
        }, 5000);
    }
}

// ==================== 工具函数 ====================

function showError(message) {
    const errorDiv = document.getElementById('auth-error');
    errorDiv.textContent = message;
    errorDiv.style.display = 'block';
    setTimeout(() => {
        errorDiv.style.display = 'none';
    }, 3000);
}

function showMessage(message, type = 'success') {
    const messageDiv = document.getElementById('import-message');
    if (!messageDiv) return;
    messageDiv.textContent = message;
    messageDiv.className = `message ${type}`;
    messageDiv.style.display = '';
    setTimeout(() => {
        messageDiv.className = 'message';
        messageDiv.style.display = 'none';
    }, 3000);
}

// 生成提示字符串
function getHintString(word, revealedCount) {
    const wordText = word.english;
    if (revealedCount >= wordText.length) {
        return wordText;
    }
    // 显示前revealedCount个字母，其余用下划线
    const revealedPart = wordText.substring(0, revealedCount);
    const hiddenPart = '_'.repeat(wordText.length - revealedCount);
    return revealedPart + hiddenPart;
}

// 初始化下划线显示 + 透明输入层（桌面/移动端统一，可唤起软键盘）
function initializeUnderlineInput(word) {
    const container = document.getElementById('underline-input');
    const capture = document.getElementById('mobile-word-capture');
    if (!container || !capture) return;

    container.innerHTML = '';

    const wordLength = word.english.length;
    container.dataset.wordLength = String(wordLength);
    container.dataset.currentInput = '';

    for (let i = 0; i < wordLength; i++) {
        const charSpan = document.createElement('span');
        charSpan.className = 'underline-char empty';
        charSpan.dataset.index = String(i);
        container.appendChild(charSpan);
    }

    capture.value = '';

    const syncFromCapture = () => {
        let v = capture.value.replace(/[^a-zA-Z]/g, '').toLowerCase();
        if (v.length > wordLength) {
            v = v.slice(0, wordLength);
        }
        capture.value = v;
        container.dataset.currentInput = v;
        updateUnderlineDisplay();
    };

    capture.oninput = syncFromCapture;
    capture.onkeydown = (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            submitAnswer();
        }
    };

    setTimeout(() => capture.focus(), 50);
}

// 更新下划线显示
function updateUnderlineDisplay() {
    const container = document.getElementById('underline-input');
    if (!container) return;
    
    const wordLength = parseInt(container.dataset.wordLength);
    const currentInput = container.dataset.currentInput || '';
    
    const charSpans = container.querySelectorAll('.underline-char');
    charSpans.forEach((span, index) => {
        if (index < currentInput.length) {
            // 有字符
            span.textContent = currentInput[index];
            span.className = 'underline-char filled';
        } else {
            // 无字符
            span.textContent = '';
            span.className = 'underline-char empty';
        }
    });
}

// 获取当前输入值
function getCurrentInput() {
    const container = document.getElementById('underline-input');
    if (!container) return '';
    return container.dataset.currentInput || '';
}

// 清空下划线输入
function clearUnderlineInput() {
    const container = document.getElementById('underline-input');
    const capture = document.getElementById('mobile-word-capture');
    if (!container) return;
    container.dataset.currentInput = '';
    if (capture) {
        capture.value = '';
    }
    updateUnderlineDisplay();
}

// 浏览器端朗读（远程访问时服务端 say 只在服务器出声，用户听不到）
function speakEnglishInBrowser(text) {
    const raw = String(text || '').trim().slice(0, 500);
    if (!raw) return false;
    if (typeof window.speechSynthesis === 'undefined') {
        return false;
    }
    const safe = [...raw]
        .filter((c) => {
            const cp = c.codePointAt(0);
            return (cp >= 32 && cp !== 127) || c === '\n' || c === '\t';
        })
        .join('')
        .trim()
        .slice(0, 500);
    if (!safe) return false;

    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(safe);
    u.lang = 'en-US';
    const voices = window.speechSynthesis.getVoices();
    const en = voices.find((v) => v.lang && v.lang.toLowerCase().startsWith('en'));
    if (en) u.voice = en;
    window.speechSynthesis.speak(u);
    return true;
}

// 朗读例句
async function speakExample() {
    const word = currentReviewList[currentReviewIndex];
    if (!word) return;
    
    // 使用原始例句，而不是隐藏后的版本
    const exampleText = word.example;
    if (!exampleText || exampleText === '暂无例句') {
        return;
    }
    
    // 提取英文部分（下划线前）
    let enText = exampleText.split('_')[0];
    if (!enText) {
        enText = exampleText;
    }
    enText = enText.trim();
    if (!enText) return;

    if (speakEnglishInBrowser(enText)) {
        return;
    }

    try {
        await apiRequest('/words/speak', {
            method: 'POST',
            body: JSON.stringify({
                text: enText
            })
        });
    } catch (error) {
        /* 朗读失败时静默 */
    }
}

// API 请求
async function apiRequest(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };
    
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    
    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });
    
    if (!response.ok) {
        const error = await response.json();

        if (response.status === 401) {
            token = null;
            username = null;
            localStorage.removeItem('token');
            localStorage.removeItem('username');
            showLoginPage();
        }
        
        throw new Error(error.error || error.detail || '请求失败');
    }
    
    return response.json();
}

// ==================== 认证功能 ====================

async function login(loginUsername, password) {
    try {
        const formData = new FormData();
        formData.append('username', loginUsername);
        formData.append('password', password);
        
        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || data.detail || '登录失败');
        }
        
        token = data.access_token;
        username = data.username;
        
        localStorage.setItem('token', token);
        localStorage.setItem('username', username);
        
        showMainPage();
    } catch (error) {
        showError(error.message);
    }
}

async function register(username, password, email) {
    try {
        await apiRequest('/auth/register', {
            method: 'POST',
            body: JSON.stringify({ username, password, email })
        });
        
        // 注册成功后自动登录
        await login(username, password);
    } catch (error) {
        showError(error.message);
    }
}

async function logout() {
    try {
        if (token) {
            await fetch(`${API_BASE}/auth/logout`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
        }
    } catch (e) {
        /* 网络错误仍清除本地会话 */
    }
    token = null;
    username = null;
    localStorage.removeItem('token');
    localStorage.removeItem('username');

    showLoginPage();
}

// ==================== 页面切换 ====================

function showLoginPage() {
    document.getElementById('login-page').classList.add('active');
    document.getElementById('main-page').classList.remove('active');
}

function showMainPage() {
    document.getElementById('login-page').classList.remove('active');
    document.getElementById('main-page').classList.add('active');
    document.getElementById('username-display').textContent = username;
    
    loadStats();
    // 默认展示「今日复习」区块；须拉取列表，否则会一直显示 index.html 里的占位词（如 apple）
    showSection('review');
}

function showSection(sectionId) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    const sectionElement = document.getElementById(sectionId + '-section');
    if (sectionElement) {
        sectionElement.classList.add('active');
    }
    
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
    const navItem = document.querySelector(`[data-page="${sectionId}"]`);
    if (navItem) {
        navItem.classList.add('active');
    }
    
    if (sectionId === 'review') {
        loadReviewList();
    } else if (sectionId === 'progress') {
        loadProgress();
    } else if (sectionId === 'mastered') {
        loadMastered();
    }
}

// ==================== 统计功能 ====================

async function loadStats() {
    try {
        const data = await apiRequest('/words/status');
        
        document.getElementById('review-count').textContent = data.words.filter(w => 
            new Date(w.next_review_date) <= new Date()
        ).length;
        
        document.getElementById('mastered-count').textContent = data.stats.mastered_words;
        document.getElementById('round-count').textContent = data.stats.current_round + 1;
    } catch (error) {
        showMainBanner('加载统计失败，请稍后重试');
    }
}

// ==================== 复习功能 ====================

async function loadReviewList() {
    try {
        const data = await apiRequest('/words/review');
        currentReviewList = data.words;
        currentReviewIndex = 0;
        
        if (currentReviewList.length === 0) {
            document.getElementById('review-box').style.display = 'none';
            document.getElementById('review-complete').style.display = 'block';
        } else {
            document.getElementById('review-box').style.display = 'block';
            document.getElementById('review-complete').style.display = 'none';
            showCurrentWord();
        }
    } catch (error) {
        showMainBanner('加载复习列表失败，请稍后重试');
    }
}

async function showCurrentWord() {
    if (currentReviewIndex >= currentReviewList.length) {
        document.getElementById('review-box').style.display = 'none';
        document.getElementById('review-complete').style.display = 'block';
        return;
    }
    
    const word = currentReviewList[currentReviewIndex];
    
    // 重置错误计数和揭示字母数
    currentErrorCount = 0;
    currentRevealedCount = 0;
    
    // 显示中文意思
    document.getElementById('current-word-chinese').textContent = word.chinese;
    const maxSucc = word.max_success_count != null ? word.max_success_count : 8;
    document.getElementById('current-word-progress').textContent = `${word.success_count}/${maxSucc}`;
    
    // 处理例句：隐藏目标单词
    let exampleText = word.example || '暂无例句';
    if (exampleText !== '暂无例句') {
        // 处理双语例句格式：英文_中文
        const parts = exampleText.split('_');
        if (parts.length >= 2) {
            // 有下划线分隔，英文部分是parts[0]
            let englishPart = parts[0];
            // 替换目标单词为下划线（忽略大小写）
            const regex = new RegExp(`\\b${escapeRegExp(word.english)}\\b`, 'gi');
            englishPart = englishPart.replace(regex, '_'.repeat(word.english.length));
            // 重新组合
            exampleText = englishPart + '_' + parts.slice(1).join('_');
        } else {
            // 单语例句，直接替换
            const regex = new RegExp(`\\b${escapeRegExp(word.english)}\\b`, 'gi');
            exampleText = exampleText.replace(regex, '_'.repeat(word.english.length));
        }
    }
    
    document.getElementById('current-word-example').textContent = exampleText;
    
    // 英文单词显示为提示字符串
    const hintString = getHintString(word, currentRevealedCount);
    document.getElementById('current-word-english').textContent = hintString;
    
    // 绑定朗读按钮事件
    const speakBtn = document.getElementById('speak-example-btn');
    if (speakBtn) {
        speakBtn.onclick = speakExample;
    }
    
    // 初始化下划线输入框
    initializeUnderlineInput(word);
    
    // 清空消息
    document.getElementById('word-message').style.display = 'none';
}

async function submitAnswer() {
    const answer = getCurrentInput();
    const word = currentReviewList[currentReviewIndex];
    
    if (!answer) {
        return;
    }
    
    try {
        const result = await apiRequest('/words/practice', {
            method: 'POST',
            body: JSON.stringify({
                word_id: word.english,
                answer: answer
            })
        });
        
        const messageDiv = document.getElementById('word-message');
        messageDiv.textContent = result.message;
        messageDiv.className = `word-message ${result.correct ? 'success' : 'error'}`;
        messageDiv.style.display = 'block';
        
        if (result.correct) {
            // 答案正确，显示完整单词，然后进入下一个单词
            document.getElementById('current-word-english').textContent = word.english;
            setTimeout(() => {
                currentReviewIndex++;
                showCurrentWord();
                loadStats();
            }, 1500);
        } else {
            // 答案错误
            currentErrorCount++;
            
            // 每次错误多揭示一个字母
            if (currentRevealedCount < word.english.length) {
                currentRevealedCount++;
            }
            
            if (currentErrorCount >= 3) {
                // 错误次数达到3次，显示完整单词，然后进入下一个单词
                document.getElementById('current-word-english').textContent = word.english;
                setTimeout(() => {
                    currentReviewIndex++;
                    showCurrentWord();
                    loadStats();
                }, 1500);
            } else {
                // 还有尝试机会，更新提示字符串
                const hintString = getHintString(word, currentRevealedCount);
                document.getElementById('current-word-english').textContent = hintString;
                
                // 显示剩余次数
                messageDiv.textContent = `${result.message} (还剩 ${3 - currentErrorCount} 次尝试机会)`;
                // 清空下划线输入框，让用户重新输入
                clearUnderlineInput();
                // 聚焦下划线输入框
                const underlineInput = document.getElementById('underline-input');
                if (underlineInput) {
                    underlineInput.focus();
                }
            }
        }
    } catch (error) {
        const msg = error.message || '提交失败，请重试';
        const reviewSection = document.getElementById('review-section');
        const messageDiv = document.getElementById('word-message');
        if (reviewSection && reviewSection.classList.contains('active') && messageDiv) {
            messageDiv.textContent = msg;
            messageDiv.className = 'word-message error';
            messageDiv.style.display = 'block';
        } else {
            showError(msg);
        }
    }
}

// ==================== 进度功能 ====================

async function loadProgress() {
    try {
        const data = await apiRequest('/words/status');
        
        // 显示统计
        const statsHtml = `
            <div class="stat-card">
                <div class="stat-icon">📝</div>
                <div class="stat-content">
                    <div class="stat-label">总单词数</div>
                    <div class="stat-value">${data.stats.total_words}</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">✅</div>
                <div class="stat-content">
                    <div class="stat-label">已掌握</div>
                    <div class="stat-value">${data.stats.mastered_words}</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📊</div>
                <div class="stat-content">
                    <div class="stat-label">平均复习次数</div>
                    <div class="stat-value">${data.stats.avg_review_count.toFixed(1)}</div>
                </div>
            </div>
        `;
        document.getElementById('progress-stats').innerHTML = statsHtml;
        
        // 显示单词列表
        const listHtml = data.words.map(word => `
            <div class="word-item">
                <div class="word-item-info">
                    <div class="word-item-english">${escapeHtml(word.english)}</div>
                    <div class="word-item-chinese">${escapeHtml(word.chinese)}</div>
                </div>
                <div class="word-item-stats">
                    <div class="word-stat">
                        <div class="word-stat-value">${escapeHtml(word.success_count)}/${escapeHtml(word.max_success_count)}</div>
                        <div class="word-stat-label">掌握进度</div>
                    </div>
                    <div class="word-stat">
                        <div class="word-stat-value">${escapeHtml(word.review_count)}</div>
                        <div class="word-stat-label">复习次数</div>
                    </div>
                </div>
            </div>
        `).join('');
        
        document.getElementById('word-list').innerHTML = listHtml || '<p style="padding: 20px; text-align: center; color: #999;">暂无单词</p>';
    } catch (error) {
        showMainBanner('加载进度失败，请稍后重试');
    }
}

// ==================== 已掌握功能 ====================

async function loadMastered() {
    try {
        const data = await apiRequest('/words/mastered');
        
        const listHtml = data.words.map(word => `
            <div class="word-item">
                <div class="word-item-info">
                    <div class="word-item-english">${escapeHtml(word.english)}</div>
                    <div class="word-item-chinese">${escapeHtml(word.chinese)}</div>
                </div>
                <div class="word-item-stats">
                    <div class="word-stat">
                        <div class="word-stat-value">${escapeHtml(word.review_count)}</div>
                        <div class="word-stat-label">复习次数</div>
                    </div>
                </div>
            </div>
        `).join('');
        
        document.getElementById('mastered-list').innerHTML = listHtml || '<p style="padding: 20px; text-align: center; color: #999;">暂无已掌握单词</p>';
    } catch (error) {
        showMainBanner('加载已掌握列表失败，请稍后重试');
    }
}

// ==================== 导入功能 ====================

async function importWords() {
    const fileInput = document.getElementById('file-input');
    const file = fileInput.files[0];
    
    if (!file) {
        showMessage('请先选择文件', 'error');
        return;
    }
    
    try {
        const formData = new FormData();
        formData.append('file', file);
        
        const response = await fetch(`${API_BASE}/words/import`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            },
            body: formData
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || data.detail || '导入失败');
        }
        
        showMessage(data.message, 'success');
        
        const uploadArea = document.querySelector('.upload-area');
        const icon = uploadArea && uploadArea.querySelector('.upload-icon');
        const firstP = uploadArea && uploadArea.querySelector('p');
        if (icon) icon.textContent = '📄';
        if (firstP) firstP.textContent = '点击或拖拽文件到此处';
        fileInput.value = '';
        
        // 刷新统计
        loadStats();
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

// ==================== 事件监听与初始化 ====================

document.addEventListener('DOMContentLoaded', function() {
    // 登录表单
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('login-username').value;
            const password = document.getElementById('login-password').value;
            await login(username, password);
        });
    }
    
    // 注册表单
    const registerForm = document.getElementById('register-form');
    if (registerForm) {
        registerForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('reg-username').value;
            const password = document.getElementById('reg-password').value;
            const passwordConfirm = document.getElementById('reg-password-confirm').value;
            const email = document.getElementById('reg-email').value || null;
            
            if (password !== passwordConfirm) {
                showError('两次密码输入不一致');
                return;
            }
            
            await register(username, password, email);
        });
    }
    
    // Tab 切换
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            const tabName = tab.dataset.tab;
            document.getElementById('login-form').style.display = tabName === 'login' ? 'block' : 'none';
            document.getElementById('register-form').style.display = tabName === 'register' ? 'block' : 'none';
        });
    });
    
    // 导航切换
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            showSection(page);
        });
    });
    
    // 退出登录
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => { logout(); });
    }
    
    // 提交答案
    const submitBtn = document.getElementById('submit-answer');
    if (submitBtn) {
        submitBtn.addEventListener('click', submitAnswer);
    }
    
    // 下划线输入框的Enter键已经在initializeUnderlineInput中处理
    
    // 导入单词
    const importBtn = document.getElementById('import-btn');
    if (importBtn) {
        importBtn.addEventListener('click', importWords);
    }
    
    // 继续学习
    const reviewMoreBtn = document.getElementById('review-more');
    if (reviewMoreBtn) {
        reviewMoreBtn.addEventListener('click', () => {
            showSection('progress');
        });
    }

    // 上传区域：点击与拖拽
    const uploadArea = document.querySelector('.upload-area');
    const fileInput = document.getElementById('file-input');
    if (uploadArea && fileInput) {
        fileInput.addEventListener('change', function (e) {
            const fileName = e.target.files[0]?.name;
            if (fileName) {
                const icon = uploadArea.querySelector('.upload-icon');
                const firstP = uploadArea.querySelector('p');
                if (icon) icon.textContent = '✅';
                if (firstP) firstP.textContent = fileName;
            }
        });

        uploadArea.addEventListener('click', () => fileInput.click());
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((ev) => {
            uploadArea.addEventListener(ev, (e) => {
                e.preventDefault();
                e.stopPropagation();
            });
        });
        uploadArea.addEventListener('dragover', () => {
            uploadArea.style.borderColor = 'var(--primary-color)';
        });
        uploadArea.addEventListener('dragleave', () => {
            uploadArea.style.borderColor = '';
        });
        uploadArea.addEventListener('drop', (e) => {
            uploadArea.style.borderColor = '';
            const files = e.dataTransfer && e.dataTransfer.files;
            if (files && files.length > 0) {
                try {
                    const dt = new DataTransfer();
                    dt.items.add(files[0]);
                    fileInput.files = dt.files;
                } catch (err) {
                    /* 部分环境不可写 files，忽略 */
                }
                fileInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
    }
    
    // 初始化页面
    if (token && username) {
        showMainPage();
    } else {
        showLoginPage();
    }
});
