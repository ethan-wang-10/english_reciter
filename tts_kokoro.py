"""Optional local CPU Kokoro worker core; importing this module loads no model.

Weights and voices must already exist locally. This module never downloads them.
The web process talks to scripts/kokoro_worker.py and keeps its existing fallback.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import importlib.metadata
import io
import json
import logging
import os
from pathlib import Path
import re
import struct
import tempfile
import threading
import time
import unicodedata
import wave


MAX_PHONEMES = 510
MAX_WAV_BYTES = 12 * 1024 * 1024
SPEED = 1.0
CACHE_SCHEMA = 'kokoro-v1.1-zh-checked-phonemes-v3'


class KokoroError(ValueError):
    def __init__(self, message='Local speech is unavailable.', status=503, code='unavailable'):
        super().__init__(message)
        self.status, self.code = status, code


def _integer(environ, name, default, minimum, maximum):
    raw = environ.get(name, str(default))
    try:
        result = int(raw)
    except (ValueError, TypeError):
        raise ValueError(name + ' must be an integer') from None
    if not minimum <= result <= maximum:
        raise ValueError(name + ' is outside its allowed range')
    return result


@dataclass(frozen=True)
class KokoroConfig:
    enabled: bool = False
    model_path: str = ''
    voices_path: str = ''
    cache_dir: Path = field(default_factory=lambda: Path('user_data_simple/_shared/kokoro_cache'))
    zh_voice: str = 'zf_001'
    en_voice: str = 'af_maple'
    zh_voices: tuple[str, ...] = ('zf_001',)
    en_voices: tuple[str, ...] = ('af_maple',)
    queue_size: int = 400
    max_files: int = 2000
    max_bytes: int = 256 * 1024 * 1024
    max_age_days: int = 30
    synthesis_timeout: float = 30.0
    espeak_library: str = ''
    espeak_data: str = ''
    vocab_path: str = ''

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        zh, en = env.get('KOKORO_ZH_VOICE', 'zf_001'), env.get('KOKORO_EN_VOICE', 'af_maple')
        def allowed(name, default):
            values = tuple(value.strip() for value in env.get(name, default).split(',') if value.strip())
            if not values or len(values) > 32 or any(not re.fullmatch(r'[a-z][a-z0-9_]{1,63}', value) for value in values):
                raise ValueError(name + ' contains invalid voice names')
            return tuple(dict.fromkeys(values))
        return cls(
            enabled=env.get('KOKORO_EXPERIMENT_ENABLED', '').lower() in ('1', 'true', 'yes'),
            model_path=env.get('KOKORO_MODEL', ''), voices_path=env.get('KOKORO_VOICES', ''),
            cache_dir=Path(env.get('KOKORO_CACHE_DIR', 'user_data_simple/_shared/kokoro_cache')).expanduser(),
            zh_voice=zh, en_voice=en, zh_voices=allowed('KOKORO_ZH_VOICES', zh), en_voices=allowed('KOKORO_EN_VOICES', en),
            queue_size=_integer(env, 'KOKORO_QUEUE_SIZE', 400, 1, 400),
            max_files=_integer(env, 'KOKORO_CACHE_MAX_FILES', 2000, 1, 50000),
            max_bytes=_integer(env, 'KOKORO_CACHE_MAX_BYTES', 256 * 1024 * 1024, 1024, 4 * 1024 * 1024 * 1024),
            max_age_days=_integer(env, 'KOKORO_CACHE_MAX_AGE_DAYS', 30, 1, 365),
            synthesis_timeout=float(_integer(env, 'KOKORO_SYNTHESIS_TIMEOUT', 30, 1, 120)),
            espeak_library=env.get('KOKORO_ESPEAK_LIBRARY', ''), espeak_data=env.get('KOKORO_ESPEAK_DATA', ''),
            vocab_path=env.get('KOKORO_VOCAB', ''),
        )

    def ready(self):
        return bool(self.enabled and self.model_path and self.voices_path
                    and Path(self.model_path).is_file() and Path(self.voices_path).is_file()
                    and (not self.vocab_path or Path(self.vocab_path).is_file())
                    and self.zh_voice in self.zh_voices and self.en_voice in self.en_voices)


def load_vocab(path):
    """Read an explicit local export vocabulary, never fetch or infer one."""
    try:
        source = Path(path)
        if not source.is_file() or source.stat().st_size > 1_000_000:
            raise ValueError('Vocabulary is missing or too large')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate vocabulary JSON key')
                result[key] = value
            return result
        value = json.loads(source.read_text(encoding='utf-8'), object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError('Vocabulary must be an object')
        vocab = value.get('vocab') if 'vocab' in value else value.get('model', {}).get('vocab')
        if (not isinstance(vocab, dict) or not 1 <= len(vocab) <= 4096
                or any(not isinstance(char, str) or len(char) != 1 or unicodedata.category(char) in ('Cc', 'Cf', 'Cs')
                       or type(token) is not int or not 0 <= token <= 65535 for char, token in vocab.items())
                or len(set(vocab.values())) != len(vocab)):
            raise ValueError('Vocabulary must map individual characters to unique integer IDs')
        return {'vocab': vocab}
    except (OSError, ValueError, TypeError, AttributeError):
        raise KokoroError('The local model vocabulary is invalid.', code='vocab') from None


def validate_item(language, text, voice, config):
    if language not in ('zh', 'en') or not isinstance(language, str):
        raise KokoroError('Language must be zh or en.', 400, 'invalid')
    if not isinstance(text, str) or any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') for char in text):
        raise KokoroError('Text contains invalid characters.', 400, 'invalid')
    text = ' '.join(unicodedata.normalize('NFC', text).split())
    maximum = 100 if language == 'zh' else 500
    if not text or len(text) > maximum or not any(unicodedata.category(char).startswith(('L', 'N')) for char in text):
        raise KokoroError('Text is empty or exceeds its language limit.', 400, 'invalid')
    chosen = (config.zh_voice if language == 'zh' else config.en_voice) if voice is None else voice
    choices = config.zh_voices if language == 'zh' else config.en_voices
    if not isinstance(chosen, str) or chosen not in choices:
        raise KokoroError('Voice is not allowed for this language.', 400, 'invalid')
    return {'language': language, 'text': text, 'voice': chosen}


def split_phonemes(phonemes, maximum=MAX_PHONEMES):
    """Partition the complete phoneme string; never slice away its tail."""
    if not isinstance(phonemes, str) or not phonemes.strip():
        raise KokoroError('No usable phonemes were generated.', code='phonemes')
    if len(phonemes) > 20000:
        raise KokoroError('Phoneme expansion exceeds the safe bound.', code='phonemes')
    pieces, remaining = [], phonemes
    while len(remaining) > maximum:
        window = remaining[:maximum]
        positions = [window.rfind(mark) for mark in '.!?;,: ']
        cut = max(positions) + 1
        if cut < maximum // 2:
            cut = maximum
        pieces.append(remaining[:cut]); remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    if ''.join(pieces) != phonemes or any(len(piece) > maximum for piece in pieces):
        raise KokoroError('Phoneme coverage could not be verified.', code='phonemes')
    return pieces


def chinese_phonemes(chinese, text, mapping):
    """Keep a narrowly phrased pronunciation hint's final character in context.

    Misaki 0.9.4 can read the repeated isolated character differently from the
    phrase in “单薄的薄”. Reuse only that phrase's already-initialized frontend;
    do not mutate pypinyin's process-global character dictionaries.
    """
    match = re.fullmatch(r'([\u4e00-\u9fff]{2,8})的([\u4e00-\u9fff])', text)
    if match is None or match[1].count(match[2]) != 1:
        return chinese(text)[0]
    phrase, target = match.groups()
    try:
        initials, finals = chinese.frontend._get_initials_finals(phrase)
        if len(initials) != len(phrase) or len(finals) != len(phrase):
            raise ValueError('Unaligned phrase pronunciation')
        index = phrase.index(target)
        initial, final = initials[index], finals[index]
        if not isinstance(initial, str) or not isinstance(final, str) or not re.fullmatch(r'[a-z]+[1-5]', final):
            raise ValueError('Invalid phrase pronunciation')
        # This is the pinned frontend's own phoneme-to-ZH_MAP transformation,
        # applied to one aligned character rather than a guessed Pinyin alias.
        pieces = '_'.join(value for value in (initial, final) if value).replace('_eR', '_er').replace('R', '_R')
        pieces = re.sub(r'(?=\d)', '_', pieces).split('_')
        if not pieces or any(piece not in mapping for piece in pieces):
            raise ValueError('Unrepresentable phrase pronunciation')
        ending = ''.join(mapping[piece] for piece in pieces)
        prefix = chinese(phrase + '的')[0]
        if not prefix or not ending or '❓' in prefix or '❓' in ending:
            raise ValueError('Incomplete phrase pronunciation')
        return prefix + ' ' + ending
    except Exception:
        raise KokoroError('The pronunciation hint could not be mapped reliably.', code='phonemes') from None


def normalize_english_phonemes(phonemes):
    """Expand two standard rhotic IPA vowels without discarding their r sound.

    eSpeak emits the single symbols ɚ/ɝ, while this Kokoro export represents
    them as schwa/open-mid vowel plus the existing approximant ɹ token.
    Every resulting character still has to pass the strict model vocabulary.
    """
    return phonemes.replace('ɚ', 'əɹ').replace('ɝ', 'ɜɹ')


def valid_wav(content):
    if (not isinstance(content, bytes) or not 44 < len(content) <= MAX_WAV_BYTES
            or content[:4] != b'RIFF' or content[8:12] != b'WAVE'
            or struct.unpack('<I', content[4:8])[0] + 8 != len(content)):
        return False
    try:
        with wave.open(io.BytesIO(content), 'rb') as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 24000 or source.getcomptype() != 'NONE':
                return False
            frames = source.getnframes()
            data = source.readframes(frames + 1)
            return 0 < frames <= 24000 * 240 and len(data) == frames * 2 and any(data)
    except (wave.Error, EOFError, ValueError, struct.error):
        return False


@contextmanager
def _file_lock(path, timeout=0.0):
    """Small bounded POSIX locks shared with other explicit worker processes."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    started, acquired = time.monotonic(), False
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB); acquired = True; break
            except BlockingIOError:
                if time.monotonic() - started >= timeout:
                    raise KokoroError('Local speech is busy.', code='busy') from None
                time.sleep(0.01)
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class KokoroEngine:
    """One lazy ORT session, one synthesis at a time, persistent validated WAVs."""
    def __init__(self, config=None, *, runtime_factory=None, versions=None):
        self.config = config or KokoroConfig.from_env()
        self._runtime_factory = runtime_factory
        self._runtime = None
        self._runtime_identity = None
        self._model_lock = threading.Lock()
        self._synthesis_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._fingerprints = {}
        self._versions = versions or {name: self._version(name) for name in (
            'kokoro-onnx', 'misaki', 'phonemizer', 'espeakng-loader', 'onnxruntime',
            'pypinyin', 'pypinyin-dict', 'jieba', 'cn2an',
        )}

    @staticmethod
    def _version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return 'missing'

    def health(self):
        return {'engine': 'kokoro', 'enabled': self.config.ready(), 'ready': self.config.ready(),
                'loaded': self._runtime is not None, 'prepare': True, 'languages': ['zh', 'en'],
                'voices': {'zh': list(self.config.zh_voices), 'en': list(self.config.en_voices)},
                'speed': SPEED, 'cpu_threads': 1}

    def item(self, language, text, voice=None):
        return validate_item(language, text, voice, self.config)

    def _fingerprint(self, value):
        path = Path(value).resolve(); stat = path.stat()
        identity = (str(path), stat.st_size, stat.st_mtime_ns)
        cached = self._fingerprints.get(str(path))
        if cached is None or cached[0] != identity:
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            self._fingerprints[str(path)] = (identity, digest)
        return self._fingerprints[str(path)][1]

    def key(self, item):
        if not self.config.ready():
            raise KokoroError('Local speech is disabled or its local files are missing.')
        try:
            library = self.config.espeak_library or os.getenv('PHONEMIZER_ESPEAK_LIBRARY', '')
            identity = {'schema': CACHE_SCHEMA, 'model': self._fingerprint(self.config.model_path),
                        'voices': self._fingerprint(self.config.voices_path), 'versions': self._versions,
                        'vocab': self._fingerprint(self.config.vocab_path) if self.config.vocab_path else None,
                        'espeak_library': {'path': str(Path(library).resolve()), 'sha256': self._fingerprint(library)} if library else None,
                        'espeak_data': str(Path(self.config.espeak_data).resolve()) if self.config.espeak_data else None,
                        'language': item['language'], 'voice': item['voice'], 'speed': SPEED, 'text': item['text']}
            return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        except OSError:
            raise KokoroError() from None

    def _cache_path(self, key):
        return self.config.cache_dir / (key + '.wav')

    def cached(self, key):
        path = self._cache_path(key)
        try:
            if path.is_symlink() or not path.is_file() or not 44 < path.stat().st_size <= MAX_WAV_BYTES:
                return None
            content = path.read_bytes()
            if not valid_wav(content):
                return None
            if time.time() - path.stat().st_mtime > self.config.max_age_days * 86400:
                return None
            os.utime(path, None)
            return content
        except OSError:
            return None

    def _load(self):
        if self._runtime is not None:
            return self._runtime
        with self._model_lock:
            if self._runtime is None:
                self._runtime = self._runtime_factory() if self._runtime_factory else self._load_runtime()
                self._runtime_identity = self._asset_identity()
        return self._runtime

    def _asset_identity(self):
        return (self._fingerprint(self.config.model_path), self._fingerprint(self.config.voices_path),
                self._fingerprint(self.config.vocab_path) if self.config.vocab_path else None)

    def _load_runtime(self):
        vocab_options = {'vocab_config': load_vocab(self.config.vocab_path)} if self.config.vocab_path else {}
        import numpy as np
        import onnxruntime as ort
        import phonemizer
        from kokoro_onnx import Kokoro
        from kokoro_onnx.config import EspeakConfig
        from misaki.zh import ZHG2P
        from misaki.zh_frontend import ZH_MAP
        logging.getLogger('kokoro_onnx').setLevel(logging.CRITICAL)
        pronunciation_log = logging.getLogger('kokoro_worker.phonemizer')
        pronunciation_log.handlers = [logging.NullHandler()]
        pronunciation_log.propagate = False; pronunciation_log.setLevel(logging.CRITICAL)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1; options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(str(Path(self.config.model_path).resolve()), sess_options=options,
                                       providers=['CPUExecutionProvider'])
        espeak = EspeakConfig(lib_path=self.config.espeak_library or None, data_path=self.config.espeak_data or None)
        model = Kokoro.from_session(session, str(Path(self.config.voices_path).resolve()), espeak_config=espeak, **vocab_options)
        available = set(model.get_voices())
        if not set(self.config.zh_voices + self.config.en_voices) <= available:
            raise KokoroError('A configured voice is missing from the local voice pack.', code='voice')
        # Do not use Tokenizer.phonemize: it silently filters unknown phonemes.
        def english(text):
            phones = phonemizer.phonemize(text, language='en-us', preserve_punctuation=True, with_stress=True, logger=pronunciation_log)
            return normalize_english_phonemes(phones)
        chinese = ZHG2P(version='1.1', en_callable=english)
        vocab = model.tokenizer.vocab
        def render(item, deadline):
            phones = english(item['text']) if item['language'] == 'en' else chinese_phonemes(chinese, item['text'], ZH_MAP)
            phones = ' '.join(phones.split())
            if not phones or any(char not in vocab for char in phones) or '❓' in phones:
                raise KokoroError('Complete pronunciation could not be verified.', code='phonemes')
            arrays = []
            for part in split_phonemes(phones):
                if time.monotonic() >= deadline:
                    raise KokoroError('Local speech exceeded its synthesis time limit.', code='timeout')
                samples, sample_rate = model.create(part, voice=item['voice'], speed=SPEED, lang='en-us', is_phonemes=True, trim=False)
                samples = np.asarray(samples)
                if (sample_rate != 24000 or samples.ndim != 1 or not samples.size
                        or not np.isfinite(samples).all() or not np.any(np.abs(samples) > 1e-8)):
                    raise KokoroError('The speech engine returned invalid audio.', code='audio')
                arrays.append(samples)
            if time.monotonic() >= deadline:
                raise KokoroError('Local speech exceeded its synthesis time limit.', code='timeout')
            samples = np.concatenate(arrays)
            if len(samples) > 24000 * 240:
                raise KokoroError('Generated audio exceeds the safe duration.', code='audio')
            pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes()
            content = io.BytesIO()
            with wave.open(content, 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000); output.writeframes(pcm)
            return content.getvalue()
        return render

    def cleanup(self):
        if not self.config.enabled:
            return
        if not self._cleanup_lock.acquire(False):
            return
        try:
            directory = self.config.cache_dir
            if not directory.is_dir():
                return
            now, rows = time.time(), []
            for path in directory.glob('.kokoro-*.tmp'):
                try:
                    if now - path.stat().st_mtime > 3600:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
            for path in directory.glob('*.wav'):
                try:
                    stat = path.stat()
                    if path.is_symlink() or now - stat.st_mtime > self.config.max_age_days * 86400:
                        path.unlink(missing_ok=True); continue
                    rows.append((stat.st_mtime, stat.st_size, path))
                except OSError:
                    continue
            rows.sort(); total = sum(size for _, size, _ in rows)
            while rows and (len(rows) > self.config.max_files or total > self.config.max_bytes):
                _, size, path = rows.pop(0)
                try:
                    path.unlink(missing_ok=True); total -= size
                except OSError:
                    continue
        finally:
            self._cleanup_lock.release()

    def _save_cache(self, key, content):
        directory = self.config.cache_dir
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix='.kokoro-', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.chmod(name, 0o600); os.replace(name, self._cache_path(key))
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass
        self.cleanup()

    def audio(self, language, text, voice=None):
        item = self.item(language, text, voice); key = self.key(item); started = time.monotonic()
        hit = self.cached(key)
        if hit is not None:
            return hit, {'cache_hit': True, 'engine': 'kokoro', 'duration_ms': (time.monotonic() - started) * 1000}
        if not self._synthesis_lock.acquire(False):
            raise KokoroError('Local speech is busy.', code='busy')
        try:
            locks = self.config.cache_dir / '.locks'
            with _file_lock(locks / ('key-' + key[:2] + '.lock')), _file_lock(locks / 'synthesis.lock'):
                hit = self.cached(key)
                if hit is not None:
                    return hit, {'cache_hit': True, 'engine': 'kokoro', 'duration_ms': (time.monotonic() - started) * 1000}
                deadline = time.monotonic() + self.config.synthesis_timeout
                render = self._load()
                if self._runtime_identity != self._asset_identity():
                    raise KokoroError('Local speech assets changed; restart the worker.', code='assets_changed')
                content = render(item, deadline)
                if time.monotonic() >= deadline:
                    raise KokoroError('Local speech exceeded its synthesis time limit.', code='timeout')
                if not valid_wav(content):
                    raise KokoroError('The speech engine returned an invalid WAV.', code='audio')
                self._save_cache(key, content)
                return content, {'cache_hit': False, 'engine': 'kokoro', 'duration_ms': (time.monotonic() - started) * 1000}
        except KokoroError:
            raise
        except Exception:
            raise KokoroError() from None
        finally:
            self._synthesis_lock.release()


class PreparationQueue:
    """A bounded, deduplicated single background thread; no request text in status."""
    def __init__(self, engine, *, start=True):
        self.engine = engine
        self._condition = threading.Condition()
        self._queue = deque()
        self._keys = set()
        self._active = None
        self._completed = self._failed = 0
        self._stopped = False
        self._thread = None
        if start:
            self.start()

    def start(self):
        with self._condition:
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name='kokoro-prepare', daemon=True)
                self._thread.start()

    def status(self):
        with self._condition:
            return {'pending': len(self._queue), 'active': self._active is not None,
                    'completed': self._completed, 'failed': self._failed}

    def prepare(self, language, texts, voice=None):
        if not isinstance(texts, list) or not 0 <= len(texts) <= 400:
            raise KokoroError('Prepare accepts at most 400 text items.', 400, 'invalid')
        # Validate every entry before scheduling any of them.
        items = [self.engine.item(language, text, voice) for text in texts]
        keyed = [(self.engine.key(item), item) for item in items]
        accepted = cached = pending = failed = 0
        with self._condition:
            if self._stopped:
                raise KokoroError('Preparation is stopped.')
            for key, item in keyed:
                if self.engine.cached(key) is not None:
                    cached += 1
                elif key in self._keys:
                    pending += 1
                elif len(self._queue) + (self._active is not None) >= self.engine.config.queue_size:
                    failed += 1
                else:
                    self._queue.append((key, item)); self._keys.add(key); accepted += 1
            self._condition.notify()
        return {'accepted': accepted, 'cached': cached, 'pending': pending, 'failed': failed, 'count': len(texts)}

    def _work(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self._stopped, timeout=60)
                if self._stopped:
                    return
                if not self._queue:
                    self.engine.cleanup(); continue
                key, item = self._queue.popleft(); self._active = key
            try:
                self.engine.audio(**item)
            except Exception:
                with self._condition:
                    self._failed += 1
            else:
                with self._condition:
                    self._completed += 1
            finally:
                with self._condition:
                    self._keys.discard(key); self._active = None; self._condition.notify_all()

    def close(self):
        with self._condition:
            self._stopped = True; self._queue.clear(); self._keys.clear(); self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=1)
