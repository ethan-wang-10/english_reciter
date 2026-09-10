import gaokao_questions as questions


def test_name_metadata_does_not_expose_the_correct_option():
    assert questions._recognition_core_sense("n. 艾米（人名）") == "艾米"
    assert questions._recognition_core_sense("n. 人名（安娜）") == "安娜"
    assert questions._recognition_core_sense("n. 约克（英国城市）") == "约克"
    assert questions._recognition_senses("n. 汉（姓氏/朝代）") == ["汉"]
    assert questions._recognition_core_sense("n. 姓氏（人名）") == "姓氏"


def test_meaningful_qualifiers_are_preserved():
    assert questions._recognition_core_sense("n. 光（尤指可见光）") == "光（尤指可见光）"
    assert questions._recognition_core_sense("n. 法官（英国高级法院）") == "法官（英国高级法院）"


def test_multiple_senses_and_name_annotations_remain_separate():
    assert questions._recognition_senses("n. 约克（英国城市）；人名（安娜）") == ["约克", "安娜"]
