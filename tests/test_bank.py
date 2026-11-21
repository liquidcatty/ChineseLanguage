import re
from collections import Counter

import build_bank


def test_requested_length_distribution():
    lengths = [build_bank.choose_length(1, f"noun-{index}") for index in range(3000)]
    counts = Counter(lengths)

    assert set(lengths) <= {60, 150, 500}
    assert counts[60] == 0
    assert 0.83 <= counts[150] / 3000 <= 0.87
    assert 0.13 <= counts[500] / 3000 <= 0.17


def test_bank_ids_are_stable_unique_and_url_safe():
    first = build_bank.bank_id(1, "书")
    assert first == build_bank.bank_id(1, "书")
    assert first != build_bank.bank_id(2, "书")
    assert first != build_bank.bank_id(1, "猫")
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
