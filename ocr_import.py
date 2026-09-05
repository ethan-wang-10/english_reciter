"""Offline OCR cleanup, alternate readings and vocabulary suggestions."""

import csv
import re
from difflib import SequenceMatcher, get_close_matches
from functools import lru_cache
from pathlib import Path


MAX_SUGGESTION_LINES = 150
WORD_RE = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")
IPA_RE = re.compile(r"/[^/\r\n]*/|\[[^\]\r\n]*\]")
IPA_TOKEN_RE = re.compile(r"\S*[\u0250-\u02af\u1d00-\u1dbf]\S*")
POS_LABELS = frozenset("adj adv art aux conj det int interj n num phr prep pron v vi vt".split())
PUNCTUATION = str.maketrans({"\u2018": "'", "\u2019": "'", **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014", "-")})
DIGIT_SHAPES = str.maketrans("01258", "olzsb")


def english_only_text(text):
    output = []
    for line in (text or "").splitlines():
        line = IPA_TOKEN_RE.sub(" ", IPA_RE.sub(" ", line.translate(PUNCTUATION)))
        words = [word for word in WORD_RE.findall(line)
                 if word.casefold() not in POS_LABELS and (len(word) > 1 or word.lower() in ("a", "i"))]
        if words:
            output.append(" ".join(words))
    return "\n".join(output)


def bundled_vocabulary(root):
    files = sorted(Path(root).glob("*.txt"))
    signature = tuple((str(path), path.stat().st_mtime_ns) for path in files)
    return _read_bundled_vocabulary(signature)


@lru_cache(maxsize=1)
def _read_bundled_vocabulary(signature):
    entries = {}
    for filename, _ in signature:
        with open(filename, encoding="utf-8-sig") as stream:
            for row in csv.reader(stream, delimiter="\t"):
                if not row:
                    continue
                fields = row if len(row) > 1 else row[0].split(maxsplit=1)
                word = fields[0].strip().casefold()
                if len(word) <= 80 and re.fullmatch(r"[a-z]+(?:[ '-][a-z]+)*", word):
                    entries[word] = entries.get(word, "") + " " + " ".join(fields[1:])
    return entries


class Vocabulary:
    def __init__(self, entries, surface_variants=None):
        self.entries = entries
        self.surface_variants = surface_variants
        self.known_cache = {}
        self.by_length = {}
        self.cache = {}
        for word in sorted(entries):
            if " " not in word:
                self.by_length.setdefault(len(word), []).append(word)

    def coverage(self, text):
        words = WORD_RE.findall(text.casefold())
        return sum(self.is_known(word) for word in words) / max(1, len(words))

    def is_known(self, word):
        if word not in self.known_cache:
            self.known_cache[word] = word in self.entries or bool(
                self.surface_variants and any(variant in self.entries for variant in self.surface_variants(word)))
        return self.known_cache[word]

    def suggest(self, surface, hint):
        word = surface.casefold()
        key = (word, hint)
        if key in self.cache:
            return self.cache[key]
        if len(word) < 3 or len(word) > 30 or self.is_known(word):
            return []
        shaped = word.translate(DIGIT_SHAPES)
        pool = [candidate for length in range(max(1, len(word) - 2), len(word) + 3)
                for candidate in self.by_length.get(length, ())]
        matches = set(get_close_matches(shaped, pool, n=5, cutoff=.66 if len(word) == 3 else .72))
        hints = re.findall(r"[\u4e00-\u9fff]{2,}", hint)

        def rank(candidate):
            meaning = self.entries[candidate]
            supported = any(part[i:i + 2] in meaning for part in hints for i in range(len(part) - 1))
            return SequenceMatcher(None, shaped, candidate).ratio() + .12 * supported

        result = sorted(matches, key=lambda candidate: (-rank(candidate), candidate))[:3]
        self.cache[key] = result
        return result


def normalize_result(result, size):
    width, height = size
    texts = getattr(result, "txts", None)
    if texts is None:
        return []
    scores = getattr(result, "scores", None)
    boxes = getattr(result, "boxes", None)
    lines = []
    for index, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            continue
        box = None
        if boxes is not None:
            points = boxes[index]
            left = max(0., min(float(point[0]) for point in points) / width)
            top = max(0., min(float(point[1]) for point in points) / height)
            right = min(1., max(float(point[0]) for point in points) / width)
            bottom = min(1., max(float(point[1]) for point in points) / height)
            if right > left and bottom > top:
                box = [left, top, right - left, bottom - top]
        lines.append({"text": text, "score": float(scores[index]) if scores is not None else 0., "box": box})
    return lines


def enhanced_image(image, lines):
    import cv2
    import numpy as np
    from PIL import Image

    width, height = image.size
    boxes = [line["box"] for line in lines if line.get("box") and line["score"] >= .6
             and (len(english_only_text(line["text"])) >= 3 or re.search(r"[\u4e00-\u9fff]{2}", line["text"]))]
    region = (0, 0, width, height)
    if boxes:
        pad = max(16, round(max(width, height) * .015))
        region = (max(0, int(min(box[0] for box in boxes) * width) - pad),
                  max(0, int(min(box[1] for box in boxes) * height) - pad),
                  min(width, int(max(box[0] + box[2] for box in boxes) * width) + pad),
                  min(height, int(max(box[1] + box[3] for box in boxes) * height) + pad))
    crop = image.crop(region)
    gray = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=2., tileGridSize=(8, 8)).apply(gray)
    return Image.fromarray(enhanced).convert("RGB"), region


def map_crop_lines(lines, region, size):
    left, top, right, bottom = region
    width, height = size
    for line in lines:
        if line.get("box"):
            x, y, w, h = line["box"]
            line["box"] = [(left + x * (right - left)) / width, (top + y * (bottom - top)) / height,
                           w * (right - left) / width, h * (bottom - top) / height]
    return lines


def _overlap(a, b):
    if not a or not b:
        return 0.
    width = max(0., min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    height = max(0., min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    # A tall box spanning two rows must not collapse those rows into one.
    if abs((a[1] + a[3] / 2) - (b[1] + b[3] / 2)) > min(a[3], b[3]) * .6:
        return 0.
    return width * height / max(1e-8, min(a[2] * a[3], b[2] * b[3]))


def build_review(original, enhanced, vocabulary):
    groups = [[line] for line in original]
    for line in enhanced:
        matches = [(_overlap(group[0].get("box"), line.get("box")), index) for index, group in enumerate(groups)]
        overlap, index = max(matches, default=(0, 0))
        if overlap >= .45:
            groups[index].append(line)
        elif line.get("box") or not any(line["text"] == group[0]["text"] for group in groups):
            groups.append([line])
    groups.sort(key=lambda group: (group[0]["box"][1], group[0]["box"][0]) if group[0].get("box") else (2, 0))
    review = []
    for group in groups:
        readings = []
        for line in group:
            text = english_only_text(line["text"])
            if text and text not in [reading[0] for reading in readings]:
                readings.append((text, line))
        numeric_words = [word for line in group for word in re.findall(r"[0-9A-Za-z]+", line["text"])
                         if re.search(r"\d", word) and re.search(r"[A-Za-z]", word)]
        if not readings and not numeric_words:
            continue
        # Only observed OCR readings can be selected automatically; dictionary guesses stay suggestions.
        chosen = max(readings, key=lambda reading: (vocabulary.coverage(reading[0]), reading[1]["score"]), default=("", group[0]))
        text, source = chosen
        alternatives = list(dict.fromkeys(reading[0] for reading in readings if reading[0] != text))
        hint = " ".join(line["text"] for line in group)
        words = WORD_RE.findall(text)
        if len(words) <= 6 and len(review) < MAX_SUGGESTION_LINES:
            for word in words:
                for candidate in vocabulary.suggest(word, hint):
                    alternatives.append(re.sub(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", candidate, text))
            for word in numeric_words:
                alternatives.extend(vocabulary.suggest(word, hint))
        alternatives = list(dict.fromkeys(candidate for candidate in alternatives if candidate != text))[:6]
        review.append({"text": text, "alternatives": alternatives,
                       "needs_review": bool(alternatives) or vocabulary.coverage(text) < 1 or source["score"] < .8,
                       "box": source.get("box")})
    return review
