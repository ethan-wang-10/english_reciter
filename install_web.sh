#!/bin/bash
# Web 版依赖安装脚本

echo "📦 开始安装 Web 版依赖..."

# 安装 Python 依赖
pip3 install -r requirements.txt

echo "✅ 依赖安装完成！"
echo ""
echo "🚀 启动方式："
echo ""
echo "方式1: 直接运行"
echo "  python3 web_app.py"
echo ""
echo "方式2: Docker 运行（推荐）"
echo "  docker-compose up -d"
echo ""
echo "访问地址: http://localhost:8000"
