import pytest

from opensearch_analyze.formatting import Formatter


@pytest.mark.parametrize("n,expected", [
    (0, "0.0B"),
    (512, "512.0B"),
    (1024, "1.0KB"),
    (1024 * 1024, "1.0MB"),
    (1024 ** 3, "1.0GB"),
    (1024 ** 4, "1.0TB"),
    (1024 ** 5, "1.0PB"),
])
def test_format_bytes(n, expected):
    assert Formatter.format_bytes(n) == expected


@pytest.mark.parametrize("n,expected", [
    (0, "0ms"),
    (999, "999ms"),
    (1000, "1.00s"),
    (2500, "2.50s"),
])
def test_format_ms(n, expected):
    assert Formatter.format_ms(n) == expected


def test_format_timestamp_ms():
    # 0 ms epoch formats as a plain HH:MM:SS string, locale/timezone-dependent
    # but always well-formed.
    result = Formatter.format_timestamp_ms(0)
    assert len(result) == 8
    assert result.count(":") == 2
