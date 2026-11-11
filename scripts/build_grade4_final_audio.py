"""Build fixed grade-four listening WAVs using the offline macOS speech voice.

Requires /usr/bin/say and /usr/bin/afconvert when synthesizing new speech. The speech service must be
accessible to the process; a sandboxed say invocation may return empty audio.
Intermediate files and the quality report stay outside the repository.
Normal playback keeps the original voice timing and pitch. An optional
--target-wpm uses ffmpeg's pitch-preserving atempo filter for an explicit pace.
--reuse-source-dir accepts previously synthesized WAVs with matching text files.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import wave


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'builtin_exams/shanghai-grade4-u5-u10-final.json'
OUTPUT_ROOT = ROOT / 'static/exam-preparation/audio'
WORK_ROOT = Path('/private/tmp/reciter-normal-audio-build')
SAMPLE_RATE = 22050
CHANNELS = 1
SAMPLE_WIDTH = 2
LEAD_AND_TAIL_SECONDS = 0.5
REPEAT_GAP_SECONDS = 2.0


def read_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), 'rb') as reader:
        if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth(), reader.getcomptype()) != (
            SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH, 'NONE',
        ):
            raise RuntimeError(f'Unexpected WAV format: {path}')
        frames = reader.getnframes()
        pcm = reader.readframes(frames)
    if not frames or len(pcm) != frames * SAMPLE_WIDTH:
        raise RuntimeError(
            f'Speech synthesis produced empty or incomplete audio: {path}. '
            'Run with access to the macOS system speech service.'
        )
    return pcm, frames


def pcm_metrics(pcm: bytes) -> dict:
    samples = array.array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    if not samples:
        raise RuntimeError('No PCM samples')
    peak = max(abs(sample) for sample in samples)
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    clipped = sum(abs(sample) >= 32767 for sample in samples)
    active_ratio = sum(abs(sample) > 100 for sample in samples) / len(samples)
    if rms < 200 or active_ratio < 0.1:
        raise RuntimeError('Speech is silent or too quiet')
    if clipped:
        raise RuntimeError(f'Speech contains {clipped} clipped samples')
    return {
        'peak': peak,
        'rms': round(rms, 2),
        'rms_dbfs': round(20 * math.log10(rms / 32768), 2),
        'active_sample_ratio': round(active_ratio, 4),
        'clipped_samples': clipped,
    }


def listening_questions(source: Path) -> tuple[list[dict], str]:
    document = json.loads(source.read_text(encoding='utf-8'))
    questions = [question for question in document['questions'] if question['type'] == 'listening_choice']
    if len(questions) != 10:
        raise RuntimeError('Expected exactly ten ordered listening clips')
    folders = set()
    for index, question in enumerate(questions, 1):
        audio_file = question['audio_file']
        if not isinstance(audio_file, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*/listen-[0-9]{2}\.wav', audio_file):
            raise RuntimeError(f'Unsafe audio filename: {audio_file}')
        relative = PurePosixPath(audio_file)
        if len(relative.parts) != 2 or relative.name != f'listen-{index:02d}.wav':
            raise RuntimeError('Expected ordered listen-01.wav through listen-10.wav')
        folders.add(relative.parent.as_posix())
    if len(folders) != 1:
        raise RuntimeError('A paper must use one dedicated listening folder')
    folder = folders.pop()
    if not (OUTPUT_ROOT / folder).resolve().is_relative_to(OUTPUT_ROOT.resolve()):
        raise RuntimeError('Audio output folder escapes the static audio directory')
    return questions, folder


def work_directory(folder: str) -> Path:
    label = folder.removeprefix('shanghai-grade4-')
    return WORK_ROOT / label


def selected_ids(questions: list[dict], only: str | None) -> set[str]:
    if not only:
        return {question['id'] for question in questions}
    known = {question['id'] for question in questions}
    selected = set()
    for value in only.split(','):
        value = value.strip()
        if value in known:
            selected.add(value)
        elif value.isdigit() and 1 <= int(value) <= len(questions):
            selected.add(questions[int(value) - 1]['id'])
        else:
            raise RuntimeError(f'Unknown listening clip selection: {value}')
    return selected


def build(voice: str = 'Samantha', rate: int = 100, target_wpm: int | None = None,
          source: Path = SOURCE, only: str | None = None,
          reuse_source_dir: Path | None = None) -> list[dict]:
    questions, folder = listening_questions(source)
    selected = selected_ids(questions, only)
    work_dir = work_directory(folder)
    work_dir.mkdir(parents=True, exist_ok=True)
    report_path = work_dir / 'quality-report.json'
    report_by_id = {}
    if only:
        previous = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else []
        report_by_id = {item['question_id']: item for item in previous}
        for question in questions:
            if question['id'] in selected:
                continue
            item = report_by_id.get(question['id'])
            destination = OUTPUT_ROOT / question['audio_file']
            if not item or item['text_sha256'] != hashlib.sha256(question['listening_text'].encode('utf-8')).hexdigest():
                raise RuntimeError(f'Unselected clip has no matching transcript report: {question["id"]}')
            if not destination.is_file() or item['sha256'] != hashlib.sha256(destination.read_bytes()).hexdigest():
                raise RuntimeError(f'Unselected clip differs from its report: {question["id"]}')
    ffmpeg = None
    if target_wpm is not None:
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            ffmpeg = next((str(path) for path in (Path('/opt/homebrew/bin/ffmpeg'), Path('/usr/local/bin/ffmpeg')) if path.is_file()), None)
        if not ffmpeg:
            raise RuntimeError('An existing ffmpeg is required for an explicit target pace')
    output_dir = OUTPUT_ROOT / folder
    output_dir.mkdir(parents=True, exist_ok=True)
    edge_silence = bytes(round(SAMPLE_RATE * LEAD_AND_TAIL_SECONDS) * SAMPLE_WIDTH)
    repeat_silence = bytes(round(SAMPLE_RATE * REPEAT_GAP_SECONDS) * SAMPLE_WIDTH)
    for index, question in enumerate(questions, 1):
        if question['id'] not in selected:
            continue
        text = question['listening_text']
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f'Missing listening text for {question["id"]}')
        stem = f'listen-{index:02d}'
        transcript = work_dir / (stem + '.txt')
        aiff = work_dir / (stem + '.aiff')
        source_wav = work_dir / (stem + '.source.wav')
        single_wav = work_dir / (stem + '.single.wav')
        if reuse_source_dir is not None:
            cached_text = reuse_source_dir / (stem + '.txt')
            cached_wav = reuse_source_dir / (stem + '.source.wav')
            if cached_text.read_text(encoding='utf-8') != text:
                raise RuntimeError(f'Cached source transcript differs: {question["id"]}')
            if cached_wav.resolve() != source_wav.resolve():
                shutil.copy2(cached_wav, source_wav)
            transcript.write_text(text, encoding='utf-8')
            aiff_duration = None
        else:
            transcript.write_text(text, encoding='utf-8')
            aiff.unlink(missing_ok=True)
            source_wav.unlink(missing_ok=True)
            subprocess.run([
                '/usr/bin/say', '-v', voice, '-r', str(rate),
                '-o', str(aiff), '-f', str(transcript),
            ], check=True, timeout=60)
            subprocess.run([
                '/usr/bin/afconvert', '-f', 'WAVE', '-d', f'LEI16@{SAMPLE_RATE}',
                '-c', str(CHANNELS), str(aiff), str(source_wav),
            ], check=True, timeout=60)
            aiff_info = subprocess.check_output(['/usr/bin/afinfo', str(aiff)], text=True, timeout=30)
            aiff_duration = float(re.search(r'estimated duration:\s*([0-9.]+) sec', aiff_info).group(1))
            aiff_sample_rate = float(re.search(r'([0-9.]+) Hz', aiff_info).group(1))
            if aiff_sample_rate != SAMPLE_RATE:
                raise RuntimeError(f'AIFF sample rate differs: {question["id"]}')
        source_pcm, source_frames = read_pcm(source_wav)
        source_duration = source_frames / SAMPLE_RATE
        if aiff_duration is not None and abs(aiff_duration - source_duration) > 1 / SAMPLE_RATE:
            raise RuntimeError(f'AIFF conversion changed sample rate or duration: {question["id"]}')
        word_count = len(re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)*", text))
        if not word_count or not 1.0 <= source_duration <= 60.0:
            raise RuntimeError(f'Unexpected source speech: {question["id"]}')
        pcm_metrics(source_pcm)
        source_wpm = word_count * 60 / source_duration
        tempo_factor = 1.0
        if target_wpm is None:
            single_pcm, single_frames = source_pcm, source_frames
        else:
            tempo_factor = target_wpm / source_wpm
            if not 0.5 <= tempo_factor <= 2.0:
                raise RuntimeError(f'Tempo normalization requires an unsupported factor: {tempo_factor}')
            subprocess.run([
                ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-i', str(source_wav), '-af', f'atempo={tempo_factor:.10f}',
                '-ar', str(SAMPLE_RATE), '-ac', str(CHANNELS), '-c:a', 'pcm_s16le',
                '-map_metadata', '-1', str(single_wav),
            ], check=True, timeout=60)
            single_pcm, single_frames = read_pcm(single_wav)
        single_duration = single_frames / SAMPLE_RATE
        measured_wpm = word_count * 60 / single_duration
        if not 1.0 <= single_duration <= 60.0:
            raise RuntimeError(f'Unexpected speech duration: {question["id"]}: {single_duration}')
        if target_wpm is not None and abs(measured_wpm - target_wpm) > 2:
            raise RuntimeError(f'Speech pace differs from the explicit target: {question["id"]}')
        speech_metrics = pcm_metrics(single_pcm)
        combined_pcm = edge_silence + single_pcm + repeat_silence + single_pcm + edge_silence
        destination = OUTPUT_ROOT / question['audio_file']
        with wave.open(str(destination), 'wb') as writer:
            writer.setnchannels(CHANNELS)
            writer.setsampwidth(SAMPLE_WIDTH)
            writer.setframerate(SAMPLE_RATE)
            writer.writeframes(combined_pcm)
        destination.chmod(0o644)
        verified_pcm, final_frames = read_pcm(destination)
        if verified_pcm != combined_pcm:
            raise RuntimeError(f'WAV verification failed: {destination}')
        metrics = pcm_metrics(verified_pcm)
        item = {
            'question_id': question['id'],
            'source_catalog': str(source.resolve()),
            'audio_file': question['audio_file'],
            'voice': voice,
            'rate': rate,
            'spoken_word_count': word_count,
            'target_wpm': target_wpm,
            'source_single_read_seconds': round(source_duration, 3),
            'source_aiff_seconds': aiff_duration,
            'source_sample_rate': SAMPLE_RATE,
            'source_estimated_wpm': round(source_wpm, 2),
            'atempo_factor': round(tempo_factor, 8),
            'estimated_wpm': round(measured_wpm, 2),
            'tempo_method': 'original speech (no tempo filter)' if target_wpm is None else 'ffmpeg atempo (pitch preserving)',
            'reused_source': str(reuse_source_dir.resolve()) if reuse_source_dir else None,
            'sample_rate': SAMPLE_RATE,
            'sample_width_bytes': SAMPLE_WIDTH,
            'channels': CHANNELS,
            'repetitions': 2,
            'repeat_gap_seconds': REPEAT_GAP_SECONDS,
            'leading_silence_seconds': LEAD_AND_TAIL_SECONDS,
            'trailing_silence_seconds': LEAD_AND_TAIL_SECONDS,
            'single_read_seconds': round(single_duration, 3),
            'duration_seconds': round(final_frames / SAMPLE_RATE, 3),
            'bytes': destination.stat().st_size,
            'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'speech_only_metrics': speech_metrics,
            **metrics,
        }
        report_by_id[question['id']] = item
        print(f'{question["id"]}: {word_count} words, {item["estimated_wpm"]:.2f} wpm, '
              f'{item["duration_seconds"]:.3f}s, '
              f'{item["rms_dbfs"]:.2f} dBFS, {item["sha256"]}', flush=True)
    report = [report_by_id[question['id']] for question in questions]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--voice', default='Samantha')
    parser.add_argument('--rate', type=int, default=100)
    parser.add_argument('--target-wpm', type=int, default=None, help='Optional explicit pace; omitted keeps normal original speech')
    parser.add_argument('--source', type=Path, default=SOURCE, help='Catalog JSON; defaults to the final exam')
    parser.add_argument('--only', help='Regenerate selected question IDs or clip numbers, separated by commas')
    parser.add_argument('--reuse-source-dir', type=Path, help='Use matching listen-NN.txt and listen-NN.source.wav instead of synthesizing')
    args = parser.parse_args()
    if not 80 <= args.rate <= 180:
        parser.error('--rate must be between 80 and 180')
    if args.target_wpm is not None and not 80 <= args.target_wpm <= 240:
        parser.error('--target-wpm must be between 80 and 240')
    source = args.source.resolve()
    report = build(args.voice, args.rate, args.target_wpm, source, args.only, args.reuse_source_dir)
    _, folder = listening_questions(source)
    print(f'Verified {len(report)} fixed listening WAVs; report: {work_directory(folder) / "quality-report.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
