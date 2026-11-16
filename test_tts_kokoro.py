"""Optional worker tests run without model downloads, ORT or a GPU."""
from dataclasses import replace
from http.client import HTTPConnection
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time
from types import SimpleNamespace
import wave

import pytest

from tts_kokoro import KokoroConfig, KokoroEngine, KokoroError, PreparationQueue, chinese_phonemes, load_vocab, normalize_english_phonemes, split_phonemes, valid_wav


def wav(frames=240):
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(24000)
        target.writeframes(struct.pack('<h', 1000) * frames)
    return output.getvalue()


@pytest.fixture
def config(tmp_path):
    model, voices = tmp_path / 'local.onnx', tmp_path / 'voices.bin'
    model.write_bytes(b'local-model-v1'); voices.write_bytes(b'local-voices-v1')
    return KokoroConfig(enabled=True, model_path=str(model), voices_path=str(voices), cache_dir=tmp_path / 'cache')


def engine(config, calls=None, versions=None):
    calls = [] if calls is None else calls
    def load():
        calls.append('load')
        def render(item, deadline):
            calls.append(item)
            return wav()
        return render
    return KokoroEngine(config, runtime_factory=load, versions=versions or {'test-g2p': '1'})


def test_disabled_import_and_health_do_not_load_runtime_or_create_files(config):
    calls = []
    disabled = engine(replace(config, enabled=False), calls)
    assert disabled.health()['enabled'] is False and disabled.health()['loaded'] is False
    with pytest.raises(KokoroError) as caught:
        disabled.audio('zh', '薄雾')
    assert caught.value.status == 503 and calls == [] and not config.cache_dir.exists()
    ready = engine(config, calls)
    assert ready.health()['ready'] and ready.health()['prepare'] and not ready.health()['loaded']
    assert 'model_path' not in ready.health() and calls == []


@pytest.mark.parametrize('language,text,voice', [
    ('fr', 'bonjour', None), ('en', '', None), ('en', 'A' * 501, None), ('zh', '字' * 101, None),
    ('en', 'a\nb', None), ('zh', '薄\u200b雾', None), ('en', '!!!', None), ('en', None, None),
    ('en', 'Hello', 'zf_001'), ('zh', '你好', 'af_maple'), ('en', 'Hello', '../../voice'),
])
def test_validation_rejects_invalid_items_without_loading_or_truncating(config, language, text, voice):
    calls = []; worker = engine(config, calls)
    with pytest.raises(KokoroError) as caught:
        worker.audio(language, text, voice)
    assert caught.value.status == 400 and calls == [] and not config.cache_dir.exists()


def test_full_text_is_passed_to_one_lazy_runtime_and_cache_hit_needs_no_runtime(config):
    text = "I won't " + 'a' * 484 + ' forget.'
    assert len(text) == 500
    calls = []; worker = engine(config, calls)
    first, metadata = worker.audio('en', text)
    assert valid_wav(first) and metadata['cache_hit'] is False
    assert calls == ['load', {'language': 'en', 'text': text, 'voice': 'af_maple'}]
    cached, metadata = worker.audio('en', text)
    assert cached == first and metadata['cache_hit'] and len(calls) == 2
    def forbidden():
        raise AssertionError('A disk cache hit must not load ORT')
    restarted = KokoroEngine(config, runtime_factory=forbidden, versions={'test-g2p': '1'})
    assert restarted.audio('en', text)[1]['cache_hit'] and not restarted.health()['loaded']
    path = worker._cache_path(worker.key(worker.item('en', text)))
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(config.cache_dir.glob('*.tmp'))


def test_cache_identity_includes_model_voice_g2p_and_language(config):
    worker = engine(config)
    base = worker.key(worker.item('en', 'Hello'))
    assert worker.key(worker.item('zh', 'Hello')) != base
    other = engine(replace(config, en_voice='am_adam', en_voices=('af_maple', 'am_adam')))
    assert other.key(other.item('en', 'Hello')) != base
    assert engine(config, versions={'test-g2p': '2'}).key(worker.item('en', 'Hello')) != base
    vocab = config.cache_dir.parent / 'tokenizer.json'
    vocab.write_text(json.dumps({'model': {'vocab': {'a': 1}}}))
    explicit = engine(replace(config, vocab_path=str(vocab)))
    explicit_key = explicit.key(explicit.item('en', 'Hello'))
    assert explicit_key != base
    vocab.write_text(json.dumps({'model': {'vocab': {'a': 2}}}))
    assert explicit.key(explicit.item('en', 'Hello')) != explicit_key
    Path(config.model_path).write_bytes(b'changed-model-v2')
    assert worker.key(worker.item('en', 'Hello')) != base


@pytest.mark.parametrize('content', [b'', b'RIFF' + b'\0' * 100, wav()[:-3], wav() + b'extra'])
def test_corrupted_cache_is_not_played_and_is_replaced_atomically(config, content):
    calls = []; worker = engine(config, calls)
    key = worker.key(worker.item('en', 'Hello'))
    config.cache_dir.mkdir(); worker._cache_path(key).write_bytes(content)
    rendered, metadata = worker.audio('en', 'Hello')
    assert valid_wav(rendered) and not metadata['cache_hit'] and calls[0] == 'load'
    assert valid_wav(worker._cache_path(key).read_bytes())


@pytest.mark.parametrize('failure', ['exception', 'invalid-wav', 'timeout'])
def test_failed_synthesis_never_caches_partial_audio(config, failure):
    def load():
        def render(item, deadline):
            if failure == 'exception':
                raise RuntimeError('private input must not escape into API errors')
            if failure == 'timeout':
                return wav()
            return wav()[:-3]
        return render
    worker = KokoroEngine(replace(config, synthesis_timeout=-1) if failure == 'timeout' else config, runtime_factory=load)
    with pytest.raises(KokoroError) as caught:
        worker.audio('en', 'Private words')
    assert caught.value.status == 503 and 'Private' not in str(caught.value)
    assert not list(config.cache_dir.glob('*.wav'))


def test_only_one_synthesis_runs_and_interactive_busy_returns_quickly(config):
    entered, release = threading.Event(), threading.Event()
    def load():
        def render(item, deadline):
            entered.set(); assert release.wait(5); return wav()
        return render
    worker = KokoroEngine(config, runtime_factory=load)
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.audio('en', 'First')))
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(KokoroError) as caught:
            worker.audio('en', 'Second')
        assert caught.value.code == 'busy'
    finally:
        release.set(); thread.join(3)
    assert len(result) == 1 and valid_wav(result[0][0])


def test_cleanup_enforces_age_count_and_size_without_loading_runtime(config):
    worker = engine(replace(config, max_files=2, max_bytes=len(wav()) * 2, max_age_days=1))
    config.cache_dir.mkdir()
    for index in range(4):
        path = config.cache_dir / (str(index) + '.wav'); path.write_bytes(wav())
        os.utime(path, (time.time() - 100 + index, time.time() - 100 + index))
    expired = config.cache_dir / 'expired.wav'; expired.write_bytes(wav()); os.utime(expired, (0, 0))
    worker.cleanup()
    assert sorted(path.name for path in config.cache_dir.glob('*.wav')) == ['2.wav', '3.wav']
    assert not worker.health()['loaded']


def test_long_phoneme_input_is_fully_partitioned_even_without_word_boundaries():
    for value in ('a' * 1601, ('hello world. ' * 150).strip(), '中ɕ↓ æ! ' * 180):
        parts = split_phonemes(value)
        assert len(parts) > 1 and ''.join(parts) == value and max(map(len, parts)) <= 510
    with pytest.raises(KokoroError):
        split_phonemes('a' * 20001)


def test_rhotic_english_vowels_expand_without_losing_r_or_hiding_other_unknowns():
    source = 'dˈɑːktɚ ˈɝːli ɚ' + 'a' * 510 + 'ɚ❓'
    normalized = normalize_english_phonemes(source)
    assert normalized == 'dˈɑːktəɹ ˈɜɹːli əɹ' + 'a' * 510 + 'əɹ❓'
    assert normalized.count('ɹ') == 4 and '❓' in normalized
    chunks = split_phonemes(normalized)
    assert ''.join(chunks) == normalized and chunks[-1].endswith('əɹ❓')


@pytest.mark.parametrize('explicit_vocab', [False, True])
def test_runtime_uses_one_cpu_session_explicit_chinese_g2p_and_all_phoneme_chunks(config, monkeypatch, explicit_vocab):
    np = pytest.importorskip('numpy')
    made, spoken, settings, seen_english = [], [], [], []
    class Session:
        def __init__(self, path, **kwargs):
            made.append((path, kwargs)); self._model_path = path
    class Model:
        tokenizer = SimpleNamespace(vocab={char: index + 1 for index, char in enumerate('ab 中.?!')})
        def get_voices(self):
            return ['zf_001', 'af_maple']
        def create(self, text, **kwargs):
            spoken.append((text, kwargs)); return np.array([0.1, -0.2, 0.3]), 24000
        @classmethod
        def from_session(cls, session, path, **kwargs):
            assert isinstance(session, Session)
            assert ('vocab_config' in kwargs) is explicit_vocab
            if explicit_vocab:
                assert kwargs['vocab_config'] == {'vocab': cls.tokenizer.vocab}
            return cls()
    def english(text, **kwargs):
        seen_english.append(text); return 'ab ' * 350
    class Chinese:
        def __init__(self, version, en_callable):
            assert version == '1.1'; self.english = en_callable
        def __call__(self, text):
            return ('中 ' + self.english('English') if 'English' in text else '中'), None
    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(SessionOptions=lambda: settings.append(SimpleNamespace()) or settings[-1],
                        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL='sequential'), InferenceSession=Session))
    monkeypatch.setitem(sys.modules, 'kokoro_onnx', SimpleNamespace(Kokoro=Model))
    monkeypatch.setitem(sys.modules, 'kokoro_onnx.config', SimpleNamespace(EspeakConfig=lambda **kwargs: kwargs))
    monkeypatch.setitem(sys.modules, 'phonemizer', SimpleNamespace(phonemize=english))
    monkeypatch.setitem(sys.modules, 'misaki', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'misaki.zh', SimpleNamespace(ZHG2P=Chinese))
    monkeypatch.setitem(sys.modules, 'misaki.zh_frontend', SimpleNamespace(ZH_MAP={}))
    if explicit_vocab:
        path = config.cache_dir.parent / 'tokenizer.json'
        path.write_text(json.dumps({'model': {'vocab': Model.tokenizer.vocab}}))
        config = replace(config, vocab_path=str(path))
    worker = KokoroEngine(config)
    content, _ = worker.audio('en', 'A' * 500)
    assert valid_wav(content) and len(made) == 1 and len(spoken) >= 3
    assert ''.join(text for text, _ in spoken) == ('ab ' * 350).strip()
    assert all(len(text) <= 510 and kwargs['is_phonemes'] is True and kwargs['speed'] == 1.0 for text, kwargs in spoken)
    assert made[0][1]['providers'] == ['CPUExecutionProvider']
    assert settings[0].intra_op_num_threads == settings[0].inter_op_num_threads == 1
    spoken.clear(); worker.audio('zh', '中文 English')
    assert len(made) == 1 and ''.join(text for text, _ in spoken).startswith('中 ab')
    assert seen_english[-1] == 'English'
    Model.tokenizer.vocab.pop('b')
    with pytest.raises(KokoroError) as caught:
        worker.audio('en', 'A new uncached item')
    assert caught.value.code == 'phonemes'
    assert not worker._cache_path(worker.key(worker.item('en', 'A new uncached item'))).exists()


@pytest.mark.parametrize('payload', [{'vocab': {'a': 1, ' ': 2}}, {'model': {'vocab': {'a': 1, ' ': 2}}}])
def test_explicit_local_export_vocabulary_supports_both_documented_shapes(config, payload):
    path = config.cache_dir.parent / 'tokenizer.json'; path.write_text(json.dumps(payload))
    assert load_vocab(path) == {'vocab': {'a': 1, ' ': 2}}
    selected = KokoroConfig.from_env({'KOKORO_EXPERIMENT_ENABLED': '1', 'KOKORO_MODEL': config.model_path,
                                      'KOKORO_VOICES': config.voices_path, 'KOKORO_VOCAB': str(path)})
    assert selected.vocab_path == str(path) and selected.ready()


@pytest.mark.parametrize('encoded', ['broken', '[]', '{"model":null}', '{"vocab":{}}',
                                    '{"vocab":{"aa":1}}', '{"vocab":{"a":true}}', '{"vocab":{"a":-1}}',
                                    '{"vocab":{"a":65536}}', '{"vocab":{"a":1,"b":1}}', '{"vocab":{"a":1,"a":2}}'])
def test_invalid_local_vocabulary_is_rejected_without_download_or_model_load(config, encoded):
    path = config.cache_dir.parent / 'tokenizer.json'; path.write_text(encoded)
    with pytest.raises(KokoroError) as caught:
        load_vocab(path)
    assert caught.value.code == 'vocab'


def test_contextual_hint_uses_one_aligned_phrase_character_without_global_changes():
    calls = []
    def phrase(value):
        calls.append(('phrase', value)); return ['d', 'b'], ['an1', 'o2']
    class Chinese:
        frontend = SimpleNamespace(_get_initials_finals=phrase)
        def __call__(self, text):
            calls.append(('whole', text)); return 'prefix-phones', None
    chinese = Chinese()
    assert chinese_phonemes(chinese, '单薄的薄', {'b': 'ㄅ', 'o': 'ㄛ', '2': '2'}) == 'prefix-phones ㄅㄛ2'
    assert calls == [('phrase', '单薄'), ('whole', '单薄的')]
    calls.clear()
    for text in ('薄薄的薄', '我说的是单薄的薄。', '薄的薄', '单薄的重'):
        assert chinese_phonemes(chinese, text, {}) == 'prefix-phones'
    assert all(kind == 'whole' for kind, _ in calls)


def test_unreliable_contextual_hint_is_rejected_instead_of_guessing_or_dropping():
    chinese = SimpleNamespace(frontend=SimpleNamespace(_get_initials_finals=lambda _: (['b'], ['o2'])))
    with pytest.raises(KokoroError) as caught:
        chinese_phonemes(chinese, '单薄的薄', {})
    assert caught.value.code == 'phonemes'


def test_contextual_hints_match_the_real_pinned_chinese_phrase_g2p():
    zh = pytest.importorskip('misaki.zh')
    frontend = pytest.importorskip('misaki.zh_frontend')
    chinese = zh.ZHG2P(version='1.1', en_callable=lambda text: 'english')
    before = {target: chinese(target)[0] for target in ('薄', '行', '长')}
    for phrase, target in (('单薄', '薄'), ('银行', '行'), ('长大', '长')):
        result = chinese_phonemes(chinese, phrase + '的' + target, frontend.ZH_MAP)
        prefix, isolated = result.rsplit(' ', 1)
        assert prefix == chinese(phrase + '的')[0]
        assert isolated in chinese(phrase)[0], 'The target must use the same reading as the complete phrase'
        assert result == chinese_phonemes(chinese, phrase + '的' + target, frontend.ZH_MAP)
    assert chinese('薄')[0] != chinese_phonemes(chinese, '单薄的薄', frontend.ZH_MAP).rsplit(' ', 1)[1]
    assert {target: chinese(target)[0] for target in before} == before, 'No global pronunciation dictionary may be mutated'


def test_prepare_queue_bounds_deduplicates_and_rejects_the_whole_invalid_request(config):
    worker = engine(replace(config, queue_size=2)); queue = PreparationQueue(worker, start=False)
    assert queue.prepare('en', ['First', 'First', 'Second', 'Overflow']) == {'accepted': 2, 'cached': 0, 'pending': 1, 'failed': 1, 'count': 4}
    assert queue.status()['pending'] == 2
    with pytest.raises(KokoroError):
        queue.prepare('en', ['Valid', 'A' * 501])
    assert queue.status()['pending'] == 2
    queue.start()
    with queue._condition:
        assert queue._condition.wait_for(lambda: queue.status()['completed'] == 2, timeout=3)
    assert queue.prepare('en', ['First']) == {'accepted': 0, 'cached': 1, 'pending': 0, 'failed': 0, 'count': 1}
    assert not any('text' in key for key in queue.status())
    queue.close()


def test_worker_http_validates_local_requests_and_exposes_cache_and_prepare_without_models(config):
    path = Path(__file__).parent / 'scripts/kokoro_worker.py'
    spec = importlib.util.spec_from_file_location('isolated_kokoro_worker_test', path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    calls = []; core = engine(config, calls)
    try:
        server = worker.WorkerServer(('127.0.0.1', 0), core)
    except PermissionError:
        pytest.skip('The sandbox does not allow local loopback sockets')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def request(path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        encoded = json.dumps(body) if body is not None else None
        connection.request('POST' if body is not None else 'GET', path, body=encoded,
                           headers={'Content-Type': 'application/json', **(headers or {})})
        result = connection.getresponse(); content = result.read(); status = result.status; fields = dict(result.getheaders()); connection.close()
        return status, content, fields
    try:
        assert json.loads(request('/health')[1])['loaded'] is False and calls == []
        assert request('/health', headers={'Origin': 'https://outside.example'})[0] == 403
        assert request('/audio', {'language': 'en', 'text': 'A' * 501})[0] == 400
        assert request('/audio', {'language': 'en', 'text': 'Private', 'unexpected': True})[0] == 400
        first = request('/audio', {'language': 'en', 'text': 'Private'})
        assert first[0] == 200 and valid_wav(first[1]) and first[2]['X-TTS-Cache'] == 'MISS'
        assert request('/audio', {'language': 'en', 'text': 'Private'})[2]['X-TTS-Cache'] == 'HIT'
        assert request('/prepare', {'language': 'en', 'texts': ['Private']})[0] == 200
        assert request('/status')[0] == 200
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
    with pytest.raises(ValueError):
        worker.WorkerServer(('0.0.0.0', 8877), core)


def test_background_queue_prioritizes_current_item_without_duplicating_work(config):
    worker = engine(config)
    queue = PreparationQueue(worker, start=False)
    queue.prepare('en', ['First', 'Second', 'Current'])
    receipt = queue.prepare('en', ['Current'], priority=True)
    assert receipt == {'accepted': 0, 'cached': 0, 'pending': 1, 'failed': 0, 'count': 1}
    assert [item['text'] for _, item in queue._queue] == ['Current', 'First', 'Second']
    queue.prepare('en', ['New current'], priority=True)
    assert [item['text'] for _, item in queue._queue] == ['New current', 'Current', 'First', 'Second']
    queue.close()


def test_worker_background_audio_enqueues_then_uses_cache_and_reset_releases_process(config, monkeypatch):
    path = Path(__file__).parent / 'scripts/kokoro_worker.py'
    spec = importlib.util.spec_from_file_location('background_kokoro_worker_test', path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    monkeypatch.setenv('KOKORO_BACKGROUND_ONLY', '1')
    calls = []; core = engine(config, calls)
    try:
        server = worker.WorkerServer(('127.0.0.1', 0), core)
    except PermissionError:
        pytest.skip('The sandbox does not allow local loopback sockets')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def post(path, payload, origin=None):
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        connection.request('POST', path, body=json.dumps(payload), headers=headers)
        response = connection.getresponse(); content = response.read(); result = response.status, content, dict(response.getheaders()); connection.close()
        return result
    stopped = False
    try:
        first = post('/audio', {'language': 'en', 'text': 'Prepare then play'})
        assert first[0] == 503 and json.loads(first[1])['code'] == 'preparing'
        with server.preparation._condition:
            assert server.preparation._condition.wait_for(lambda: server.preparation.status()['completed'] == 1, timeout=3)
        cached = post('/audio', {'language': 'en', 'text': 'Prepare then play'})
        assert cached[0] == 200 and valid_wav(cached[1]) and cached[2]['X-TTS-Cache'] == 'HIT'
        assert len(calls) == 2, 'Only background generation should load and synthesize once'
        assert post('/reset', {}, 'https://external.example')[0] == 403
        assert post('/reset', {'unexpected': True})[0] == 400
        assert post('/reset', {})[:2] == (200, b'{"reset":true}')
        thread.join(3); assert not thread.is_alive(); stopped = True
    finally:
        if not stopped: server.shutdown()
        server.server_close(); thread.join(3)
