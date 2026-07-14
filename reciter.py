import os
import sys
import json
import math
import random
import shutil
import platform
import subprocess
import tempfile
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional
from datetime import date, timedelta
from pathlib import Path
from prettytable import PrettyTable
import readchar
import logging

from app_time import china_date_from_timestamp, china_now, china_today
from review_scheduler import (
    EXERCISE_TYPES,
    claim_review_event,
    choose_exercise_type,
    mastery_ready,
    mastery_snapshot,
    next_review_date as adaptive_next_review_date,
    normalize_review_state,
    record_mastery_attempt as update_mastery_attempt,
    schedule_review,
    scheduler_snapshot,
)

# 常量定义
MAX_ATTEMPTS = 3  # 拼写、听写等题型的最大尝试次数
SEMANTIC_EXERCISE_TYPES = frozenset({'recognition', 'context'})
SEMANTIC_MAX_ATTEMPTS = 2
DEFAULT_DAILY_REVIEW_LIMIT = 120
MAX_DAILY_REVIEW_LIMIT = 300
MIN_DAILY_REVIEW_SHARE = 0.6
DEFAULT_DAILY_NEW_WORD_TARGET = 15
DAILY_PERFORMANCE_HISTORY_DAYS = 30


def exercise_attempt_limit(exercise_type: str) -> int:
    """Return the attempt limit for one pass of an exercise."""
    if str(exercise_type or '').strip().lower() in SEMANTIC_EXERCISE_TYPES:
        return SEMANTIC_MAX_ATTEMPTS
    return MAX_ATTEMPTS


def get_logger(name: str = __name__) -> logging.Logger:
    """获取日志记录器（单例模式）"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # 文件处理器
        file_handler = logging.FileHandler('reciter.log', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    return logger

logger = get_logger()

try:
    from tts_piper import piper_runtime_ready, piper_synthesize_wav, play_wav_bytes
except ImportError:
    piper_runtime_ready = None  # type: ignore[misc, assignment]
    piper_synthesize_wav = None  # type: ignore[misc, assignment]
    play_wav_bytes = None  # type: ignore[misc, assignment]


class Config:
    """配置管理类"""
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self._load_config()
    
    def _load_config(self):
        """加载配置文件"""
        default_config = {
            "word_file": "words.txt",
            "data_file": "learning_data.json",
            "example_db": "word_examples.json",
            "max_success_count": 8,
            "tts_enabled": True,
            "max_review_round": 8,
            "review_interval_days": [1, 2, 4, 7, 15, 30, 60],
            "daily_review_limit": DEFAULT_DAILY_REVIEW_LIMIT,
            "backup_enabled": True,
            "backup_interval_days": 7,
            "max_backups": 10,
            "language": "zh",
            "log_level": "INFO",
            "piper_model": "",
            "piper_binary": "",
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    default_config.update(user_config)
            except Exception as e:
                logger.warning(f"配置文件加载失败，使用默认配置: {e}")
        
        self.WORD_FILE = default_config["word_file"]
        self.DATA_FILE = default_config["data_file"]
        self.EXAMPLE_DB = default_config["example_db"]
        self.MAX_SUCCESS_COUNT = default_config["max_success_count"]
        self.TTS_ENABLED = default_config["tts_enabled"]
        self.MAX_REVIEW_ROUND = default_config["max_review_round"]
        self.REVIEW_INTERVAL_DAYS = default_config["review_interval_days"]
        try:
            daily_review_limit = int(default_config["daily_review_limit"])
        except (TypeError, ValueError):
            daily_review_limit = DEFAULT_DAILY_REVIEW_LIMIT
        self.DAILY_REVIEW_LIMIT = max(
            1,
            min(MAX_DAILY_REVIEW_LIMIT, daily_review_limit),
        )
        self.BACKUP_ENABLED = default_config["backup_enabled"]
        self.BACKUP_INTERVAL_DAYS = default_config["backup_interval_days"]
        self.MAX_BACKUPS = default_config["max_backups"]
        self.LANGUAGE = default_config["language"]
        
        log_level = getattr(logging, default_config.get("log_level", "INFO"))
        logger.setLevel(log_level)

        self.PIPER_MODEL = (default_config.get("piper_model") or "").strip()
        self.PIPER_BINARY = (default_config.get("piper_binary") or "").strip()


class ExampleGenerator:
    """离线例句生成器（完全本地，无需网络）"""
    
    def __init__(self, example_db_file: str):
        self.example_db_file = example_db_file
        self.local_db: Dict[str, List[str]] = {}
        self._load_local_db()
    
    def _load_local_db(self) -> None:
        """加载本地例句库"""
        try:
            if os.path.exists(self.example_db_file):
                with open(self.example_db_file, 'r', encoding='utf-8') as f:
                    self.local_db = json.load(f)
                logger.info(f"成功加载本地例句库: {len(self.local_db)} 个单词")
        except Exception as e:
            logger.error(f"加载例句库失败: {e}")
            self.local_db = {}
    
    def save_local_db(self) -> None:
        """保存本地例句库"""
        try:
            with open(self.example_db_file, 'w', encoding='utf-8') as f:
                json.dump(self.local_db, f, ensure_ascii=False, indent=2)
            logger.info("例句库保存成功")
        except Exception as e:
            logger.error(f"保存例句库失败: {e}")
    
    def get_example(self, word: str, chinese: str) -> str:
        """
        获取包含指定单词的例句（完全离线）
        
        Args:
            word: 英文单词
            chinese: 中文释义
            
        Returns:
            例句（格式：英文_中文）
            
        Raises:
            ValueError: 如果 word 或 chinese 为空
        """
        if not word or not isinstance(word, str):
            raise ValueError("单词不能为空")
        if not chinese or not isinstance(chinese, str):
            raise ValueError("中文释义不能为空")
        
        word_lower = word.lower()
        
        if word_lower in self.local_db:
            return random.choice(self.local_db[word_lower])
        
        # 尝试使用 NLTK WordNet
        try:
            import nltk
            from nltk.corpus import wordnet as wn
            nltk.data.path.append(str(Path.home() / 'nltk_data'))
            
            synsets = wn.synsets(word)
            if synsets:
                examples = synsets[0].examples()
                if examples:
                    example = examples[0].lower()
                    if word_lower not in example.lower():
                        example = f"This is a {word} example."
                    return f"{example}_这是一个包含{chinese}的例句"
        except ImportError:
            logger.debug("NLTK 未安装，跳过 WordNet 查询")
        except Exception as e:
            logger.warning(f"NLTK 获取例句失败: {e}")
        
        # 生成默认例句
        templates = [
            f"This is a {word}.",
            f"I have a {word}.",
            f"The {word} is here.",
            f"This {word} is very good.",
            f"This is an example of {word}."
        ]
        example = random.choice(templates)
        
        return f"{example}_这是一个包含{chinese}的例句"
    
    def add_example(self, word: str, example: str) -> None:
        """添加例句到本地库"""
        word_lower = word.lower()
        if word_lower not in self.local_db:
            self.local_db[word_lower] = []
        if example not in self.local_db[word_lower]:
            self.local_db[word_lower].append(example)
            logger.info(f"为单词 '{word}' 添加了新例句")


class Word:
    """单词数据模型"""
    
    def __init__(
        self,
        english: str,
        chinese: str,
        success_count: int = 0,
        next_review_date: Optional[date] = None,
        example: Optional[str] = None,
        review_round: int = 0,
        review_count: int = 0
    ):
        self.english = english
        self.chinese = chinese
        self.success_count = success_count
        self.next_review_date = next_review_date or china_today()
        self.example = example
        self.review_round = review_round
        self.review_count = review_count
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'english': self.english,
            'chinese': self.chinese,
            'success_count': self.success_count,
            'next_review_date': self.next_review_date.isoformat(),
            'example': self.example,
            'review_round': self.review_round,
            'review_count': self.review_count
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Word':
        """从字典创建对象（不修改传入的 dict）"""
        d = dict(data)
        nr = d.get('next_review_date')
        if nr:
            try:
                d['next_review_date'] = date.fromisoformat(str(nr)[:10])
            except (TypeError, ValueError):
                d['next_review_date'] = china_today()
        else:
            d['next_review_date'] = china_today()
        d.setdefault('success_count', 0)
        d.setdefault('review_round', 0)
        d.setdefault('review_count', 0)
        if 'example' not in d or d.get('example') in (None, ''):
            d['example'] = None
        def safe_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        # 显式读取已知字段，避免未来版本新增逐词字段时旧 reader 把整个词库读成空。
        return cls(
            english=str(d.get('english') or '').strip(),
            chinese=str(d.get('chinese') or '').strip(),
            success_count=max(0, safe_int(d.get('success_count'))),
            next_review_date=d['next_review_date'],
            example=d.get('example'),
            review_round=max(0, safe_int(d.get('review_round'))),
            review_count=max(0, safe_int(d.get('review_count'))),
        )


class WordRepository:
    """单词数据访问层"""
    
    def __init__(self, config: Config):
        self.config = config
        self.learning_state_v2: Dict[str, Any] = {
            'version': 1,
            'review_states': {},
            'daily_task': None,
        }

    def _learning_state_sidecar_path(self) -> Path:
        data_path = Path(self.config.DATA_FILE)
        return data_path.with_name(f'{data_path.stem}.learning_state_v2.json')

    def _set_learning_state(self, raw: Any) -> None:
        src = dict(raw) if isinstance(raw, dict) else {}
        states = src.get('review_states')
        task = src.get('daily_task')
        src.setdefault('version', 1)
        src['review_states'] = dict(states) if isinstance(states, dict) else {}
        src['daily_task'] = dict(task) if isinstance(task, dict) else None
        self.learning_state_v2 = src

    @staticmethod
    def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            suffix='.json',
            dir=str(path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    
    def load_data(self) -> tuple[list[Word], list[Word]]:
        """
        加载学习数据
        
        Returns:
            (all_words, mastered_words)
        """
        try:
            with open(self.config.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                pending_rows = data.get('all_words') if isinstance(data, dict) else []
                mastered_rows = data.get('mastered_words') if isinstance(data, dict) else []
                all_words = [
                    Word.from_dict(w) for w in (pending_rows or []) if isinstance(w, dict)
                ]
                mastered_words = [
                    Word.from_dict(w) for w in (mastered_rows or []) if isinstance(w, dict)
                ]
                main_state = data.get('learning_state_v2')
                self._set_learning_state(main_state)
                sidecar = self._learning_state_sidecar_path()
                if not isinstance(main_state, dict) and sidecar.is_file():
                    try:
                        with sidecar.open('r', encoding='utf-8') as state_file:
                            sidecar_state = json.load(state_file)
                        self._set_learning_state(sidecar_state)
                    except (OSError, json.JSONDecodeError) as state_error:
                        logger.warning('学习状态 sidecar 无法读取，回退主数据: %s', state_error)
                
                for word in all_words + mastered_words:
                    if not hasattr(word, 'review_round'):
                        word.review_round = 0
                    if not hasattr(word, 'review_count'):
                        word.review_count = 0
                
                total_words = len(all_words) + len(mastered_words)
                mastered_count = len(mastered_words)
                avg_review_count = (
                    sum(w.review_count for w in all_words) / len(all_words)
                    if all_words else 0
                )
                
                logger.info(
                    f"加载成功: 总计 {total_words} 个 | "
                    f"已掌握 {mastered_count} 个 | "
                    f"平均复习次数 {avg_review_count:.1f}"
                )
                return all_words, mastered_words
                
        except FileNotFoundError:
            logger.warning(f"数据文件 {self.config.DATA_FILE} 不存在，将创建新文件")
            self.learning_state_v2 = {'version': 1, 'review_states': {}, 'daily_task': None}
            return [], []
        except json.JSONDecodeError as e:
            logger.error(f"数据文件 {self.config.DATA_FILE} 格式错误: {e}")
            logger.warning("将重置为初始状态")
            self.learning_state_v2 = {'version': 1, 'review_states': {}, 'daily_task': None}
            return [], []
        except Exception as e:
            logger.error(f"加载数据时发生未知错误: {e}")
            self.learning_state_v2 = {'version': 1, 'review_states': {}, 'daily_task': None}
            return [], []
    
    def save_data(
        self,
        all_words: list[Word],
        mastered_words: list[Word],
        learning_state_v2: Optional[Dict[str, Any]] = None,
    ) -> None:
        """保存学习数据（原子写入，降低并发下文件损坏风险）"""
        try:
            state_to_save = (
                learning_state_v2
                if learning_state_v2 is not None
                else self.learning_state_v2
            )
            data = {
                'all_words': [w.to_dict() for w in all_words],
                'mastered_words': [w.to_dict() for w in mastered_words],
                'learning_state_v2': state_to_save,
            }
            path = Path(self.config.DATA_FILE)
            self._write_json_atomic(path, data)
            self._write_json_atomic(self._learning_state_sidecar_path(), state_to_save)
            logger.debug("数据保存成功")
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
            raise
    
    def backup_data(self) -> Optional[str]:
        """
        备份数据文件
        
        Returns:
            备份文件路径，失败返回 None
        """
        if not self.config.BACKUP_ENABLED:
            return None
        
        try:
            now = china_now()
            backup_dir = Path("backups")
            backup_dir.mkdir(exist_ok=True)
            
            # 添加时间戳避免同一天多次备份冲突
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            backup_file = backup_dir / f"learning_data_backup_{timestamp}.json"
            
            shutil.copy2(self.config.DATA_FILE, backup_file)
            # copy2 保留源学习数据的 mtime；备份轮换应以创建备份的时间为准。
            os.utime(backup_file, None)
            
            self._cleanup_old_backups(backup_dir)
            
            logger.info(f"数据备份成功: {backup_file}")
            return str(backup_file)
        except FileNotFoundError:
            logger.warning("数据文件不存在，跳过备份")
            return None
        except Exception as e:
            logger.error(f"备份数据失败: {e}")
            return None
    
    def _cleanup_old_backups(self, backup_dir: Path) -> None:
        """清理旧备份文件"""
        try:
            backups = sorted(
                backup_dir.glob("learning_data_backup_*.json"),
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            
            for old_backup in backups[self.config.MAX_BACKUPS:]:
                old_backup.unlink()
                logger.info(f"删除旧备份: {old_backup}")
        except Exception as e:
            logger.warning(f"清理旧备份时出错: {e}")


class WordReciter:
    """核心背诵系统（完全离线）"""
    
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.example_generator = ExampleGenerator(self.config.EXAMPLE_DB)
        self.repository = WordRepository(self.config)
        
        self.all_words: List[Word] = []
        self.mastered_words: List[Word] = []
        self.learning_state_v2: Dict[str, Any] = {
            'version': 1,
            'review_states': {},
            'daily_task': None,
        }
        self.today = china_today()
        self.current_review_round = 0
        
        self._load_data()
        self._process_overdue_words()
        self._update_review_round()
        self._check_and_create_backup()
    
    def _check_and_create_backup(self) -> None:
        """检查并创建备份"""
        if not self.config.BACKUP_ENABLED:
            return
        
        backup_dir = Path("backups")
        if backup_dir.exists():
            backups = list(backup_dir.glob("learning_data_backup_*.json"))
            if backups:
                latest_backup = max(backups, key=lambda x: x.stat().st_mtime)
                last_backup_date = china_date_from_timestamp(latest_backup.stat().st_mtime)
                days_since_backup = (self.today - last_backup_date).days
                if days_since_backup < self.config.BACKUP_INTERVAL_DAYS:
                    logger.debug(f"距离上次备份仅 {days_since_backup} 天，跳过备份")
                    return
        
        self.repository.backup_data()
    
    def _load_data(self) -> None:
        """加载学习数据"""
        self.all_words, self.mastered_words = self.repository.load_data()
        self.learning_state_v2 = self.repository.learning_state_v2
    
    def _save_data(self, backup: bool = True) -> None:
        """保存学习数据"""
        self.repository.learning_state_v2 = self.learning_state_v2
        self.repository.save_data(
            self.all_words,
            self.mastered_words,
            self.learning_state_v2,
        )
        if backup:
            self.repository.backup_data()
    
    def show_mastered_words(self) -> None:
        """显示已掌握词汇"""
        if not self.mastered_words:
            print("\n📚 您还没有掌握任何单词")
            return
        
        table = PrettyTable()
        table.title = "🎓 已掌握词汇"
        table.field_names = ["英文", "中文", "掌握日期", "复习次数"]
        
        for word in self.mastered_words:
            table.add_row([
                word.english,
                word.chinese,
                word.next_review_date.strftime("%Y-%m-%d"),
                word.review_count
            ])
        
        print(table)
        print(f"\n📊 总计已掌握单词: {len(self.mastered_words)}")
    
    def review_mastered_words(self) -> None:
        """复习已掌握词汇"""
        if not self.mastered_words:
            print("\n📚 您还没有掌握任何单词")
            return
        
        sorted_words = sorted(self.mastered_words, key=lambda w: w.review_count)
        selected_words = sorted_words[:10]
        
        print(f"\n📚 开始复习 {len(selected_words)} 个已掌握单词")
        
        for word in selected_words:
            self._practice_word(word)
            word.review_count += 1
            self._save_data(backup=False)
        
        print("\n📊 本次复习完成！")
    
    def _process_overdue_words(self) -> None:
        """
        跨日时调用。不再把逾期日期强行改为「今天」，以保留 next_review_date < 今天，
        用于区分「此前未按计划完成而遗留」与「今日排期」；列表内先今日排期、后遗留。
        """
        overdue_count = sum(1 for w in self.all_words if w.next_review_date < self.today)
        if overdue_count > 0:
            logger.info(f"当前有 {overdue_count} 个单词的排期早于今日（遗留，排在今日排期之后）")
    
    def _update_review_round(self) -> None:
        """更新复习轮次"""
        if self.all_words:
            min_review_round = min(word.review_round for word in self.all_words)
            self.current_review_round = min_review_round
        else:
            self.current_review_round = 0
        
        logger.info(f"当前复习轮次: 第{self.current_review_round + 1}轮")
    
    def refresh_for_new_day(self) -> None:
        """服务器常驻时若跨日，更新日期并重新处理过期词与轮次。"""
        today = china_today()
        if today != self.today:
            self.today = today
            self._process_overdue_words()
            self._update_review_round()
    
    def get_today_review_list(self) -> List[Word]:
        """获取今日待复习列表（供 Web / 外部调用）。"""
        return self.get_today_learning_plan()['words']
    
    def save_learning_data(self, backup: bool = True) -> None:
        """持久化学习数据（供 Web / 外部调用）。"""
        self._save_data(backup=backup)

    @staticmethod
    def word_state_key(word_or_english: Any) -> str:
        raw = word_or_english.english if hasattr(word_or_english, 'english') else word_or_english
        return str(raw or '').strip().casefold()

    @staticmethod
    def task_attempt_count(item: Optional[Dict[str, Any]]) -> int:
        if not isinstance(item, dict):
            return 0
        try:
            attempts = max(0, min(1_000_000, int(item.get('attempts') or 0)))
        except (TypeError, ValueError, OverflowError):
            attempts = 0
        item['attempts'] = attempts
        return attempts

    def find_word(self, english: str, *, include_mastered: bool = True) -> Optional[Word]:
        key = self.word_state_key(english)
        for word in self.all_words:
            if self.word_state_key(word) == key:
                return word
        if include_mastered:
            for word in self.mastered_words:
                if self.word_state_key(word) == key:
                    return word
        return None

    def get_review_state(self, word: Word) -> Dict[str, Any]:
        states = self.learning_state_v2.setdefault('review_states', {})
        key = self.word_state_key(word)
        legacy_interval = self._calculate_review_days(max(0, word.success_count))
        state = normalize_review_state(
            states.get(key),
            success_count=word.success_count,
            max_success_count=self.config.MAX_SUCCESS_COUNT,
            legacy_interval_days=legacy_interval,
            legacy_active=word in self.mastered_words,
        )
        states[key] = state
        return state

    def review_state_payload(
        self,
        word: Word,
        *,
        listening_available: bool = False,
    ) -> Dict[str, Any]:
        state = self.get_review_state(word)
        return {
            'exercise_type': choose_exercise_type(
                state,
                listening_available=listening_available,
            ),
            'mastery': mastery_snapshot(state),
            'scheduler': scheduler_snapshot(state),
            'memory_status': str(state.get('memory_status') or 'learning'),
        }

    def record_mastery_attempt(
        self,
        word: Word,
        exercise_type: str,
        correct: bool,
        *,
        event_id: str = '',
        event_fingerprint: str = '',
        elapsed_ms: int = 0,
    ) -> bool:
        state = self.get_review_state(word)
        return update_mastery_attempt(
            state,
            exercise_type,
            correct,
            today=self.today,
            event_id=event_id,
            event_fingerprint=event_fingerprint,
            elapsed_ms=elapsed_ms,
        )

    def _daily_performance_history(self) -> Dict[str, Dict[str, int]]:
        raw = self.learning_state_v2.get('daily_performance')
        source = raw if isinstance(raw, dict) else {}
        oldest = self.today - timedelta(days=DAILY_PERFORMANCE_HISTORY_DAYS - 1)
        history: Dict[str, Dict[str, int]] = {}
        for day_key, values in source.items():
            try:
                day = date.fromisoformat(str(day_key)[:10])
            except (TypeError, ValueError):
                continue
            if day < oldest or day > self.today or not isinstance(values, dict):
                continue
            try:
                attempts = max(0, min(1_000_000, int(values.get('attempts') or 0)))
                correct = max(0, min(attempts, int(values.get('correct') or 0)))
            except (TypeError, ValueError, OverflowError):
                continue
            history[day.isoformat()] = {'attempts': attempts, 'correct': correct}
        self.learning_state_v2['daily_performance'] = history
        return history

    def _record_daily_performance(self, correct: bool) -> None:
        history = self._daily_performance_history()
        day_key = self.today.isoformat()
        row = history.setdefault(day_key, {'attempts': 0, 'correct': 0})
        row['attempts'] = min(1_000_000, int(row['attempts']) + 1)
        if correct:
            row['correct'] = min(row['attempts'], int(row['correct']) + 1)

    def recent_performance_snapshot(self, days: int = 7) -> Dict[str, Any]:
        window_days = max(1, min(DAILY_PERFORMANCE_HISTORY_DAYS, int(days or 7)))
        oldest = self.today - timedelta(days=window_days - 1)
        attempts = 0
        correct = 0
        for day_key, row in self._daily_performance_history().items():
            if date.fromisoformat(day_key) < oldest:
                continue
            attempts += int(row.get('attempts') or 0)
            correct += int(row.get('correct') or 0)
        accuracy = (correct / attempts) if attempts else None
        return {
            'days': window_days,
            'attempts': attempts,
            'correct': correct,
            'accuracy': round(accuracy, 4) if accuracy is not None else None,
        }

    def _daily_task_history(self) -> Dict[str, Dict[str, int]]:
        raw = self.learning_state_v2.get('daily_task_history')
        source = raw if isinstance(raw, dict) else {}
        oldest = self.today - timedelta(days=DAILY_PERFORMANCE_HISTORY_DAYS - 1)
        history: Dict[str, Dict[str, int]] = {}
        for day_key, values in source.items():
            try:
                day = date.fromisoformat(str(day_key)[:10])
            except (TypeError, ValueError):
                continue
            if day < oldest or day > self.today or not isinstance(values, dict):
                continue
            try:
                total = max(0, min(1_000_000, int(values.get('total') or 0)))
                completed = max(0, min(total, int(values.get('completed') or 0)))
                attempts = max(0, min(1_000_000, int(values.get('attempts') or 0)))
                difficult = max(0, min(total, int(values.get('difficult') or 0)))
            except (TypeError, ValueError, OverflowError):
                continue
            history[day.isoformat()] = {
                'total': total,
                'completed': completed,
                'attempts': attempts,
                'difficult': difficult,
            }
        self.learning_state_v2['daily_task_history'] = history
        return history

    def _task_history_row(self, task: Any) -> Optional[Dict[str, int]]:
        if not isinstance(task, dict):
            return None
        try:
            task_date = date.fromisoformat(str(task.get('date') or '')[:10])
        except (TypeError, ValueError):
            return None
        if task_date > self.today:
            return None
        items = [item for item in (task.get('items') or []) if isinstance(item, dict)]
        return {
            'total': len(items),
            'completed': sum(1 for item in items if item.get('status') == 'completed'),
            'attempts': sum(self.task_attempt_count(item) for item in items),
            'difficult': sum(
                1
                for item in items
                if self.task_attempt_count(item) >= exercise_attempt_limit(
                    str(item.get('exercise_type') or 'spelling')
                )
                or item.get('phase') == 'remedial'
            ),
        }

    def _archive_daily_task(self, task: Any) -> None:
        row = self._task_history_row(task)
        if row is None or not isinstance(task, dict):
            return
        day_key = str(task.get('date') or '')[:10]
        if day_key:
            self._daily_task_history()[day_key] = row

    def recent_learning_load_snapshot(self, days: int = 7) -> Dict[str, Any]:
        window_days = max(1, min(DAILY_PERFORMANCE_HISTORY_DAYS, int(days or 7)))
        oldest = self.today - timedelta(days=window_days - 1)
        rows = {
            day_key: dict(row)
            for day_key, row in self._daily_task_history().items()
            if date.fromisoformat(day_key) >= oldest
        }
        current_task = self.learning_state_v2.get('daily_task')
        current_row = self._task_history_row(current_task)
        if current_row is not None and isinstance(current_task, dict):
            current_day_key = str(current_task.get('date') or '')[:10]
            if current_day_key and date.fromisoformat(current_day_key) >= oldest:
                rows[current_day_key] = current_row

        total = sum(row['total'] for row in rows.values())
        completed = sum(row['completed'] for row in rows.values())
        attempts = sum(row['attempts'] for row in rows.values())
        difficult = sum(row['difficult'] for row in rows.values())
        return {
            'days': window_days,
            'assigned': total,
            'completed': completed,
            'completion_rate': round(completed / total, 4) if total else None,
            'attempts_per_completed': round(attempts / completed, 2) if completed else None,
            'difficult_rate': round(difficult / total, 4) if total else None,
        }

    def automatic_new_word_target(self, overdue_count: int = 0) -> int:
        recent = self.recent_performance_snapshot(7)
        workload = self.recent_learning_load_snapshot(7)
        accuracy = recent['accuracy']
        if recent['attempts'] < 20 or accuracy is None:
            target = DEFAULT_DAILY_NEW_WORD_TARGET
        elif accuracy >= 0.9:
            target = 30
        elif accuracy >= 0.8:
            target = 20
        elif accuracy >= 0.7:
            target = 15
        else:
            target = 5

        completion_rate = workload.get('completion_rate')
        if workload.get('assigned', 0) >= 10 and completion_rate is not None:
            if completion_rate < 0.6:
                target = min(target, 5)
            elif completion_rate < 0.8:
                target = min(target, 10)

        attempts_per_completed = workload.get('attempts_per_completed')
        if attempts_per_completed is not None:
            if attempts_per_completed >= 2.0:
                target = min(target, 10)
            elif attempts_per_completed >= 1.5:
                target = min(target, 15)

        limit = self.config.DAILY_REVIEW_LIMIT
        if overdue_count >= limit:
            target = 0
        elif overdue_count >= max(1, (limit + 1) // 2):
            target = min(target, 5)
        elif overdue_count >= max(1, (limit + 3) // 4):
            target = min(target, 10)
        return max(0, min(limit, target))

    def _is_mastered_maintenance_due(self, word: Word) -> bool:
        state = self.get_review_state(word)
        return bool(state['scheduler'].get('active')) and word.next_review_date <= self.today

    def count_due_words(self) -> int:
        due_keys = {
            self.word_state_key(word)
            for word in self.all_words
            if word.next_review_date <= self.today
        }
        due_keys.update(
            self.word_state_key(word)
            for word in self.mastered_words
            if self._is_mastered_maintenance_due(word)
        )
        task = self.learning_state_v2.get('daily_task')
        if isinstance(task, dict) and task.get('date') == self.today.isoformat():
            due_keys.update(
                str(item.get('word_key') or '')
                for item in (task.get('items') or [])
                if isinstance(item, dict) and item.get('status') == 'pending'
            )
        due_keys.discard('')
        return len(due_keys)

    def _today_task_candidates(
        self,
        *,
        listening_available: bool = False,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for word in self.all_words:
            if word.next_review_date > self.today:
                state = self.get_review_state(word)
                weak_mastery = mastery_snapshot(state)
                weak_attempts = sum(
                    int(x.get('attempts') or 0)
                    for x in weak_mastery.get('by_type', {}).values()
                )
                if weak_attempts < 3 or weak_mastery['overall'] >= 0.35:
                    continue
                candidates.append(
                    {'word': word, 'maintenance': False, 'forced_reason': 'weak'}
                )
                continue
            candidates.append({'word': word, 'maintenance': False})
        for word in self.mastered_words:
            if self._is_mastered_maintenance_due(word):
                candidates.append({'word': word, 'maintenance': True})

        for item in candidates:
            word = item['word']
            state = self.get_review_state(word)
            mastery = mastery_snapshot(state)
            attempted = sum(
                int(x.get('attempts') or 0) for x in mastery.get('by_type', {}).values()
            )
            if item.get('forced_reason'):
                reason = str(item['forced_reason'])
            elif item['maintenance']:
                state_status = str(state.get('memory_status') or 'stable')
                reason = 'reinforcement' if state_status == 'reinforcement' else 'maintenance'
            elif word.success_count <= 0 and attempted == 0 and word.review_count <= 0:
                reason = 'new'
            elif word.next_review_date < self.today:
                reason = 'overdue'
            elif attempted > 0 and mastery['overall'] < 0.5:
                reason = 'weak'
            else:
                reason = 'due'
            item.update(
                {
                    'reason': reason,
                    'exercise_type': choose_exercise_type(
                        state,
                        listening_available=listening_available,
                    ),
                    'mastery_score': mastery['overall'],
                }
            )

        rank = {
            'overdue': 0,
            'weak': 1,
            'reinforcement': 2,
            'due': 3,
            'maintenance': 4,
            'new': 5,
        }
        candidates.sort(
            key=lambda item: (
                rank[item['reason']],
                item['word'].next_review_date,
                item['mastery_score'],
                item['word'].review_count,
                item['word'].english.casefold(),
            )
        )
        return candidates

    def _create_today_task(
        self,
        *,
        listening_available: bool = False,
    ) -> Optional[Dict[str, Any]]:
        previous_task = self.learning_state_v2.get('daily_task')
        self._archive_daily_task(previous_task)
        candidates = self._today_task_candidates(
            listening_available=listening_available,
        )
        non_new = [item for item in candidates if item['reason'] != 'new']
        new_words = [item for item in candidates if item['reason'] == 'new']
        limit = self.config.DAILY_REVIEW_LIMIT
        review_reserve = min(len(non_new), math.ceil(limit * MIN_DAILY_REVIEW_SHARE))
        new_capacity = max(0, limit - review_reserve)
        overdue_count = sum(1 for item in non_new if item['reason'] == 'overdue')
        new_word_target = self.automatic_new_word_target(overdue_count)
        selected_new = new_words[: min(new_capacity, new_word_target)]
        remaining_slots = max(0, limit - len(selected_new))
        selected_non_new = non_new[:remaining_slots]
        selected = selected_non_new + selected_new
        if not selected:
            self.learning_state_v2['daily_task'] = None
            return None

        task_id = uuid.uuid4().hex
        items = []
        for candidate in selected:
            word = candidate['word']
            items.append(
                {
                    'item_id': uuid.uuid4().hex[:16],
                    'word_key': self.word_state_key(word),
                    'scheduled_due_date': word.next_review_date.isoformat(),
                    'exercise_type': candidate['exercise_type'],
                    'reason': candidate['reason'],
                    'phase': 'main',
                    'status': 'pending',
                    'attempts': 0,
                }
            )
        task = {
            'version': 1,
            'task_id': task_id,
            'date': self.today.isoformat(),
            'created_at': china_now().isoformat(),
            'status': 'active',
            'available_at_creation': len(candidates),
            'new_word_target': new_word_target,
            'review_reserve': review_reserve,
            'recent_performance': self.recent_performance_snapshot(7),
            'recent_learning_load': self.recent_learning_load_snapshot(7),
            'items': items,
        }
        self.learning_state_v2['daily_task'] = task
        return task

    def _current_today_task(
        self,
        *,
        listening_available: bool = False,
    ) -> Optional[Dict[str, Any]]:
        task = self.learning_state_v2.get('daily_task')
        if not isinstance(task, dict) or task.get('date') != self.today.isoformat():
            return None
        if not str(task.get('task_id') or '').strip():
            task['task_id'] = uuid.uuid4().hex
        items = task.get('items')
        if not isinstance(items, list):
            return None
        valid_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            word = self.find_word(item.get('word_key', ''))
            if word is None:
                continue
            if not str(item.get('item_id') or '').strip():
                item['item_id'] = uuid.uuid4().hex[:16]
            if item.get('exercise_type') not in EXERCISE_TYPES:
                item['exercise_type'] = choose_exercise_type(
                    self.get_review_state(word),
                    listening_available=listening_available,
                )
            elif item.get('exercise_type') == 'listening' and not listening_available:
                item['exercise_type'] = choose_exercise_type(
                    self.get_review_state(word),
                    listening_available=False,
                )
            if item.get('phase') not in ('main', 'remedial'):
                item['phase'] = 'main'
            if item.get('reason') not in (
                'overdue',
                'weak',
                'reinforcement',
                'maintenance',
                'due',
                'new',
            ):
                item['reason'] = 'due'
            if item.get('status') not in ('pending', 'completed'):
                item['status'] = 'pending'
            self.task_attempt_count(item)
            # 兼容仍在运行的旧前端：它会推进排期但不会回传 task/item id。
            # 未来薄弱词本来就排在今天之后，必须确认排期确实发生变化，避免刷新即误完成。
            scheduled_due_date = str(item.get('scheduled_due_date') or '')
            schedule_changed = bool(
                scheduled_due_date
                and word.next_review_date.isoformat() != scheduled_due_date
            )
            legacy_item_moved = not scheduled_due_date and item.get('reason') != 'weak'
            if (
                item.get('status') == 'pending'
                and self.task_attempt_count(item) == 0
                and word.next_review_date > self.today
                and (schedule_changed or legacy_item_moved)
            ):
                item['status'] = 'completed'
            valid_items.append(item)
        if items and not valid_items:
            return None
        task['items'] = valid_items
        pending = [item for item in valid_items if item.get('status') == 'pending']
        if pending:
            task['status'] = 'active'
            return task
        task['status'] = 'completed'
        return task

    def get_today_learning_plan(
        self,
        *,
        listening_available: bool = False,
    ) -> Dict[str, Any]:
        """Return a persistent, bounded task and its remaining word objects."""
        task = self._current_today_task(
            listening_available=listening_available,
        ) or self._create_today_task(
            listening_available=listening_available,
        )
        if not task:
            return {
                'words': [],
                'items': [],
                'plan': {
                    'task_id': '',
                    'date': self.today.isoformat(),
                    'total': 0,
                    'completed': 0,
                    'remaining': 0,
                    'estimated_minutes': 0,
                    'backlog_after_task': 0,
                    'buckets': {},
                    'exercise_mix': {},
                    'algorithm': 'adaptive-sm2-v1',
                    'new_word_target': self.automatic_new_word_target(),
                    'review_reserve': 0,
                    'recent_accuracy_percent': None,
                    'recent_completion_percent': None,
                    'recent_attempts_per_completed': None,
                },
            }

        items = task['items']
        pending_items = [item for item in items if item.get('status') == 'pending']
        completed = len(items) - len(pending_items)
        words: List[Word] = []
        active_items: List[Dict[str, Any]] = []
        for item in pending_items:
            word = self.find_word(item.get('word_key', ''))
            if word is None:
                continue
            words.append(word)
            active_items.append(item)

        buckets: Dict[str, int] = defaultdict(int)
        exercise_mix: Dict[str, int] = defaultdict(int)
        for item in items:
            buckets[str(item.get('reason') or 'due')] += 1
            exercise_mix[str(item.get('exercise_type') or 'spelling')] += 1
        total = len(items)
        try:
            available_at_creation = max(
                total,
                int(task.get('available_at_creation') or total),
            )
        except (TypeError, ValueError, OverflowError):
            available_at_creation = total
        task['available_at_creation'] = available_at_creation
        backlog = available_at_creation - total
        recent = (
            task.get('recent_performance')
            if isinstance(task.get('recent_performance'), dict)
            else {}
        )
        recent_accuracy = recent.get('accuracy')
        recent_load = (
            task.get('recent_learning_load')
            if isinstance(task.get('recent_learning_load'), dict)
            else {}
        )
        recent_completion = recent_load.get('completion_rate')
        attempts_per_completed = recent_load.get('attempts_per_completed')
        return {
            'words': words,
            'items': active_items,
            'plan': {
                'task_id': task['task_id'],
                'date': task['date'],
                'total': total,
                'completed': completed,
                'remaining': len(active_items),
                'estimated_minutes': max(1, round(len(active_items) * 0.6)) if active_items else 0,
                'backlog_after_task': backlog,
                'buckets': dict(buckets),
                'exercise_mix': dict(exercise_mix),
                'algorithm': 'adaptive-sm2-v1',
                'new_word_target': int(task.get('new_word_target') or 0),
                'review_reserve': int(task.get('review_reserve') or 0),
                'recent_accuracy_percent': (
                    round(float(recent_accuracy) * 100)
                    if recent_accuracy is not None
                    else None
                ),
                'recent_completion_percent': (
                    round(float(recent_completion) * 100)
                    if recent_completion is not None
                    else None
                ),
                'recent_attempts_per_completed': (
                    round(float(attempts_per_completed), 1)
                    if attempts_per_completed is not None
                    else None
                ),
            },
        }

    def resolve_daily_task_item(
        self,
        task_id: str,
        item_id: str,
        word_id: str,
        event_id: str = '',
    ) -> Optional[Dict[str, Any]]:
        task = self.learning_state_v2.get('daily_task')
        if not isinstance(task, dict) or task.get('date') != self.today.isoformat():
            return None
        if str(task.get('task_id') or '') != str(task_id or ''):
            return None
        key = self.word_state_key(word_id) if str(word_id or '').strip() else ''
        event_key = str(event_id or '')[:96]
        states = self.learning_state_v2.get('review_states')
        review_state = states.get(key) if isinstance(states, dict) else None
        recent_ids = (
            review_state.get('recent_event_ids')
            if isinstance(review_state, dict)
            and isinstance(review_state.get('recent_event_ids'), list)
            else []
        )
        recent_results = (
            review_state.get('recent_event_results')
            if isinstance(review_state, dict)
            and isinstance(review_state.get('recent_event_results'), dict)
            else {}
        )
        known_completed_replay = bool(
            event_key
            and event_key in recent_ids
            and isinstance(recent_results.get(event_key), dict)
        )
        for item in task.get('items') or []:
            if (
                isinstance(item, dict)
                and str(item.get('item_id') or '') == str(item_id or '')
                and (not key or item.get('word_key') == key)
                and (
                    item.get('status') == 'pending'
                    or (
                        item.get('status') == 'completed'
                        and (
                            str(item.get('last_event_id') or '') == event_key
                            or known_completed_replay
                        )
                    )
                )
            ):
                return item
        return None

    def record_daily_task_attempt(
        self,
        item: Optional[Dict[str, Any]],
        event_id: str = '',
    ) -> int:
        if not item:
            return 0
        item['attempts'] = min(1_000_000, self.task_attempt_count(item) + 1)
        if event_id:
            event_key = str(event_id)[:96]
            if str(item.get('last_event_id') or '') != event_key:
                item.pop('last_event_result', None)
                item.pop('last_event_remedial', None)
            item['last_event_id'] = event_key
        return item['attempts']

    def complete_daily_task_item(
        self,
        item: Optional[Dict[str, Any]],
        event_id: str = '',
    ) -> None:
        if not item:
            return
        item['status'] = 'completed'
        item['completed_at'] = china_now().isoformat()
        if event_id:
            item['last_event_id'] = str(event_id)[:96]

    def daily_task_progress(self) -> Dict[str, Any]:
        task = self.learning_state_v2.get('daily_task')
        if not isinstance(task, dict) or task.get('date') != self.today.isoformat():
            return {'total': 0, 'completed': 0, 'remaining': 0}
        items = [item for item in (task.get('items') or []) if isinstance(item, dict)]
        completed = sum(1 for item in items if item.get('status') == 'completed')
        return {
            'task_id': str(task.get('task_id') or ''),
            'total': len(items),
            'completed': completed,
            'remaining': max(0, len(items) - completed),
        }

    def _prune_learning_state_for_words(self, english_values: List[str]) -> None:
        keys = {self.word_state_key(value) for value in english_values if value}
        states = self.learning_state_v2.setdefault('review_states', {})
        for key in keys:
            states.pop(key, None)
        task = self.learning_state_v2.get('daily_task')
        if isinstance(task, dict) and isinstance(task.get('items'), list):
            before_count = len(task['items'])
            task['items'] = [
                item
                for item in task['items']
                if not isinstance(item, dict) or item.get('word_key') not in keys
            ]
            removed_from_task = before_count - len(task['items'])
            if removed_from_task > 0:
                try:
                    available = int(task.get('available_at_creation') or before_count)
                except (TypeError, ValueError, OverflowError):
                    available = before_count
                task['available_at_creation'] = max(
                    len(task['items']),
                    available - removed_from_task,
                )

    def remove_words_by_english(self, english_candidates: List[str]) -> dict:
        """
        按英文（不区分大小写）从待复习与已掌握中移除单词；同步删除本地例句库中对应条目。
        english_candidates 中同一词只处理一次（按首次出现顺序）。
        """
        keys_ordered: List[str] = []
        seen = set()
        for e in english_candidates:
            if not e or not str(e).strip():
                continue
            k = str(e).strip().lower()
            if k not in seen:
                seen.add(k)
                keys_ordered.append(k)
        if not keys_ordered:
            return {'removed': 0, 'removed_english': [], 'not_found': []}

        removed_english: List[str] = []
        not_found: List[str] = []

        for key in keys_ordered:
            found = False
            for lst in (self.all_words, self.mastered_words):
                for i in range(len(lst)):
                    if lst[i].english.lower() == key:
                        w = lst.pop(i)
                        removed_english.append(w.english)
                        lk = w.english.lower()
                        if lk in self.example_generator.local_db:
                            del self.example_generator.local_db[lk]
                        found = True
                        break
                if found:
                    break
            if not found:
                not_found.append(key)

        if removed_english:
            self._prune_learning_state_for_words(removed_english)
            self._update_review_round()
            self.save_learning_data(backup=True)
            self.example_generator.save_local_db()

        return {
            'removed': len(removed_english),
            'removed_english': removed_english,
            'not_found': not_found,
        }

    def remove_pending_words_by_english(self, english_candidates: List[str]) -> dict:
        """
        仅从待复习列表中按英文移除单词（不删除已掌握词）；同步删除本地例句库中对应条目。
        """
        keys_ordered: List[str] = []
        seen = set()
        for e in english_candidates:
            if not e or not str(e).strip():
                continue
            k = str(e).strip().lower()
            if k not in seen:
                seen.add(k)
                keys_ordered.append(k)
        if not keys_ordered:
            return {'removed': 0, 'removed_english': [], 'not_found': []}

        removed_english: List[str] = []
        not_found: List[str] = []

        for key in keys_ordered:
            found = False
            for i in range(len(self.all_words)):
                if self.all_words[i].english.lower() == key:
                    w = self.all_words.pop(i)
                    removed_english.append(w.english)
                    lk = w.english.lower()
                    if lk in self.example_generator.local_db:
                        del self.example_generator.local_db[lk]
                    found = True
                    break
            if not found:
                not_found.append(key)

        if removed_english:
            self._prune_learning_state_for_words(removed_english)
            self._update_review_round()
            self.save_learning_data(backup=True)
            self.example_generator.save_local_db()

        return {
            'removed': len(removed_english),
            'removed_english': removed_english,
            'not_found': not_found,
        }

    def calculate_review_days(self, success_count: int) -> int:
        """根据成功次数计算下次复习间隔天数。"""
        return self._calculate_review_days(success_count)

    def record_answer_incorrect(self, word: Word, *, final_attempt: bool = False) -> None:
        """记录答错；仅在本轮最后一次失败时结算新的排期。"""
        word.review_count += 1
        if final_attempt:
            state = self.get_review_state(word)
            schedule_review(state, 'again', today=self.today)
            word.next_review_date = adaptive_next_review_date(state, self.today)
            if word in self.mastered_words:
                state['memory_status'] = 'reinforcement'
                state['reinforcement_since'] = self.today.isoformat()

    def record_bonus_answer_correct(self, word: Word) -> str:
        """加练答对：仅增加复习次数，不改变掌握进度与排期。"""
        word.review_count += 1
        return '✅ 正确！（加练仅计复习次数）'

    def get_extra_review_words(self, count: int = 5) -> List[Word]:
        """
        从待复习与已掌握词库中选词：复习次数最少优先，同次数内随机打乱，
        保证长期覆盖（低次数词优先被抽到）。
        """
        pool: List[Word] = list(self.all_words) + list(self.mastered_words)
        if not pool:
            return []
        by_rc: dict[int, List[Word]] = defaultdict(list)
        for w in pool:
            by_rc[w.review_count].append(w)
        ordered: List[Word] = []
        for rc in sorted(by_rc.keys()):
            tier = by_rc[rc]
            random.shuffle(tier)
            ordered.extend(tier)
        return ordered[:count]

    def record_answer_correct(
        self,
        word: Word,
        *,
        remedial: bool = False,
        rating: str = 'good',
        require_multidimensional: bool = False,
        require_listening: bool = True,
    ) -> str:
        """
        答对后的持久化更新（Web / CLI 共用）。
        remedial=True：错题巩固，不增加 success_count，但排期到今日之后，避免当日重复出现。
        """
        state = self.get_review_state(word)
        effective_rating = 'hard' if remedial else rating
        delta_days = schedule_review(state, effective_rating, today=self.today)
        word.next_review_date = adaptive_next_review_date(state, self.today)

        if remedial:
            word.review_count += 1
            if word in self.mastered_words and state.get('memory_status') == 'reinforcement':
                return '✅ 正确！（本轮巩固完成，后续将继续确认记忆稳定性）'
            return '✅ 正确！（错题巩固不计入掌握进度）'

        if word in self.mastered_words:
            word.review_count += 1
            if (
                state.get('memory_status') == 'reinforcement'
                and mastery_ready(state)
            ):
                state['memory_status'] = 'stable'
                state['reinforcement_since'] = None
                return f'✅ 已恢复稳定掌握，下次复习: +{delta_days}天'
            return f'✅ 记忆保持良好，下次复习: +{delta_days}天'

        word.success_count = min(self.config.MAX_SUCCESS_COUNT, word.success_count + 1)
        word.review_count += 1
        if mastery_ready(state):
            state['mastered_date'] = self.today.isoformat()
            state['memory_status'] = 'stable'
            state['reinforcement_since'] = None
            self.mastered_words.append(word)
            self.all_words.remove(word)
            return f'🎉 已掌握单词！{delta_days}天后进行保持复习'

        return f'✅ 正确！继续完成识义、语境与拼写目标；下次复习: +{delta_days}天'

    def apply_scored_review_attempt(
        self,
        word: Word,
        *,
        exercise_type: str,
        correct: bool,
        task_item: Optional[Dict[str, Any]] = None,
        event_id: str = '',
        elapsed_ms: int = 0,
        hint_count: int = 0,
        attempt_number: int = 1,
        remedial: bool = False,
        bonus_practice: bool = False,
        audio_available: bool = True,
        submission_fingerprint: str = '',
    ) -> Dict[str, Any]:
        """Atomically apply one server-scored answer to learning state."""
        old_success_count = word.success_count
        old_mastered_count = len(self.mastered_words)
        attempt_limit = exercise_attempt_limit(exercise_type)
        task_requires_remedial = bool(
            task_item
            and (
                task_item.get('phase') == 'remedial'
                or self.task_attempt_count(task_item) >= attempt_limit
            )
        )
        replayed_task_event = bool(
            task_item
            and event_id
            and str(task_item.get('last_event_id') or '') == str(event_id)
        )
        if replayed_task_event and 'last_event_remedial' in task_item:
            effective_remedial = bool(task_item.get('last_event_remedial'))
        else:
            effective_remedial = bool(remedial or task_requires_remedial)
        try:
            normalized_elapsed_ms = max(0, min(600_000, int(elapsed_ms or 0)))
        except (TypeError, ValueError, OverflowError):
            normalized_elapsed_ms = 0
        event_fingerprint = json.dumps(
            {
                'word': self.word_state_key(word),
                'exercise_type': str(exercise_type or 'spelling'),
                'correct': bool(correct),
                'task_item_id': str((task_item or {}).get('item_id') or ''),
                'attempt_number': max(1, int(attempt_number or 1)),
                'hint_count': max(0, int(hint_count or 0)),
                'elapsed_ms': normalized_elapsed_ms,
                'remedial': bool(remedial),
                'bonus_practice': bool(bonus_practice),
                'audio_available': bool(audio_available),
                'submission_fingerprint': str(submission_fingerprint or '')[:160],
            },
            sort_keys=True,
            separators=(',', ':'),
        )
        if bonus_practice or effective_remedial:
            recorded = claim_review_event(
                self.get_review_state(word),
                event_id,
                event_fingerprint,
            )
        else:
            recorded = self.record_mastery_attempt(
                word,
                exercise_type,
                correct,
                event_id=event_id,
                event_fingerprint=event_fingerprint,
                elapsed_ms=elapsed_ms,
            )
        if not recorded and replayed_task_event:
            cached_result = task_item.get('last_event_result')
            if isinstance(cached_result, dict):
                return dict(cached_result)
        if not recorded and event_id:
            state = self.get_review_state(word)
            event_results = state.get('recent_event_results')
            cached_result = (
                event_results.get(str(event_id)[:96])
                if isinstance(event_results, dict)
                else None
            )
            if isinstance(cached_result, dict):
                return dict(cached_result)
        effective_attempt = max(1, int(attempt_number or 1))
        if task_item and recorded:
            effective_attempt = self.record_daily_task_attempt(task_item, event_id)
            task_item['last_event_remedial'] = effective_remedial
        elif task_item:
            effective_attempt = max(1, self.task_attempt_count(task_item))
        final_attempt = bool(
            not correct and effective_attempt % attempt_limit == 0
        )

        if not recorded:
            message = '本次作答已记录'
        elif bonus_practice:
            if correct:
                message = self.record_bonus_answer_correct(word)
            else:
                self.record_answer_incorrect(word)
                message = '❌ 错误，请继续努力！'
        elif correct:
            rating = 'hard' if effective_attempt > 1 or hint_count > 0 else 'good'
            message = self.record_answer_correct(
                word,
                remedial=effective_remedial,
                rating=rating,
                require_multidimensional=True,
                require_listening=audio_available,
            )
            self.complete_daily_task_item(task_item, event_id)
        else:
            self.record_answer_incorrect(
                word,
                final_attempt=final_attempt,
            )
            if task_item and final_attempt:
                task_item['phase'] = 'remedial'
            message = '❌ 错误，请继续努力！'

        if recorded and not bonus_practice and not effective_remedial:
            self._record_daily_performance(correct)

        result = {
            'recorded': recorded,
            'message': message,
            'attempt_number': effective_attempt,
            'attempt_limit': attempt_limit,
            'final_attempt': final_attempt,
            'old_success_count': old_success_count,
            'new_success_count': word.success_count,
            'mastered_now': len(self.mastered_words) > old_mastered_count,
            'remedial': effective_remedial,
        }
        if task_item and recorded and event_id:
            task_item['last_event_result'] = dict(result)
        if recorded and event_id:
            state = self.get_review_state(word)
            event_results = state.setdefault('recent_event_results', {})
            if not isinstance(event_results, dict):
                event_results = {}
                state['recent_event_results'] = event_results
            event_results[str(event_id)[:96]] = dict(result)
        return result

    def _sort_today_review_bucket(self, words: List[Word]) -> List[Word]:
        """同一轮内排序：今日排期（符合进度）优先，其次遗留（越早到期越靠前）。"""
        today = self.today
        due_today = [w for w in words if w.next_review_date == today]
        carryover = [w for w in words if w.next_review_date < today]
        due_today.sort(key=lambda w: (w.review_count, w.english.lower()))
        carryover.sort(key=lambda w: (w.next_review_date, w.review_count, w.english.lower()))
        return due_today + carryover

    def _get_today_review_list(self) -> List[Word]:
        """获取今日复习列表"""
        overdue_words = [w for w in self.all_words if w.next_review_date <= self.today]
        
        if not overdue_words:
            return []
        
        # 使用 defaultdict 简化代码
        words_by_round: dict[int, List[Word]] = defaultdict(list)
        for word in overdue_words:
            words_by_round[word.review_round].append(word)
        
        if self.current_review_round in words_by_round:
            current_round_words = words_by_round[self.current_review_round]
            return self._sort_today_review_bucket(current_round_words)
        
        min_round = min(words_by_round.keys())
        min_round_words = words_by_round[min_round]
        return self._sort_today_review_bucket(min_round_words)
    
    def show_status(self) -> None:
        """显示复习状态看板"""
        table = PrettyTable()
        table.title = f"📅 单词复习看板（第{self.current_review_round + 1}轮）"
        table.field_names = ["英文", "中文", "掌握进度", "复习轮次", "复习次数", "下次复习", "剩余天数"]
        
        for word in sorted(self.all_words, key=lambda x: (x.review_round, x.review_count, x.next_review_date)):
            remaining_days = (word.next_review_date - self.today).days
            progress_bar = f"{word.success_count}/{self.config.MAX_SUCCESS_COUNT} " + \
                          "★"*word.success_count + "☆"*(self.config.MAX_SUCCESS_COUNT-word.success_count)
            
            table.add_row([
                word.english,
                word.chinese,
                progress_bar,
                f"第{word.review_round + 1}轮",
                word.review_count,
                word.next_review_date.strftime("%Y-%m-%d"),
                remaining_days if remaining_days > 0 else "今天"
            ])
        
        print(table)
        
        stats = PrettyTable()
        stats.title = "📊 学习统计"
        stats.field_names = ["统计项", "数量"]
        stats.add_row(["当前复习轮次", f"第{self.current_review_round + 1}轮"])
        stats.add_row(["待复习单词", len(self.all_words)])
        stats.add_row(["已掌握单词", len(self.mastered_words)])
        stats.add_row(["总单词数", len(self.all_words) + len(self.mastered_words)])
        
        if self.all_words:
            avg_review_count = sum(w.review_count for w in self.all_words) / len(self.all_words)
            stats.add_row(["平均复习次数", f"{avg_review_count:.1f}"])
        
        print(stats)
    
    def _get_example(self, word: Word) -> str:
        """获取最佳例句"""
        if not word.example:
            word.example = self.example_generator.get_example(word.english, word.chinese)
        return word.example
    
    def _calculate_review_days(self, success_count: int) -> int:
        """
        计算复习间隔天数
        
        Args:
            success_count: 成功次数
            
        Returns:
            距离下次复习的天数
        """
        if success_count == 0:
            return 0
        
        success_index = success_count - 1
        if success_index < len(self.config.REVIEW_INTERVAL_DAYS):
            return self.config.REVIEW_INTERVAL_DAYS[success_index]
        return self.config.REVIEW_INTERVAL_DAYS[-1]
    
    def _text_to_speech(self, text: str) -> None:
        """文本转语音：优先 Piper（配置模型时），否则 macOS ``say``。"""
        if not self.config.TTS_ENABLED:
            return

        try:
            if not text or not isinstance(text, str):
                logger.warning("无效的文本输入")
                return

            en_text = text.split('_')[0]
            if not en_text:
                logger.warning("无法提取有效的英文文本")
                return

            safe = "".join(
                ch for ch in en_text.strip()[:500] if ch.isprintable() or ch.isspace()
            ).strip()[:500]
            if not safe:
                return

            if piper_runtime_ready and piper_synthesize_wav and play_wav_bytes:
                if piper_runtime_ready(self.config):
                    wav = piper_synthesize_wav(safe, self.config)
                    if wav:
                        play_wav_bytes(wav)
                        return

            if shutil.which('say') is None:
                logger.debug("say 命令不可用，跳过语音播放")
                return

            try:
                subprocess.run(
                    ['say', safe],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            except subprocess.TimeoutExpired:
                logger.warning("语音朗读超时")
            except Exception as e:
                logger.error(f"语音生成失败: {e}")
        except Exception as e:
            logger.error(f"语音生成失败: {e}")
    
    def _practice_word(self, word: Word, remedial: bool = False) -> bool:
        """单个单词练习流程（与 Web 一致：最多 3 次尝试；错题巩固不计入掌握进度）。"""
        self._last_practice_wrong_attempts = 0
        print(f"\n{'━'*30}")
        print(f"🔔 当前进度: {word.success_count}/{self.config.MAX_SUCCESS_COUNT}")
        if remedial:
            print("📌 错题巩固（答对不计入掌握进度，与在线版一致）")

        example = self._get_example(word)
        
        # 清理例句中多余的英文下划线
        if '_' in example:
            first_occurrence = example.index('_')
            example = example[:first_occurrence+1] + example[first_occurrence+1:].replace('_', '')
        
        en_example, zh_example = example.split('_') if '_' in example else (example, "")
        
        lower_en_example = en_example.lower()
        lower_word = word.english.lower()
        start_index = lower_en_example.find(lower_word)
        if start_index != -1:
            end_index = start_index + len(word.english)
            blanked_part = '_' * len(word.english) + f"({len(word.english)})"
            blanked_example = en_example[:start_index] + blanked_part + en_example[end_index:]
        else:
            blanked_example = en_example
        
        print(f"📖 中文释义: {word.chinese}")
        print(f"📝 例句: {blanked_example}")
        if zh_example:
            print(f"🌏 例句翻译: {zh_example}")
        
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            answer = ""
            prompt = "请输入英文单词（h=显示答案，s=播放语音）: "
            
            # 初始显示
            sys.stdout.write(prompt)
            sys.stdout.flush()
            
            while True:
                try:
                    char = readchar.readchar()
                    if char == '\n':
                        # 输入完成，换行
                        sys.stdout.write('\n')
                        sys.stdout.flush()
                        break
                    elif char == '\x7f':
                        # 退格键
                        if answer:
                            answer = answer[:-1]
                            # 使用ANSI转义序列清除整行
                            sys.stdout.write('\033[2K\r')
                            # 重新显示提示和当前输入
                            sys.stdout.write(f"{prompt}{answer}")
                            sys.stdout.flush()
                    else:
                        # 普通字符输入
                        answer += char
                        # 使用ANSI转义序列清除整行
                        sys.stdout.write('\033[2K\r')
                        # 重新显示提示、当前输入和计数
                        sys.stdout.write(f"{prompt}{answer} ({len(answer)})")
                        sys.stdout.flush()
                except Exception as e:
                    logger.error(f"读取输入时出错: {e}")
                    break
            
            answer = answer.strip().lower()
            if answer == "h":
                print(f"\n📢 正确答案: {word.english}")
                self.record_answer_incorrect(word)
                self._last_practice_wrong_attempts = max(1, attempt + 1)
                return False
            if answer == "s":
                self._text_to_speech(example)
                print("\n")
                continue
            if answer == word.english.lower():
                print("\n✅ 正确！")
                self._text_to_speech(example)
                return True
            self.record_answer_incorrect(word)
            attempt += 1
            self._last_practice_wrong_attempts = attempt
            print(f"\n❌ 错误（剩余尝试次数 {MAX_ATTEMPTS - attempt}）")

        print(f"\n📢 正确答案: {word.english}")
        return False
    
    def daily_review(self) -> None:
        """执行每日复习（主轮 + 错题巩固轮，逻辑与 Web 端一致）。"""
        task_bundle = self.get_today_learning_plan()
        review_list = task_bundle['words']
        task_items = {
            str(item.get('word_key') or ''): item
            for item in task_bundle.get('items') or []
            if isinstance(item, dict)
        }
        if not review_list:
            print("\n🎉 今日没有需要复习的单词！")
            return

        print(f"\n📚 今日需要复习 {len(review_list)} 个单词（第{self.current_review_round + 1}轮）")

        mastered_today = 0
        correct_count = 0
        wrong_count = 0
        remedial_correct = 0
        total_words = len(review_list)
        main_words_total = sum(
            1
            for word in review_list
            if not (
                task_items.get(self.word_state_key(word), {}).get('phase') == 'remedial'
                or self.task_attempt_count(task_items.get(self.word_state_key(word)))
                >= MAX_ATTEMPTS
            )
        )

        wrong_words: List[Word] = []
        for index, word in enumerate(review_list.copy(), start=1):
            print(f"\n⏳ 剩余 {total_words - index + 1} 个单词需要复习")
            task_item = task_items.get(self.word_state_key(word))
            if task_item:
                task_item['exercise_type'] = 'spelling'
            is_task_remedial = bool(
                task_item
                and (
                    task_item.get('phase') == 'remedial'
                    or self.task_attempt_count(task_item) >= MAX_ATTEMPTS
                )
            )
            success = self._practice_word(word, remedial=is_task_remedial)
            wrong_attempts = max(0, int(getattr(self, '_last_practice_wrong_attempts', 0)))
            for _ in range(wrong_attempts):
                wrong_event_id = uuid.uuid4().hex
                if is_task_remedial:
                    claim_review_event(self.get_review_state(word), wrong_event_id)
                else:
                    self.record_mastery_attempt(
                        word,
                        'spelling',
                        False,
                        event_id=wrong_event_id,
                    )
                self.record_daily_task_attempt(task_item, wrong_event_id)

            if success:
                result = self.apply_scored_review_attempt(
                    word,
                    exercise_type='spelling',
                    correct=True,
                    task_item=task_item,
                    event_id=uuid.uuid4().hex,
                    hint_count=wrong_attempts,
                    attempt_number=wrong_attempts + 1,
                    remedial=is_task_remedial,
                    audio_available=False,
                )
                if result['remedial']:
                    remedial_correct += 1
                else:
                    correct_count += 1
                if result['mastered_now']:
                    mastered_today += 1
                    print(f"🎉 已掌握单词: {word.english}")
                else:
                    print(result['message'])
            else:
                if not is_task_remedial:
                    wrong_count += 1
                wrong_words.append(word)
                state = self.get_review_state(word)
                schedule_review(state, 'again', today=self.today)
                word.next_review_date = adaptive_next_review_date(state, self.today)
                if task_item:
                    task_item['phase'] = 'remedial'
                print("⏳ 将进入错题巩固（与在线版一致）")

            self._save_data(backup=False)

        remedial_round = 0
        while wrong_words:
            remedial_round += 1
            print(f"\n{'═'*30}")
            print(f"📌 错题复习 · 第 {remedial_round} 轮（{len(wrong_words)} 个单词）")
            next_wrong: List[Word] = []
            for word in wrong_words:
                task_item = task_items.get(self.word_state_key(word))
                success = self._practice_word(word, remedial=True)
                wrong_attempts = max(0, int(getattr(self, '_last_practice_wrong_attempts', 0)))
                for _ in range(wrong_attempts):
                    wrong_event_id = uuid.uuid4().hex
                    state = self.get_review_state(word)
                    claim_review_event(state, wrong_event_id)
                    attempt_count = self.record_daily_task_attempt(task_item, wrong_event_id)
                    if attempt_count and attempt_count % MAX_ATTEMPTS == 0:
                        schedule_review(state, 'again', today=self.today)
                        word.next_review_date = adaptive_next_review_date(state, self.today)
                if success:
                    remedial_correct += 1
                    result = self.apply_scored_review_attempt(
                        word,
                        exercise_type='spelling',
                        correct=True,
                        task_item=task_item,
                        event_id=uuid.uuid4().hex,
                        remedial=True,
                        audio_available=False,
                    )
                    print(result['message'])
                else:
                    next_wrong.append(word)
                    print("⏳ 本轮未答对，将进入下一轮错题巩固")

                self._save_data(backup=False)
            wrong_words = next_wrong

        accuracy = 0
        if main_words_total > 0:
            accuracy = correct_count / main_words_total * 100

        print("\n📊 今日复习报告:")
        report = PrettyTable()
        report.field_names = ["统计项", "数量"]
        report.add_row(["复习单词总数", total_words])
        report.add_row(["主轮答对数量", correct_count])
        report.add_row(["主轮未答对数量", wrong_count])
        if remedial_round > 0:
            report.add_row(["错题巩固轮次", remedial_round])
            report.add_row(["错题巩固答对", remedial_correct])
        report.add_row(["主轮正确率", f"{accuracy:.1f}%"])
        report.add_row(["新掌握单词", mastered_today])
        report.add_row(["当前复习轮次", f"第{self.current_review_round + 1}轮"])
        report.add_row(["当前进度", f"{len(self.mastered_words)} 已掌握 / {len(self.all_words)} 待复习"])
        print(report)

        self._save_data()
    
    def _check_and_advance_round(self) -> None:
        """检查并推进复习轮次"""
        current_round_words = [w for w in self.all_words if w.review_round == self.current_review_round]
        
        if not current_round_words:
            if self.current_review_round < self.config.MAX_REVIEW_ROUND:
                self.current_review_round += 1
                print(f"\n🎯 进入第{self.current_review_round + 1}轮复习！")
                
                for word in self.all_words:
                    if word.review_round < self.current_review_round:
                        word.review_round = self.current_review_round
                        delta_days = self._calculate_review_days(word.success_count)
                        word.next_review_date = self.today + timedelta(days=delta_days)
    
    def add_words(self, words: list) -> dict:
        """批量添加单词（与已有词库去重，英文不区分大小写；同批重复只保留第一条）。"""
        existing_words = {w.english.lower() for w in self.all_words + self.mastered_words}
        new_words: List[Word] = []
        skipped_duplicate = 0
        skipped_invalid = 0

        for pair in words:
            if not isinstance(pair, (tuple, list)) or len(pair) < 2:
                skipped_invalid += 1
                continue
            en = str(pair[0]).strip()[:500]
            zh = str(pair[1]).strip()[:500]
            if not en or not zh:
                skipped_invalid += 1
                continue
            key = en.lower()
            if key in existing_words:
                skipped_duplicate += 1
                continue
            new_words.append(Word(en, zh))
            existing_words.add(key)

        if new_words:
            self.all_words.extend(new_words)
            self._save_data()
            logger.info(
                "成功添加 %s 个新单词（跳过重复 %s，无效 %s）",
                len(new_words),
                skipped_duplicate,
                skipped_invalid,
            )
        elif skipped_duplicate or skipped_invalid:
            logger.info(
                "未添加新词：跳过重复 %s，无效 %s",
                skipped_duplicate,
                skipped_invalid,
            )

        return {
            'added': len(new_words),
            'skipped_duplicate': skipped_duplicate,
            'skipped_invalid': skipped_invalid,
        }

    def add_words_from_dicts(self, items: List[dict]) -> dict:
        """从学习数据格式的字典列表加入待复习词（与已有英文去重，大小写不敏感）。"""
        existing = {w.english.lower() for w in self.all_words + self.mastered_words}
        added: List[Word] = []
        skipped_duplicate = 0
        skipped_invalid = 0
        skipped_duplicate_words: List[str] = []
        _dup_cap = 80

        for raw in items:
            if not isinstance(raw, dict):
                skipped_invalid += 1
                continue
            en = str(raw.get('english', '')).strip()[:500]
            zh = str(raw.get('chinese', '')).strip()[:500]
            if not en or not zh:
                skipped_invalid += 1
                continue
            key = en.lower()
            if key in existing:
                skipped_duplicate += 1
                if len(skipped_duplicate_words) < _dup_cap:
                    skipped_duplicate_words.append(en)
                continue
            try:
                payload = dict(raw)
                payload['english'] = en
                payload['chinese'] = zh
                ex = payload.get('example')
                if isinstance(ex, str) and len(ex) > 4000:
                    payload['example'] = ex[:4000]
                word = Word.from_dict(payload)
            except (TypeError, ValueError, KeyError):
                skipped_invalid += 1
                continue
            added.append(word)
            existing.add(key)

        if added:
            self.all_words.extend(added)
            self._save_data()
            logger.info(
                "成功从 JSON 添加 %s 个新单词（跳过重复 %s，无效 %s）",
                len(added),
                skipped_duplicate,
                skipped_invalid,
            )

        return {
            'added': len(added),
            'skipped_duplicate': skipped_duplicate,
            'skipped_invalid': skipped_invalid,
            'skipped_duplicate_words': skipped_duplicate_words,
        }


class ReciterCLI:
    """用户界面"""
    
    def __init__(self):
        self.reciter = WordReciter()
    
    def main_menu(self) -> None:
        """主菜单"""
        while True:
            print("\n"+ "="*30)
            print("  智能单词背诵系统（离线版）")
            print("="*30)
            print("1. 开始今日复习")
            print("2. 查看学习进度")
            print("3. 导入单词文件")
            print("4. 查看已掌握词汇")
            print("5. 复习已掌握词汇")
            print("6. 退出系统")
            
            try:
                choice = input("请选择操作: ").strip()
                
                if choice == '1':
                    self.reciter.daily_review()
                elif choice == '2':
                    self.reciter.show_status()
                elif choice == '3':
                    self._import_file()
                elif choice == '4':
                    self.reciter.show_mastered_words()
                elif choice == '5':
                    self.reciter.review_mastered_words()
                elif choice == '6':
                    print("👋 再见！")
                    break
                else:
                    print("⚠️ 无效的选项")
            except KeyboardInterrupt:
                print("\n\n👋 已退出")
                break
            except Exception as e:
                print(f"⚠️ 发生错误: {e}")
    
    def _import_file(self) -> None:
        """导入单词文件"""
        path = input(f"输入文件路径（默认{self.reciter.config.WORD_FILE}）: ").strip() or self.reciter.config.WORD_FILE
        try:
            with open(path, encoding='utf-8') as f:
                words = [line.strip().split(',', 1) for line in f if ',' in line]
                self.reciter.add_words(words)
        except FileNotFoundError:
            print(f"⚠️ 文件不存在: {path}")
        except Exception as e:
            print(f"⚠️ 导入失败: {str(e)}")


if __name__ == "__main__":
    cli = ReciterCLI()
    cli.main_menu()
