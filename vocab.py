"""HSK 3.0 (2025 syllabus) vocabulary helpers.

The syllabus ships as a CSV with two columns: ``word,level`` where ``level`` is
``1``-``6`` or ``7-9`` (levels 7, 8 and 9 share one word list).  Only the
``word`` and ``level`` columns are needed here, so the file can be regenerated
from any 2025-syllabus export.
"""

from __future__ import annotations

import csv
import functools
from pathlib import Path
from typing import Mapping

VOCAB_PATH = Path(__file__).with_name("hsk30_vocab.csv")

_LEVEL_RANK = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7-9": 7}

Level = int | str


def normalize_level(level: object) -> Level:
    """Return the canonical level: ``1``-``6`` (int) or ``"7-9"`` (str)."""
    if isinstance(level, bool):
        raise ValueError(f"unsupported HSK level: {level!r}")
    if isinstance(level, int):
        if 1 <= level <= 6:
            return level
        raise ValueError(f"unsupported HSK level: {level!r}")
    text = str(level).strip().replace("–", "-").replace("—", "-").replace(" ", "")
    if text in _LEVEL_RANK:
        return int(text) if text.isdigit() else "7-9"
    raise ValueError(f"unsupported HSK level: {level!r}")


def level_rank(level: Level) -> int:
    """Comparable rank for a level: ``1``-``6`` map to themselves, ``"7-9"`` to 7."""
    canonical = normalize_level(level)
    return _LEVEL_RANK[str(canonical)]


def _parse_level(value: str) -> Level:
    text = value.strip()
    if text in _LEVEL_RANK:
        return int(text) if text.isdigit() else "7-9"
    raise ValueError(f"unsupported level value in vocabulary CSV: {value!r}")


@functools.lru_cache(maxsize=4)
def _load_cached(path: str) -> dict[str, Level]:
    table: dict[str, Level] = {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if not {"word", "level"} <= fields:
            raise ValueError(
                f"{path}: expected columns 'word' and 'level', found {sorted(fields)}"
            )
        for line_no, row in enumerate(reader, start=2):
            word = (row.get("word") or "").strip()
            raw_level = (row.get("level") or "").strip()
            if not word and not raw_level:
                continue
            if not word:
                raise ValueError(f"{path}:{line_no}: missing word")
            try:
                level = _parse_level(raw_level)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
            previous = table.get(word)
            # A word may be listed more than once (homographs); the lowest
            # level wins so a word is allowed as soon as any level covers it.
            if previous is None or level_rank(level) < level_rank(previous):
                table[word] = level
    return table


def load_vocab(path: str | Path | None = None) -> dict[str, Level]:
    """Load ``{word: level}`` from ``path`` (defaults to the bundled CSV)."""
    target = Path(path) if path is not None else VOCAB_PATH
    if not target.is_file():
        raise FileNotFoundError(f"vocabulary file not found: {target}")
    return dict(_load_cached(str(target)))


def word_level(
    word: str, vocab: Mapping[str, Level] | None = None
) -> Level | None:
    """Return the syllabus level of ``word`` or ``None`` when it is not listed."""
    if vocab is None:
        vocab = _load_cached(str(VOCAB_PATH))
    return vocab.get(word)


def is_allowed(
    word: str, level: Level, vocab: Mapping[str, Level] | None = None
) -> bool:
    """True when ``word`` is on the list and not above ``level``."""
    found = word_level(word, vocab)
    return found is not None and level_rank(found) <= level_rank(level)


def words_at_level(
    level: Level, vocab: Mapping[str, Level] | None = None
) -> list[str]:
    """Words whose own level is exactly ``level`` (``"7-9"`` is one level)."""
    target = level_rank(level)
    if vocab is None:
        vocab = _load_cached(str(VOCAB_PATH))
    return sorted(word for word, lvl in vocab.items() if level_rank(lvl) == target)


def candidate_replacements(
    word: str,
    level: Level,
    vocab: Mapping[str, Level] | None = None,
    limit: int = 8,
) -> list[str]:
    """Allowed words sharing at least one character with ``word``.

    Candidates are ordered by how close their level is to the target (richer
    vocabulary first), then by the number of shared characters and length.
    """
    if vocab is None:
        vocab = _load_cached(str(VOCAB_PATH))
    target_rank = level_rank(level)
    chars = set(word)
    scored: list[tuple[tuple[int, int, int, str], str]] = []
    for candidate, candidate_level in vocab.items():
        if candidate == word:
            continue
        rank = level_rank(candidate_level)
        if rank > target_rank:
            continue
        shared = len(chars & set(candidate))
        if not shared:
            continue
        key = (target_rank - rank, -shared, len(candidate), candidate)
        scored.append((key, candidate))
    scored.sort()
    return [candidate for _key, candidate in scored[:limit]]
