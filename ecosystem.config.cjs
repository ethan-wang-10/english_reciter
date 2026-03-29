/**
 * PM2 守护 Gunicorn（与 Dockerfile 中命令一致）
 *
 * 使用前：
 *   python3 -m venv .venv
 *   .venv/bin/pip install -r requirements-simple.txt
 *   cp -n config.example.json config.json   # 按需编辑
 *
 * 启动：
 *   export SECRET_KEY="$(openssl rand -hex 32)"
 *   pm2 start ecosystem.config.cjs
 *   pm2 save && pm2 startup
 */
const path = require('path');
const root = __dirname;
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
      },
    },
  ],
};
