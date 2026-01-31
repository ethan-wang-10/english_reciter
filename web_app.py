"""
FastAPI Web 应用 - 智能英语背诵系统
支持多用户、跨平台访问、可部署到腾讯云
"""

import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from jose import JWTError, jwt
import uvicorn

# 导入核心功能
from reciter import (
    WordReciter, Word, Config,
    get_logger, MAX_ATTEMPTS
)

# 日志配置
logger = get_logger(__name__)

# FastAPI 应用
app = FastAPI(
    title="智能英语背诵系统",
    description="基于艾宾浩斯遗忘曲线的单词背诵系统 - Web版",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# JWT 配置
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24小时

# OAuth2
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# 数据目录
DATA_DIR = Path("user_data")
DATA_DIR.mkdir(exist_ok=True)


# ==================== 数据模型 ====================

class User(BaseModel):
    """用户数据模型"""
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[str] = Field(None, max_length=100)
    created_at: str


class UserCreate(BaseModel):
    """用户注册模型"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    email: Optional[str] = Field(None, max_length=100)


class UserLogin(BaseModel):
    """用户登录模型"""
    username: str
    password: str


class Token(BaseModel):
    """Token 响应模型"""
    access_token: str
    token_type: str = "bearer"
    username: str


class WordModel(BaseModel):
    """单词模型"""
    english: str
    chinese: str
    success_count: int = 0
    next_review_date: str
    example: Optional[str] = None
    review_round: int = 0
    review_count: int = 0


class WordPractice(BaseModel):
    """单词练习请求"""
    word_id: str
    answer: str


class WordResponse(BaseModel):
    """单词响应模型"""
    english: str
    chinese: str
    example: str
    blanked_example: str
    remaining_attempts: int


# ==================== 用户数据管理 ====================

class UserManager:
    """用户管理器"""
    
    def __init__(self):
        self.users_file = DATA_DIR / "users.json"
        self.users = self._load_users()
    
    def _load_users(self) -> Dict:
        """加载用户数据"""
        if not self.users_file.exists():
            return {}
        
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载用户数据失败: {e}")
            return {}
    
    def _save_users(self) -> None:
        """保存用户数据"""
        try:
            with open(self.users_file, 'w', encoding='utf-8') as f:
                json.dump(self.users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存用户数据失败: {e}")
    
    def create_user(self, username: str, password: str, email: Optional[str] = None) -> bool:
        """创建用户"""
        if username in self.users:
            return False
        
        password_hash = self._hash_password(password)
        
        self.users[username] = {
            "password_hash": password_hash,
            "email": email,
            "created_at": datetime.now().isoformat()
        }
        
        # 为用户创建数据目录
        user_dir = DATA_DIR / username
        user_dir.mkdir(exist_ok=True)
        
        self._save_users()
        return True
    
    def verify_user(self, username: str, password: str) -> bool:
        """验证用户"""
        if username not in self.users:
            return False
        
        password_hash = self.users[username]["password_hash"]
        return self._verify_password(password, password_hash)
    
    def _hash_password(self, password: str) -> str:
        """哈希密码"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def _verify_password(self, password: str, password_hash: str) -> bool:
        """验证密码"""
        return self._hash_password(password) == password_hash
    
    def get_user(self, username: str) -> Optional[Dict]:
        """获取用户信息"""
        return self.users.get(username)


# 用户管理器实例
user_manager = UserManager()


# ==================== 认证函数 ====================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """创建访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """获取当前用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    return username


def get_user_reciter(username: str) -> WordReciter:
    """获取用户的背诵器实例"""
    config_file = DATA_DIR / username / "config.json"
    config = Config(str(config_file)) if config_file.exists() else Config()
    
    # 设置用户专属数据文件
    config.DATA_FILE = str(DATA_DIR / username / "learning_data.json")
    config.EXAMPLE_DB = str(DATA_DIR / username / "word_examples.json")
    
    return WordReciter(config)


# ==================== 认证接口 ====================

@app.post("/api/auth/register", response_model=User)
async def register(user: UserCreate):
    """用户注册"""
    if user_manager.create_user(user.username, user.password, user.email):
        logger.info(f"新用户注册: {user.username}")
        return User(
            username=user.username,
            email=user.email,
            created_at=datetime.now().isoformat()
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="用户名已存在"
        )


@app.post("/api/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """用户登录"""
    username = form_data.username
    password = form_data.password
    
    if user_manager.verify_user(username, password):
        access_token = create_access_token(
            data={"sub": username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        logger.info(f"用户登录: {username}")
        return Token(
            access_token=access_token,
            username=username
        )
    else:
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ==================== 主页 ====================

@app.get("/")
async def root():
    """首页"""
    return FileResponse("static/index.html")


# ==================== 单词管理接口 ====================

@app.get("/api/words/status")
async def get_status(username: str = Depends(get_current_user)):
    """获取学习状态"""
    reciter = get_user_reciter(username)
    
    all_words = [
        {
            "english": w.english,
            "chinese": w.chinese,
            "success_count": w.success_count,
            "max_success_count": reciter.config.MAX_SUCCESS_COUNT,
            "review_round": w.review_round,
            "review_count": w.review_count,
            "next_review_date": w.next_review_date.isoformat(),
            "remaining_days": (w.next_review_date - datetime.now().date()).days
        }
        for w in reciter.all_words
    ]
    
    stats = {
        "total_words": len(all_words),
        "mastered_words": len(reciter.mastered_words),
        "current_round": reciter.current_review_round,
        "avg_review_count": sum(w["review_count"] for w in all_words) / len(all_words) if all_words else 0
    }
    
    return {"words": all_words, "stats": stats}


@app.get("/api/words/review")
async def get_review_list(username: str = Depends(get_current_user)):
    """获取今日复习列表"""
    reciter = get_user_reciter(username)
    review_list = reciter._get_today_review_list()
    
    return {
        "words": [
            {
                "english": w.english,
                "chinese": w.chinese,
                "success_count": w.success_count,
                "review_count": w.review_count
            }
            for w in review_list
        ],
        "count": len(review_list)
    }


@app.post("/api/words/practice")
async def practice_word(practice: WordPractice, username: str = Depends(get_current_user)):
    """练习单词"""
    reciter = get_user_reciter(username)
    
    # 查找单词
    word = None
    for w in reciter.all_words:
        if w.english.lower() == practice.word_id.lower():
            word = w
            break
    
    if not word:
        raise HTTPException(status_code=404, detail="单词未找到")
    
    # 检查答案
    is_correct = practice.answer.strip().lower() == word.english.lower()
    
    if is_correct:
        word.success_count += 1
        word.review_count += 1
        
        if word.success_count >= reciter.config.MAX_SUCCESS_COUNT:
            reciter.mastered_words.append(word)
            reciter.all_words.remove(word)
            message = "🎉 已掌握单词！"
        else:
            delta_days = reciter._calculate_review_days(word.success_count)
            word.next_review_date = datetime.now().date() + timedelta(days=delta_days)
            message = f"✅ 正确！下次复习: +{delta_days}天"
    else:
        word.review_count += 1
        message = "❌ 错误，请继续努力！"
    
    # 保存数据
    reciter._save_data()
    
    return {
        "correct": is_correct,
        "message": message,
        "word": {
            "english": word.english,
            "chinese": word.chinese,
            "success_count": word.success_count
        }
    }


@app.post("/api/words/import")
async def import_words(
    file: UploadFile = File(...),
    username: str = Depends(get_current_user)
):
    """导入单词文件"""
    try:
        content = await file.read()
        text = content.decode('utf-8')
        
        words = []
        for line in text.split('\n'):
            if ',' in line:
                parts = line.strip().split(',', 1)
                if len(parts) == 2:
                    words.append((parts[0], parts[1]))
        
        reciter = get_user_reciter(username)
        reciter.add_words(words)
        
        logger.info(f"用户 {username} 导入了 {len(words)} 个单词")
        
        return {
            "message": f"成功导入 {len(words)} 个单词",
            "count": len(words)
        }
    except Exception as e:
        logger.error(f"导入单词失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


@app.get("/api/words/mastered")
async def get_mastered_words(username: str = Depends(get_current_user)):
    """获取已掌握单词"""
    reciter = get_user_reciter(username)
    
    return {
        "words": [
            {
                "english": w.english,
                "chinese": w.chinese,
                "review_count": w.review_count,
                "mastered_date": w.next_review_date.isoformat()
            }
            for w in reciter.mastered_words
        ],
        "count": len(reciter.mastered_words)
    }


# ==================== 静态文件服务 ====================

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")


# ==================== 启动配置 ====================

if __name__ == "__main__":
    # 开发环境
    uvicorn.run(
        "web_app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
