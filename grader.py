"""Word segmentation and HSK-level grading.

Segmentation is done by the LLM (temperature 0) and validated against the
original sentence; if the model cannot reproduce the sentence exactly we retry
once and finally fall back to one token per character.  Ratings only look at
Han tokens: punctuation, digits, Latin text and proper nouns never count as
out-of-vocabulary.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

import llm
import vocab as vocab_mod
from pypinyin import Style, pinyin
from vocab import Level

logger = logging.getLogger(__name__)

LENGTH_TOLERANCE = 0.15

HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
DIGITS_RE = re.compile(r"^[0-9０-９]+$")
LATIN_RE = re.compile(r"^[A-Za-zＡ-Ｚａ-ｚ]+$")

ChatFn = Callable[..., dict[str, Any]]

SEGMENT_SYSTEM = """你是中文分词器，同时为每个词标注读音和释义。把用户给出的句子切成词，并判断每个词是不是人名或地名。
要求：
1. 连接所有 w 必须与句子完全相同（包括标点），不要增删或改写任何字符。
2. 标点符号单独成为一个词，例如 "，"、"。"。
3. 数字、字母连写作为一个词。
4. 人名或地名（如"小明""北京""李老师"）的 proper 设为 true，其他词为 false。
5. 每个词给出 pinyin：按这个句子里的读法，逐字给出带声调符号的拼音数组，长度必须等于 w 的字符数。
   - 多音字按上下文选读音（了 le/liǎo、东西 dōngxi、行 xíng/háng）。
   - 轻声不标声调（妈妈 → ["mā", "ma"]）。
6. gloss_pl：该词在本句中的简短波兰语释义，最多 5 个词；gloss_en：简短英语释义，最多 5 个词。
7. 标点、数字、拉丁字母的 pinyin 为 []，gloss_pl 和 gloss_en 为空字符串。
只输出 JSON，格式示例：
{"words": [
  {"w": "妈妈", "proper": false, "pinyin": ["mā", "ma"], "gloss_pl": "mama", "gloss_en": "mom"},
  {"w": "买", "proper": false, "pinyin": ["mǎi"], "gloss_pl": "kupować", "gloss_en": "buy"},
  {"w": "东西", "proper": false, "pinyin": ["dōng", "xi"], "gloss_pl": "rzeczy", "gloss_en": "things"},
  {"w": "。", "proper": false, "pinyin": [], "gloss_pl": "", "gloss_en": ""}
]}"""

SEGMENT_USER = "句子：\n{sentence}\n只输出 JSON。"

_SEGMENT_CACHE: dict[str, list[dict[str, Any]]] = {}
_PINYIN_FALLBACKS = 0


def clear_cache() -> None:
    """Forget cached segmentations (used by tests)."""
    _SEGMENT_CACHE.clear()


def reset_pinyin_fallbacks() -> None:
    """Reset the diagnostic counter for mismatched model pinyin."""
    global _PINYIN_FALLBACKS
    _PINYIN_FALLBACKS = 0


def pinyin_fallback_count() -> int:
    """How many words needed the pypinyin fallback since the last reset."""
    return _PINYIN_FALLBACKS


def fallback_pinyin(word: str) -> list[str]:
    """Tone-marked pypinyin, one syllable per character."""
    syllables = pinyin(word, style=Style.TONE, heteronym=False)
    result = [item[0] if item else "" for item in syllables]
    if len(result) != len(word):
        result = (result + [""] * len(word))[: len(word)]
    return result


def validate_pinyin(word: str, supplied: Any) -> list[str]:
    """Keep the model's pinyin only when there is one syllable per character."""
    valid = (
        isinstance(supplied, list)
        and len(supplied) == len(word)
        and all(isinstance(item, str) and item.strip() for item in supplied)
    )
    if valid:
        return [str(item).strip() for item in supplied]

    global _PINYIN_FALLBACKS
    _PINYIN_FALLBACKS += 1
    fallback = fallback_pinyin(word)
    logger.warning("pinyin fallback for %r: %r -> %r", word, supplied, fallback)
    return fallback


def _clip_gloss(value: Any, max_words: int = 5) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()[:max_words])


def count_han(text: str) -> int:
    """Number of Han characters; punctuation, digits and Latin are ignored."""
    return len(HAN_RE.findall(text))


def token_kind(word: str) -> str:
    """Classify a token: han, punct, digit, latin or empty."""
    if not word:
        return "empty"
    if DIGITS_RE.match(word):
        return "digit"
    if LATIN_RE.match(word):
        return "latin"
    if not HAN_RE.search(word):
        return "punct"
    return "han"


def _segment_once(sentence: str, chat: ChatFn) -> list[dict[str, Any]]:
    data = chat(
        SEGMENT_SYSTEM,
        SEGMENT_USER.format(sentence=sentence),
        0.0,
        max_tokens=2048,
    )
    raw = data.get("words") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("segmentation response has no 'words' list")
    words: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            text = item
            proper = False
            pinyin_raw: Any = None
            gloss_pl: Any = ""
            gloss_en: Any = ""
        elif isinstance(item, dict):
            text = item.get("w")
            proper = bool(item.get("proper", False))
            pinyin_raw = item.get("pinyin")
            gloss_pl = item.get("gloss_pl")
            gloss_en = item.get("gloss_en")
        else:
            raise ValueError(f"unsupported word entry: {item!r}")
        if not isinstance(text, str) or not text:
            raise ValueError(f"empty or non-string word: {item!r}")

        if token_kind(text) == "han":
            syllables = validate_pinyin(text, pinyin_raw)
            gloss_pl_text = _clip_gloss(gloss_pl)
            gloss_en_text = _clip_gloss(gloss_en)
        else:
            syllables = []
            gloss_pl_text = ""
            gloss_en_text = ""
        words.append(
            {
                "w": text,
                "proper": proper,
                "pinyin": syllables,
                "gloss_pl": gloss_pl_text,
                "gloss_en": gloss_en_text,
            }
        )
    return words


def _join(words: Iterable[Mapping[str, Any]]) -> str:
    return "".join(str(item["w"]) for item in words)


def _char_token(char: str) -> dict[str, Any]:
    return {
        "w": char,
        "proper": False,
        "pinyin": fallback_pinyin(char) if token_kind(char) == "han" else [],
        "gloss_pl": "",
        "gloss_en": "",
    }


def segment(
    sentence: str,
    *,
    chat: ChatFn | None = None,
    cache: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Segment ``sentence`` into words, validating the round trip.

    Results are cached by sentence.  A failed validation is retried once; a
    second failure falls back to one token per character.
    """
    cache = _SEGMENT_CACHE if cache is None else cache
    cached = cache.get(sentence)
    if cached is not None:
        return [dict(item) for item in cached]

    chat_fn = chat or llm.chat_json
    words: list[dict[str, Any]] | None = None
    for attempt in (1, 2):
        try:
            candidate = _segment_once(sentence, chat_fn)
        except Exception as exc:  # malformed JSON / schema / network
            logger.warning(
                "segmentation attempt %d failed for %r: %s", attempt, sentence, exc
            )
            continue
        if _join(candidate) == sentence:
            words = candidate
            break
        logger.warning(
            "segmentation attempt %d does not reconstruct %r: %r",
            attempt,
            sentence,
            _join(candidate),
        )

    if words is None:
        logger.warning("falling back to per-character tokens for %r", sentence)
        words = [_char_token(char) for char in sentence]

    cache[sentence] = words
    return [dict(item) for item in words]


def rate_words(
    words: Sequence[Mapping[str, Any]],
    level: Level,
    vocab: Mapping[str, Level] | None = None,
) -> list[dict[str, Any]]:
    """Attach level and status to every segmented word."""
    target_rank = vocab_mod.level_rank(level)
    tokens: list[dict[str, Any]] = []
    for item in words:
        word = str(item["w"])
        proper = bool(item.get("proper"))
        kind = token_kind(word)
        found = vocab_mod.word_level(word, vocab) if kind == "han" else None
        if kind != "han":
            status = kind
        elif proper:
            status = "proper"
        elif found is not None and vocab_mod.level_rank(found) <= target_rank:
            status = "ok"
        else:
            status = "oov"

        if kind == "han":
            syllables = [str(syllable) for syllable in item.get("pinyin") or []]
            gloss_pl = str(item.get("gloss_pl") or "")
            gloss_en = str(item.get("gloss_en") or "")
        else:
            syllables = []
            gloss_pl = ""
            gloss_en = ""

        tokens.append(
            {
                "w": word,
                "pinyin": syllables,
                "gloss_pl": gloss_pl,
                "gloss_en": gloss_en,
                "level": found,
                "status": status,
                "proper": proper,
            }
        )
    return tokens


def grade_sentences(
    sentences: Sequence[str],
    level: Level,
    *,
    vocab: Mapping[str, Level] | None = None,
    chat: ChatFn | None = None,
    cache: dict[str, list[dict[str, Any]]] | None = None,
) -> list[list[dict[str, Any]]]:
    """Segment and rate every sentence, returning per-sentence tokens."""
    return [
        rate_words(segment(sentence, chat=chat, cache=cache), level, vocab)
        for sentence in sentences
    ]


def length_bounds(target_length: int) -> tuple[int, int]:
    """Inclusive Han-character window allowed by the ±15% tolerance."""
    return (
        math.floor(target_length * (1 - LENGTH_TOLERANCE)),
        math.ceil(target_length * (1 + LENGTH_TOLERANCE)),
    )


def _build_report(
    sentences: Sequence[str],
    tokens: Sequence[Sequence[Mapping[str, Any]]],
    level: Level,
    target_length: int | None,
    seed: str | None,
    max_new_words: int,
) -> dict[str, Any]:
    oov: dict[str, Level | None] = {}
    for sentence_tokens in tokens:
        for token in sentence_tokens:
            if token["status"] == "oov":
                oov.setdefault(str(token["w"]), token["level"])

    text = "".join(sentences)
    n_han = count_han(text)
    if target_length is None:
        length_ok = True
    else:
        low, high = length_bounds(int(target_length))
        length_ok = low <= n_han <= high
    seed_present = True if not seed else seed in text
    oov_words = [{"word": word, "level": lvl} for word, lvl in oov.items()]
    limit = max(0, int(max_new_words or 0))
    passes = len(oov_words) <= limit and length_ok and seed_present

    return {
        "level": vocab_mod.normalize_level(level),
        "sentences": list(sentences),
        "tokens": [list(sentence_tokens) for sentence_tokens in tokens],
        "oov_words": oov_words,
        "n_han": n_han,
        "length_ok": length_ok,
        "seed_present": seed_present,
        "passes": passes,
    }


def grade(
    sentences: Sequence[str],
    level: Level,
    *,
    target_length: int | None = None,
    seed: str | None = None,
    max_new_words: int = 0,
    vocab: Mapping[str, Level] | None = None,
    chat: ChatFn | None = None,
    cache: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Grade a story: segmentation, ratings, OOV list and pass/fail metrics."""
    sentences = [str(sentence) for sentence in sentences]
    tokens = grade_sentences(sentences, level, vocab=vocab, chat=chat, cache=cache)
    return _build_report(
        sentences, tokens, level, target_length, seed, max_new_words
    )


def regrade(
    previous: Mapping[str, Any],
    sentences: Sequence[str],
    indices: Iterable[int],
    level: Level,
    *,
    target_length: int | None = None,
    seed: str | None = None,
    max_new_words: int = 0,
    vocab: Mapping[str, Level] | None = None,
    chat: ChatFn | None = None,
    cache: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Re-segment only ``indices`` and rebuild the report.

    ``sentences`` is the updated story; unchanged sentences reuse the tokens
    from ``previous``.
    """
    sentences = [str(sentence) for sentence in sentences]
    old_tokens = list(previous.get("tokens") or [])
    changed = set(indices)
    tokens: list[list[dict[str, Any]]] = []
    for index, sentence in enumerate(sentences):
        if index in changed or index >= len(old_tokens):
            tokens.append(
                rate_words(segment(sentence, chat=chat, cache=cache), level, vocab)
            )
        else:
            tokens.append([dict(token) for token in old_tokens[index]])
    return _build_report(
        sentences, tokens, level, target_length, seed, max_new_words
    )


def flagged_indices(tokens: Sequence[Sequence[Mapping[str, Any]]]) -> list[int]:
    """Indices of sentences containing at least one OOV token."""
    return [
        index
        for index, sentence_tokens in enumerate(tokens)
        if any(token["status"] == "oov" for token in sentence_tokens)
    ]
