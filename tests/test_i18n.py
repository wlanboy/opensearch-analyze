import pytest

from opensearch_analyze.i18n import Translator


def test_t_formats_with_kwargs():
    assert Translator.t("en", "finding_unassigned_shards", n=3) == "3 unassigned shard(s)"
    assert Translator.t("de", "finding_unassigned_shards", n=3) == "3 nicht zugewiesene(r) Shard(s)"


def test_t_falls_back_to_english_for_unknown_lang():
    assert Translator.t("fr", "no_issues") == Translator.t("en", "no_issues")


def test_headers_returns_list():
    headers = Translator.headers("en", "headers_cluster")
    assert isinstance(headers, list)
    assert "status" in headers


def test_section_label_known_and_unknown_lang():
    assert Translator.section_label("de", "nodes") == "Knoten"
    assert Translator.section_label("fr", "nodes") == "nodes"


@pytest.mark.parametrize("lang,count,expected", [
    ("en", 1, "long-running query"),
    ("en", 2, "long-running queries"),
    ("de", 1, "lang laufende Abfrage"),
    ("de", 2, "lang laufende Abfragen"),
])
def test_slow_query_word(lang, count, expected):
    assert Translator.slow_query_word(lang, count) == expected
