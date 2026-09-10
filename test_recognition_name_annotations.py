import gaokao_questions as questions


def test_name_metadata_does_not_expose_the_correct_option():
    assert questions._recognition_core_sense("n. 艾米（人名）") == "艾米"
    assert questions._recognition_core_sense("n. 人名（安娜）") == "安娜"
    assert questions._recognition_core_sense("n. 约克（英国城市）") == "约克"
    assert questions._recognition_senses("n. 汉（姓氏/朝代）") == ["汉"]
    assert questions._recognition_core_sense("n. 姓氏（人名）") == "姓氏"
    assert questions._recognition_core_sense("adj. 行为的（心理学）") == "行为的"


def test_meaningful_qualifiers_are_preserved():
    assert questions._recognition_core_sense("n. 光（尤指可见光）") == "光（尤指可见光）"
    assert questions._recognition_core_sense("n. 法官（英国高级法院）") == "法官（英国高级法院）"


def test_multiple_senses_and_name_annotations_remain_separate():
    assert questions._recognition_senses("n. 约克（英国城市）；人名（安娜）") == ["约克", "安娜"]


def test_full_part_of_speech_labels_are_metadata():
    for value, expected in [
        ("interjection 啊", "啊"), ("exclamation 哎呀", "哎呀"),
        ("pronoun 你", "你"), ("numeral 五", "五"), ("particle 不定式标记", "不定式标记"),
    ]:
        assert questions._recognition_core_sense(value) == expected
