#!/bin/bash
# 简化版Web应用启动脚本

echo "🚀 启动简化版英语背诵系统Web应用..."

# 检查Python版本
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "📦 Python版本: $python_version"

# 检查依赖
echo "📦 检查依赖..."
if ! python3 -c "import flask" 2>/dev/null; then
    echo "⚠️  Flask未安装，正在安装依赖..."
    pip3 install -r requirements-simple.txt || {
        echo "❌ 依赖安装失败"
        echo "请尝试手动安装:"
        echo "  pip3 install Flask Flask-CORS gtts playsound prettytable readchar nltk requests python-dateutil"
        exit 1
    }
    echo "✅ 依赖安装完成"
else
    echo "✅ 依赖已安装"
fi

# 创建必要目录
mkdir -p user_data_simple
mkdir -p static

# 启动应用
echo "🌐 启动Web服务器..."
echo "访问地址: http://localhost:8000"
echo "按Ctrl+C停止服务"
echo ""

python3 simple_web_app.py