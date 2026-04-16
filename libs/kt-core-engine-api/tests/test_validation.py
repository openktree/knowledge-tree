from kt_core_engine_api.extractor import is_valid_entity_name


def test_accepts_normal_names():
    assert is_valid_entity_name("Machine Learning")
    assert is_valid_entity_name("GPT-4")


def test_rejects_too_short_or_long():
    assert not is_valid_entity_name("")
    assert not is_valid_entity_name("a")
    assert not is_valid_entity_name("x" * 200)


def test_rejects_initials_and_et_al():
    assert not is_valid_entity_name("K. M. A.")
    assert not is_valid_entity_name("Smith et al.")


def test_rejects_repeated_patterns():
    assert not is_valid_entity_name("abc. abc. abc. abc.")


def test_rejects_low_alpha_ratio():
    assert not is_valid_entity_name("1234567890 ")
