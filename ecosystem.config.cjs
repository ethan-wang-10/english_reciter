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

module.exports = {
  apps: [
    {
      name: 'english-reciter',
      cwd: root,
      script: venvGunicorn,
      args:
        '--bind 0.0.0.0:8000 --workers 1 --threads 4 simple_web_app:app',
      instances: 1,
      autorestart: true,
      max_restarts: 15,
      min_uptime: '10s',
      env: {
        PYTHONUNBUFFERED: '1',
        FLASK_ENV: 'production',
        TZ: 'Asia/Shanghai',
        // 启动前在 shell 中 export SECRET_KEY=...，PM2 会继承 process.env.SECRET_KEY
        SECRET_KEY: process.env.SECRET_KEY || '',
        // Piper：与 shell 中 export 二选一；若均不设置则走浏览器 Web Speech
        PIPER_MODEL: process.env.PIPER_MODEL || '',
        PIPER_BINARY: process.env.PIPER_BINARY || '',
      },
    },
  ],
};
