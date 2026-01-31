// 全局状态
let token = localStorage.getItem('token');
let username = localStorage.getItem('username');
let currentReviewList = [];
let currentReviewIndex = 0;

// API 基础 URL
const API_BASE = '/api';

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
    messageDiv.textContent = message;
    messageDiv.className = `message ${type}`;
    setTimeout(() => {
        messageDiv.style.display = 'none';
    }, 3000);
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
        throw new Error(error.detail || '请求失败');
    }
    
    return response.json();
}

// ==================== 认证功能 ====================

async function login(username, password) {
    try {
        const formData = new FormData();
        formData.append('username', username);
        formData.append('password', password);
        
        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || '登录失败');
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

function logout() {
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
}

function showSection(sectionId) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.getElementById(sectionId).classList.add('active');
    
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
    document.querySelector(`[data-page="${sectionId}"]`).classList.add('active');
    
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
        console.error('加载统计失败:', error);
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
        console.error('加载复习列表失败:', error);
    }
}

async function showCurrentWord() {
    if (currentReviewIndex >= currentReviewList.length) {
        document.getElementById('review-box').style.display = 'none';
        document.getElementById('review-complete').style.display = 'block';
        return;
    }
    
    const word = currentReviewList[currentReviewIndex];
    
    document.getElementById('current-word-english').textContent = word.english;
    document.getElementById('current-word-chinese').textContent = word.chinese;
    document.getElementById('current-word-progress').textContent = `${word.success_count}/8`;
    document.getElementById('current-word-example').textContent = 'This is an example sentence.';
    
    // 清空输入
    document.getElementById('word-answer').value = '';
    document.getElementById('word-message').style.display = 'none';
    
    // 聚焦输入框
    document.getElementById('word-answer').focus();
}

async function submitAnswer() {
    const answer = document.getElementById('word-answer').value.trim();
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
        
        setTimeout(() => {
            currentReviewIndex++;
            showCurrentWord();
            loadStats();
        }, 1500);
    } catch (error) {
        console.error('提交答案失败:', error);
        showError(error.message);
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
                    <div class="word-item-english">${word.english}</div>
                    <div class="word-item-chinese">${word.chinese}</div>
                </div>
                <div class="word-item-stats">
                    <div class="word-stat">
                        <div class="word-stat-value">${word.success_count}/${word.max_success_count}</div>
                        <div class="word-stat-label">掌握进度</div>
                    </div>
                    <div class="word-stat">
                        <div class="word-stat-value">${word.review_count}</div>
                        <div class="word-stat-label">复习次数</div>
                    </div>
                </div>
            </div>
        `).join('');
        
        document.getElementById('word-list').innerHTML = listHtml || '<p style="padding: 20px; text-align: center; color: #999;">暂无单词</p>';
    } catch (error) {
        console.error('加载进度失败:', error);
    }
}

// ==================== 已掌握功能 ====================

async function loadMastered() {
    try {
        const data = await apiRequest('/words/mastered');
        
        const listHtml = data.words.map(word => `
            <div class="word-item">
                <div class="word-item-info">
                    <div class="word-item-english">${word.english}</div>
                    <div class="word-item-chinese">${word.chinese}</div>
                </div>
                <div class="word-item-stats">
                    <div class="word-stat">
                        <div class="word-stat-value">${word.review_count}</div>
                        <div class="word-stat-label">复习次数</div>
                    </div>
                </div>
            </div>
        `).join('');
        
        document.getElementById('mastered-list').innerHTML = listHtml || '<p style="padding: 20px; text-align: center; color: #999;">暂无已掌握单词</p>';
    } catch (error) {
        console.error('加载已掌握单词失败:', error);
    }
}

// ==================== 导入功能 ====================

document.getElementById('file-input').addEventListener('change', function(e) {
    const fileName = e.target.files[0]?.name;
    if (fileName) {
        document.querySelector('.upload-icon').textContent = '✅';
        document.querySelector('.upload-area p').textContent = fileName;
    }
});

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
            throw new Error(data.detail || '导入失败');
        }
        
        showMessage(data.message, 'success');
        
        // 重置上传区域
        document.querySelector('.upload-icon').textContent = '📄';
        document.querySelector('.upload-area p').textContent = '点击或拖拽文件到此处';
        fileInput.value = '';
        
        // 刷新统计
        loadStats();
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

// ==================== 事件监听 ====================

// 登录表单
document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('login-username').value;
    const password = document.getElementById('login-password').value;
    await login(username, password);
});

// 注册表单
document.getElementById('register-form').addEventListener('submit', async (e) => {
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
document.getElementById('logout-btn').addEventListener('click', logout);

// 提交答案
document.getElementById('submit-answer').addEventListener('click', submitAnswer);
document.getElementById('word-answer').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        submitAnswer();
    }
});

// 导入单词
document.getElementById('import-btn').addEventListener('click', importWords);

// 继续学习
document.getElementById('review-more').addEventListener('click', () => {
    showSection('progress');
});

// ==================== 初始化 ====================

if (token && username) {
    showMainPage();
} else {
    showLoginPage();
}
