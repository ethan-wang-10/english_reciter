import gaokao_questions as questions


def _row(chinese: str) -> dict:
    return {
        "english": "amy",
        "chinese": chinese,
        "level": "小学",
        "example1": "Amy waited near the library after school.",
        "example1_form": "Amy",
        "example1_cn": "艾米放学后在图书馆附近等候。",
    }


def test_pure_person_names_are_not_question_sources():
    for definition in ("n. 艾米（人名）", "n. 人名（安娜）", "n. 男名", "n. 本顿（姓氏）"):
        assert questions.is_person_name_only_definition(definition)
        assert questions.source_from_wordbank_row(_row(definition)) is None


def test_common_words_and_mixed_proper_names_remain_for_editorial_review():
    for definition in ("n. 姓氏", "n. 苔藓", "n. 达尔文（人名/地名）", "n. 人名或商行"):
        assert not questions.is_person_name_only_definition(definition)
        assert questions.source_from_wordbank_row(_row(definition)) is not None
