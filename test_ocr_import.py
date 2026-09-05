from types import SimpleNamespace

import pytest

import ocr_import as ocr


def line(text, y=.1, score=.9):
    return {"text": text, "score": score, "box": [.1, y, .5, .04]}


def test_merge_preserves_correct_original_and_recovers_new_lines():
    vocab = ocr.Vocabulary({word: "" for word in ("masterpiece", "represent", "literature")})
    original = [line("mastorpiece"), line("represent", y=.3)]
    alternate = [line("masterpiece", score=.85), line("vosudal", y=.3, score=.99), line("literature", y=.5)]
    result = ocr.build_review(original, alternate, vocab)
    assert [row["text"] for row in result] == ["masterpiece", "represent", "literature"]
    assert "mastorpiece" in result[0]["alternatives"]


def test_dictionary_guesses_never_replace_observed_spelling():
    vocab = ocr.Vocabulary({"dialogue": "\u5bf9\u8bdd", "zoom": "\u79fb\u52a8"})
    result = ocr.build_review([line("dialogne \u5bf9\u8bdd"), line("200m\u5feb\u901f\u79fb\u52a8", y=.3)], [], vocab)
    assert result[0]["text"] == "dialogne"
    assert "dialogue" in result[0]["alternatives"]
    assert result[1]["text"] == ""
    assert "zoom" in result[1]["alternatives"]
    assert all(row["needs_review"] for row in result)


def test_review_retains_phrase_order_and_removes_dictionary_metadata():
    vocab = ocr.Vocabulary(dict.fromkeys(["cable", "car", "remember", "doing", "ability"], ""))
    result = ocr.build_review([line("cable car \u7f06\u8f66"), line("remember doing", y=.2),
                               line("34. ability /\u0259/ n.", y=.3)], [], vocab)
    assert [row["text"] for row in result] == ["cable car", "remember doing", "ability"]
    assert not any(row["needs_review"] for row in result)


def test_identical_words_on_different_rows_are_not_deduplicated():
    vocab = ocr.Vocabulary({"guide": ""})
    original = [line("guide"), line("guide", y=.2)]
    assert len(ocr.build_review(original, original, vocab)) == 2


def test_tall_detection_does_not_merge_two_rows():
    a = [.1, .1, .5, .04]
    b = [.1, .1, .5, .13]
    assert ocr._overlap(a, b) == 0


def test_normalized_boxes_and_crop_mapping_use_original_image_coordinates():
    result = SimpleNamespace(txts=("guide",), scores=(.9,), boxes=(((10, 20), (80, 20), (80, 40), (10, 40)),))
    rows = ocr.normalize_result(result, (100, 100))
    assert rows[0]["box"] == pytest.approx([.1, .2, .7, .2])
    assert ocr.map_crop_lines(rows, (100, 200, 200, 300), (1000, 1000))[0]["box"] == pytest.approx([.11, .22, .07, .02])


def test_chinese_meaning_ranks_candidates_without_inventing_words():
    vocab = ocr.Vocabulary({"bad": "\u574f", "bed": "\u5e8a\u94fa"})
    assert vocab.suggest("bud", "\u5e8a\u94fa")[0] == "bed"
    assert vocab.suggest("unreadablexyz", "\u5e8a\u94fa") == []


def test_local_wordlists_include_bare_words_phrases_and_translations(tmp_path):
    path = tmp_path / "test.txt"
    path.write_text("masterpiece\ncable car\t\u7f06\u8f66\nzoom\t\u79fb\u52a8\n", encoding="utf-8")
    words = ocr.bundled_vocabulary(tmp_path)
    assert set(words) == {"masterpiece", "cable car", "zoom"}
    assert "\u79fb\u52a8" in words["zoom"]


def test_suggestion_limit_does_not_truncate_recognized_text():
    vocab = ocr.Vocabulary({"guide": ""})
    original = [line("guide", y=index / 1000) for index in range(ocr.MAX_SUGGESTION_LINES + 1)]
    assert len(ocr.build_review(original, [], vocab)) == len(original)


def test_known_inflections_are_kept_without_spelling_suggestions():
    vocab = ocr.Vocabulary({"pour": "", "rain": "", "do": "", "remember": ""},
                           lambda word: {"pouring": ["pour"], "doing": ["do"]}.get(word, []))
    result = ocr.build_review([line("pouring rain"), line("remember doing", y=.2)], [], vocab)
    assert [row["text"] for row in result] == ["pouring rain", "remember doing"]
    assert all(row["alternatives"] == [] and not row["needs_review"] for row in result)
