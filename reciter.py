import os
import json
import random
import shutil
import platform
from collections import defaultdict
from typing import Dict, List, Optional
from datetime import date, timedelta, datetime
from pathlib import Path
from prettytable import PrettyTable
import readchar
import logging

# 常量定义
MAX_ATTEMPTS = 3  # 最大尝试次数

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
            "review_interval_days": [1, 2, 4, 7, 15, 30, 60, 90],
            "backup_enabled": True,
            "backup_interval_days": 7,
            "max_backups": 10,
            "language": "zh",
            "log_level": "INFO"
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
        self.BACKUP_ENABLED = default_config["backup_enabled"]
        self.BACKUP_INTERVAL_DAYS = default_config["backup_interval_days"]
        self.MAX_BACKUPS = default_config["max_backups"]
        self.LANGUAGE = default_config["language"]
        
        log_level = getattr(logging, default_config.get("log_level", "INFO"))
        logger.setLevel(log_level)


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
        self.next_review_date = next_review_date or date.today()
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
        """从字典创建对象"""
        data['next_review_date'] = date.fromisoformat(data['next_review_date'])
        data.setdefault('review_round', 0)
        data.setdefault('review_count', 0)
        return cls(**data)


class WordRepository:
    """单词数据访问层"""
    
    def __init__(self, config: Config):
        self.config = config
    
    def load_data(self) -> tuple[list[Word], list[Word]]:
        """
        加载学习数据
        
        Returns:
            (all_words, mastered_words)
        """
        try:
            with open(self.config.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                all_words = [Word.from_dict(w) for w in data['all_words']]
                mastered_words = [Word.from_dict(w) for w in data['mastered_words']]
                
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
            return [], []
        except json.JSONDecodeError as e:
            logger.error(f"数据文件 {self.config.DATA_FILE} 格式错误: {e}")
            logger.warning("将重置为初始状态")
            return [], []
        except Exception as e:
            logger.error(f"加载数据时发生未知错误: {e}")
            return [], []
    
    def save_data(self, all_words: list[Word], mastered_words: list[Word]) -> None:
        """保存学习数据"""
        try:
            data = {
                'all_words': [w.to_dict() for w in all_words],
                'mastered_words': [w.to_dict() for w in mastered_words]
            }
            with open(self.config.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug("数据保存成功")
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
    
    def backup_data(self) -> Optional[str]:
        """
        备份数据文件
        
        Returns:
            备份文件路径，失败返回 None
        """
        if not self.config.BACKUP_ENABLED:
            return None
        
        try:
            now = datetime.now()
            backup_dir = Path("backups")
            backup_dir.mkdir(exist_ok=True)
            
            # 添加时间戳避免同一天多次备份冲突
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            backup_file = backup_dir / f"learning_data_backup_{timestamp}.json"
            
            shutil.copy2(self.config.DATA_FILE, backup_file)
            
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
        self.today = date.today()
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
                last_backup_date = date.fromtimestamp(latest_backup.stat().st_mtime)
                days_since_backup = (self.today - last_backup_date).days
                if days_since_backup < self.config.BACKUP_INTERVAL_DAYS:
                    logger.debug(f"距离上次备份仅 {days_since_backup} 天，跳过备份")
                    return
        
        self.repository.backup_data()
    
    def _load_data(self) -> None:
        """加载学习数据"""
        self.all_words, self.mastered_words = self.repository.load_data()
    
    def _save_data(self, backup: bool = True) -> None:
        """保存学习数据"""
        self.repository.save_data(self.all_words, self.mastered_words)
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
        """处理过期单词"""
        overdue_count = 0
        for word in self.all_words:
            if word.next_review_date < self.today:
                word.next_review_date = self.today
                overdue_count += 1
        
        if overdue_count > 0:
            logger.info(f"更新了 {overdue_count} 个过期单词的复习日期")
    
    def _update_review_round(self) -> None:
        """更新复习轮次"""
        if self.all_words:
            min_review_round = min(word.review_round for word in self.all_words)
            self.current_review_round = min_review_round
        else:
            self.current_review_round = 0
        
        print(f"📊 当前复习轮次: 第{self.current_review_round + 1}轮")
    
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
            current_round_words.sort(key=lambda w: w.review_count)
            return current_round_words
        
        min_round = min(words_by_round.keys())
        min_round_words = words_by_round[min_round]
        min_round_words.sort(key=lambda w: w.review_count)
        return min_round_words
    
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
        """文本转语音（跨平台支持）
        
        - macOS: 使用系统 say 命令
        - Linux/Windows: 如果 say 命令不存在则静默跳过
        """
        if not self.config.TTS_ENABLED:
            return
        
        # 检查 say 命令是否可用
        if shutil.which('say') is None:
            logger.debug("say 命令不可用，跳过语音播放")
            return
        
        try:
            if not text or not isinstance(text, str):
                logger.warning("无效的文本输入")
                return
            
            en_text = text.split('_')[0]
            if not en_text:
                logger.warning("无法提取有效的英文文本")
                return
            
            # 使用 say 命令，跨平台忽略输出和错误
            if platform.system() == 'Windows':
                os.system(f'say "{en_text}" > NUL 2>&1')
            else:
                os.system(f'say "{en_text}" > /dev/null 2>&1')
        except Exception as e:
            logger.error(f"语音生成失败: {e}")
    
    def _practice_word(self, word: Word) -> bool:
        """单个单词练习流程"""
        print(f"\n{'━'*30}")
        print(f"🔔 当前进度: {word.success_count}/{self.config.MAX_SUCCESS_COUNT}")
        
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
            print("请输入英文单词（h=显示答案，s=播放语音）: ", end='', flush=True)
            
            while True:
                try:
                    char = readchar.readchar()
                    if char == '\n':
                        break
                    elif char == '\x7f':
                        if answer:
                            answer = answer[:-1]
                            print('\b \b', end='', flush=True)
                    else:
                        answer += char
                        print(char, end='', flush=True)
                    

                except Exception as e:
                    logger.error(f"读取输入时出错: {e}")
                    break
            
            answer = answer.strip().lower()
            if answer == "h":
                print(f"\n📢 正确答案: {word.english}")
                return False
            if answer == "s":
                self._text_to_speech(example)
                print("\n")
                continue
            if answer == word.english.lower():
                print("\n✅ 正确！")
                self._text_to_speech(example)
                return True
            attempt += 1
            print(f"\n❌ 错误（剩余尝试次数 {MAX_ATTEMPTS - attempt}）")
        
        print(f"\n📢 正确答案: {word.english}")
        return False
    
    def daily_review(self) -> None:
        """执行每日复习"""
        review_list = self._get_today_review_list()
        if not review_list:
            print("\n🎉 今日没有需要复习的单词！")
            return
        
        print(f"\n📚 今日需要复习 {len(review_list)} 个单词（第{self.current_review_round + 1}轮）")
        
        mastered_today = 0
        correct_count = 0
        wrong_count = 0
        total_words = len(review_list)
        
        review_list.sort(key=lambda w: w.review_count)
        
        for index, word in enumerate(review_list.copy(), start=1):
            print(f"\n⏳ 剩余 {total_words - index + 1} 个单词需要复习")
            success = self._practice_word(word)
            
            if success:
                correct_count += 1
                word.success_count += 1
                word.review_count += 1
                
                if word.success_count >= self.config.MAX_SUCCESS_COUNT:
                    self.mastered_words.append(word)
                    self.all_words.remove(word)
                    mastered_today += 1
                    print(f"🎉 已掌握单词: {word.english}")
                else:
                    delta_days = self._calculate_review_days(word.success_count)
                    word.next_review_date = self.today + timedelta(days=delta_days)
                    print(f"⏱ 下次复习: {word.next_review_date} (+{delta_days}天，第{word.success_count}次成功)")
            else:
                wrong_count += 1
                word.review_count += 1
                print("⏳ 保持原复习计划")
            
            self._check_and_advance_round()
        
        accuracy = 0
        if total_words > 0:
            accuracy = correct_count / total_words * 100
        
        print("\n📊 今日复习报告:")
        report = PrettyTable()
        report.field_names = ["统计项", "数量"]
        report.add_row(["复习单词总数", total_words])
        report.add_row(["正确复习数量", correct_count])
        report.add_row(["错误复习数量", wrong_count])
        report.add_row(["复习正确率", f"{accuracy:.1f}%"])
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
    
    def add_words(self, words: list) -> None:
        """批量添加单词"""
        existing_words = {w.english.lower() for w in self.all_words + self.mastered_words}
        new_words = []
        
        for en, zh in words:
            if en.lower() not in existing_words:
                new_words.append(Word(en, zh))
                existing_words.add(en.lower())
        
        self.all_words.extend(new_words)
        self._save_data()
        print(f"✅ 成功添加 {len(new_words)} 个新单词")


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
