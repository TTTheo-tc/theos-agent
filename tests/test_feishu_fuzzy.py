from src.feishu.fuzzy import fuzzy_count, fuzzy_find_text, normalize_for_fuzzy_match


def test_normalize_for_fuzzy_match_converts_common_unicode_variants():
    assert normalize_for_fuzzy_match("A\u2014B\u00a0\u201cquote\u201d  ") == 'A-B "quote"'


def test_fuzzy_find_text_prefers_exact_unique_match():
    result = fuzzy_find_text("alpha beta gamma", "beta")

    assert result.found is True
    assert result.index == 6
    assert result.match_length == 4
    assert result.used_fuzzy_match is False
    assert result.content_for_replacement == "alpha beta gamma"


def test_fuzzy_find_text_prefers_exact_match_even_if_normalized_would_be_ambiguous():
    result = fuzzy_find_text("alpha-beta alpha\u2014beta", "alpha-beta")

    assert result.found is True
    assert result.index == 0
    assert result.used_fuzzy_match is False
    assert result.content_for_replacement == "alpha-beta alpha\u2014beta"


def test_fuzzy_find_text_matches_normalized_unique_text():
    result = fuzzy_find_text("alpha\u2014beta", "alpha-beta")

    assert result.found is True
    assert result.index == 0
    assert result.match_length == len("alpha-beta")
    assert result.used_fuzzy_match is True
    assert result.content_for_replacement == "alpha-beta"


def test_fuzzy_find_text_rejects_ambiguous_exact_match():
    result = fuzzy_find_text("beta beta", "beta")

    assert result.found is False
    assert result.index == -1
    assert result.content_for_replacement == "beta beta"


def test_fuzzy_find_text_rejects_ambiguous_normalized_match():
    result = fuzzy_find_text("alpha\u2014beta alpha\u2013beta", "alpha-beta")

    assert result.found is False
    assert result.index == -1
    assert result.content_for_replacement == "alpha\u2014beta alpha\u2013beta"


def test_fuzzy_count_uses_fuzzy_fallback_only_when_needed():
    assert fuzzy_count("one two one", "one") == (2, False)
    assert fuzzy_count("one\u2014two", "one-two") == (1, True)
