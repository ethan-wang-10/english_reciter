/**
 * PM2 守护 Gunicorn（与 Dockerfile 中命令一致）
 *
 * 使用前：
 *   python3 -m venv .venv
 *   .venv/bin/pip install -r requirements-simple.txt
 *   cp -n config.example.json config.json   # 按需编辑
 *
 * 启动（任选其一）：
 *   A) 项目根目录复制 .env.example 为 .env，填写 SECRET_KEY=（推荐）
 *   B) export SECRET_KEY="$(openssl rand -hex 32)"
 *   然后：pm2 start ecosystem.config.cjs
 *   pm2 save && pm2 startup
 *
 * Piper 神经朗读（可选）：
 *   在服务器安装 piper 可执行文件与 .onnx 模型后，启动前 export：
 *     export PIPER_MODEL=/绝对路径/xxx.onnx
 *     export PIPER_BINARY=/绝对路径/piper   # 若 piper 不在 PATH 中
 *   或在本文件 env 中填写 PIPER_MODEL / PIPER_BINARY 常量（见下方）。
 */
const fs = require('fs');
const path = require('path');
const root = __dirname;

/** 加载项目根目录 .env（不覆盖已在 shell 中设置的变量） */
(function loadProjectEnv() {
  const p = path.join(root, '.env');
  if (!fs.existsSync(p)) return;
  const text = fs.readFileSync(p, 'utf8');
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (!key) continue;
    if (process.env[key] === undefined || process.env[key] === '') {
      process.env[key] = val;
    }
  }
})();

const venvGunicorn = path.join(root, '.venv', 'bin', 'gunicorn');
const host = process.env.HOST || '0.0.0.0';
const port = process.env.PORT || '8000';
const workers = process.env.WEB_CONCURRENCY || '1';
const threads = process.env.GUNICORN_THREADS || '4';

module.exports = {
  apps: [
    {
      name: 'english-reciter',
      cwd: root,
      script: venvGunicorn,
      args:
        `-c gunicorn_config.py --bind ${host}:${port} --workers ${workers} --threads ${threads} simple_web_app:app`,
      instances: 1,
      autorestart: true,
      max_restarts: 15,
      min_uptime: '10s',
      env: {
        PYTHONUNBUFFERED: '1',
        FLASK_ENV: 'production',
        TZ: 'Asia/Shanghai',
        HOST: host,
        PORT: port,
        WEB_CONCURRENCY: workers,
        GUNICORN_THREADS: threads,
        // 启动前在 shell 中 export SECRET_KEY=...，PM2 会继承 process.env.SECRET_KEY
        SECRET_KEY: process.env.SECRET_KEY || '',
        PUBLIC_BASE_URL: process.env.PUBLIC_BASE_URL || '',
        SMTP_HOST: process.env.SMTP_HOST || '',
        SMTP_PORT: process.env.SMTP_PORT || '',
        SMTP_USERNAME: process.env.SMTP_USERNAME || '',
        SMTP_PASSWORD: process.env.SMTP_PASSWORD || '',
        SMTP_FROM_EMAIL: process.env.SMTP_FROM_EMAIL || '',
        SMTP_FROM_NAME: process.env.SMTP_FROM_NAME || '智能英语背诵',
        SMTP_USE_SSL: process.env.SMTP_USE_SSL || '',
        SMTP_STARTTLS: process.env.SMTP_STARTTLS || '',
        SMTP_TIMEOUT: process.env.SMTP_TIMEOUT || '15',
        PASSWORD_RESET_TTL_MINUTES: process.env.PASSWORD_RESET_TTL_MINUTES || '30',
        PASSWORD_RESET_COOLDOWN_SECONDS: process.env.PASSWORD_RESET_COOLDOWN_SECONDS || '60',
        // Piper：与 shell 中 export 二选一；若均不设置则走浏览器 Web Speech
        PIPER_MODEL: process.env.PIPER_MODEL || '',
        PIPER_BINARY: process.env.PIPER_BINARY || '',
      },
    },
  ],
};
