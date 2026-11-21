"""Iterative story generation: write, grade, repair, repeat.

The loop is intentionally simple: a draft is produced at temperature 1.0, the
grader flags out-of-vocabulary words, and each repair round asks for rewrites of
only the flagged sentences at temperature 0.3.  After at most six rounds the
version with the fewest OOV words is returned, even if it never fully passed.
"""

from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import grader
import llm
import vocab as vocab_mod
from vocab import Level

logger = logging.getLogger(__name__)

ChatFn = Callable[..., dict[str, Any]]

NOUNS_CACHE_PATH = Path(__file__).with_name("nouns_cache.json")
SEEDS_USED_PATH = Path(__file__).with_name("seeds_used.json")
NOUN_BATCH_SIZE = 200
RECENT_SEEDS = 20
DEFAULT_ROUNDS = 6
MAX_APPEND = 8

LEVEL_GUIDES: dict[int, str] = {
    1: "HSK 1（最基础的约 300 个词）：只用最常用的词和简单句型，每句约 5-12 个汉字。",
    2: "HSK 2（累计约 500 个词）：日常词为主，可用简单的并列句，每句约 6-15 个汉字。",
    3: "HSK 3（累计约 1000 个词）：可以叙述日常经历，用简单的因果、转折复句，每句约 8-18 个汉字。",
    4: "HSK 4（累计约 2000 个词）：可以表达观点和感受，句式稍复杂，每句约 10-22 个汉字。",
    5: "HSK 5（累计约 3600 个词）：可以用书面一点的表达，句式灵活，每句约 12-26 个汉字。",
    6: "HSK 6（累计约 5400 个词）：可以用较丰富的词汇和复杂句式，每句约 15-30 个汉字。",
    7: "HSK 7-9（累计约 11000 个词）：词汇和句式接近真实语料，可用书面语，每句约 15-40 个汉字。",
}

GENERATE_SYSTEM = """你是一名中文分级读物的作者，为 HSK {level} 水平的学习者写短篇故事。
词汇与语法：{guide}
文字：简体中文。
故事要求：有一个主要人物，有一个问题（困难或冲突），有一个完整的结尾。
必须出现词语「{seed}」，并让它自然地成为故事的一部分。
篇幅：约 {length} 个汉字（不含标点），请控制在 {low}-{high} 个汉字之间。
不要使用超出该水平的生僻词、成语或文言表达；人名、地名可以用。
句子要自然、多样，不要重复相同的句子或句式；不要靠重复来凑字数。
每句都以标点结尾，逐句放进 sentences 数组。
只输出 JSON，格式：{{"title": "标题", "sentences": ["第一句。", "第二句。"]}}"""

GENERATE_USER = (
    "请写一个 {low}-{high} 个汉字的 HSK {level} 短故事，故事里必须出现「{seed}」。"
    "只输出 JSON。"
)

REPAIR_SYSTEM = """你是一名中文分级读物的编辑，负责把超纲词改成 HSK {level} 以内的词。
要求：
1. 只改写被标记的句子，其他句子原样保留，不要合并、删除或调换顺序。
2. 必须保留「{seed}」，人物、情节和语气不变。
3. 用 HSK {level} 以内的常用词替换超纲词；可以改变句式或换一种更简单的说法，只要意思不变。如果某个意思很难用本级词表达，可以删掉这个细节，让句子更简单，只要情节仍然连贯。改写后的句子要通顺自然，长度接近原句。
4. index 是被标记句子的编号（从 1 开始），只能改这些编号对应的句子。
5. 需要补充篇幅时，把新句子放进 append；不需要时 append 为 []。
6. 改写和补充的句子都要提供新信息，不要重复故事里已有的句子或句式。
只输出 JSON，格式：{{"edits": [{{"index": 1, "sentence": "改写后的句子。"}}], "append": []}}"""

NOUN_SYSTEM = """你是中文词汇分类助手。用户会给出一批 HSK 词表里的词，请只挑出适合当故事主题的名词：
人、动物、物品、食物、地点、自然景物、身体部位、日常用品等具体名词。
不要代词、时间词、方位词、动词、形容词或虚词；不要改写；不要添加词表以外的词；不要重复。
只输出 JSON，格式：{"nouns": ["书", "苹果", "学校"]}"""

NOUN_USER = "词表：\n{words}\n请只返回其中的具体名词。只输出 JSON。"


def _guide(level: Level) -> str:
    return LEVEL_GUIDES[vocab_mod.level_rank(level)]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("ignoring unreadable cache %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def _clean_sentence(value: str) -> str:
    return re.sub(r"\s+", "", value)


# --- seed nouns -----------------------------------------------------------


def build_noun_list(
    level: Level,
    *,
    vocab: Mapping[str, Level] | None = None,
    chat: ChatFn | None = None,
    cache_path: str | Path | None = None,
    batch_size: int = NOUN_BATCH_SIZE,
    force: bool = False,
    max_tokens: int = 4096,
) -> list[str]:
    """Return (and cache) the nouns of exactly ``level``.

    The vocabulary is sent to the model in batches of ``batch_size`` words and
    only words from the current batch are kept, which keeps the result anchored
    to the syllabus instead of the model's imagination.
    """
    key = str(vocab_mod.normalize_level(level))
    path = Path(cache_path) if cache_path else NOUNS_CACHE_PATH
    cache = _read_json(path)
    cached = cache.get(key)
    if (
        not force
        and isinstance(cached, list)
        and cached
        and all(isinstance(item, str) for item in cached)
    ):
        return list(cached)

    if vocab is None:
        vocab = vocab_mod.load_vocab()
    words = vocab_mod.words_at_level(level, vocab)
    chat_fn = chat or llm.chat_json
    nouns: list[str] = []
    seen: set[str] = set()

    for start in range(0, len(words), batch_size):
        batch = words[start : start + batch_size]
        allowed = set(batch)
        try:
            data = chat_fn(
                NOUN_SYSTEM,
                NOUN_USER.format(words="、".join(batch)),
                0.0,
                max_tokens=max_tokens,
            )
        except Exception as exc:  # keep going with the remaining batches
            logger.warning(
                "noun batch %d-%d failed: %s", start, start + len(batch), exc
            )
            continue
        raw = data.get("nouns") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            logger.warning(
                "noun batch %d-%d returned no 'nouns' list", start, start + len(batch)
            )
            continue
        for item in raw:
            if isinstance(item, str) and item in allowed and item not in seen:
                seen.add(item)
                nouns.append(item)
        logger.info(
            "noun list level %s: batch %d-%d -> %d nouns",
            key,
            start,
            start + len(batch),
            len(nouns),
        )

    if not nouns:
        raise llm.LLMError(
            f"could not build a noun list for HSK {key}; check the model response"
        )

    cache[key] = nouns
    _write_json(path, cache)
    return nouns


def _load_seeds_used(path: Path) -> dict[str, list[str]]:
    raw = _read_json(path)
    used: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(value, list):
            used[str(key)] = [item for item in value if isinstance(item, str)]
    return used


def pick_seed_noun(
    level: Level,
    nouns: Sequence[str],
    *,
    rng: random.Random | None = None,
    seeds_path: str | Path | None = None,
    recent: int = RECENT_SEEDS,
) -> str:
    """Pick a random noun, skipping the last ``recent`` seeds for that level."""
    if not nouns:
        raise ValueError("noun list is empty")
    key = str(vocab_mod.normalize_level(level))
    path = Path(seeds_path) if seeds_path else SEEDS_USED_PATH
    used = _load_seeds_used(path)
    history = used.get(key, [])
    blocked = set(history[-recent:]) if recent > 0 else set()
    pool = [noun for noun in nouns if noun not in blocked]
    if not pool:
        logger.warning(
            "all HSK %s nouns were used recently; recycling the full list", key
        )
        pool = list(nouns)

    chooser = rng or random.Random()
    choice = chooser.choice(pool)
    history.append(choice)
    used[key] = history[-100:]
    _write_json(path, used)
    return choice


# --- prompts and edits ----------------------------------------------------


def _max_tokens(length: int) -> int:
    return max(2048, min(16000, int(length) * 8))


def _parse_story(data: Mapping[str, Any]) -> tuple[str, list[str]]:
    raw = data.get("sentences")
    if not isinstance(raw, list):
        raise ValueError("story JSON has no 'sentences' list")
    sentences: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"sentence is not a string: {item!r}")
        cleaned = _clean_sentence(item)
        if cleaned:
            sentences.append(cleaned)
    if not sentences:
        raise ValueError("story has no usable sentences")
    title = data.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else "无题"
    return title, sentences


def generate_draft(
    level: Level,
    length: int,
    seed: str,
    *,
    chat: ChatFn | None = None,
) -> tuple[str, list[str]]:
    """Ask for a first draft and validate its JSON shape."""
    chat_fn = chat or llm.chat_json
    low, high = grader.length_bounds(length)
    system = GENERATE_SYSTEM.format(
        level=level,
        guide=_guide(level),
        seed=seed,
        length=length,
        low=low,
        high=high,
    )
    user = GENERATE_USER.format(
        level=level, low=low, high=high, seed=seed
    )
    max_tokens = _max_tokens(length)
    last_error: Exception | None = None
    for attempt in (1, 2):
        data = chat_fn(system, user, 1.0, max_tokens=max_tokens)
        try:
            return _parse_story(data)
        except ValueError as exc:
            last_error = exc
            logger.warning("draft attempt %d was unusable: %s", attempt, exc)
    raise llm.LLMError(f"model returned an unusable story: {last_error}")


def _length_note(report: Mapping[str, Any], target_length: int | None) -> str:
    n_han = int(report["n_han"])
    if not target_length:
        return f"当前全文 {n_han} 个汉字，篇幅不限。"
    low, high = grader.length_bounds(int(target_length))
    if n_han < low:
        return (
            f"当前全文 {n_han} 个汉字，目标 {target_length} 个汉字（允许 {low}-{high}）。"
            f"请在 append 中补充约 {target_length - n_han} 个汉字的句子（可以分几句，"
            "要有新信息，不要重复），使全文接近目标。"
        )
    if n_han > high:
        return (
            f"当前全文 {n_han} 个汉字，目标 {target_length} 个汉字（允许 {low}-{high}）。"
            f"篇幅过长，请把被标记的句子改短（减少约 {n_han - high} 个汉字），"
            "append 返回 []。"
        )
    return (
        f"当前全文 {n_han} 个汉字，目标 {target_length} 个汉字（允许 {low}-{high}），"
        "篇幅合适，append 返回 []。"
    )


def build_repair_request(
    report: Mapping[str, Any],
    level: Level,
    seed: str,
    target_length: int | None,
    vocab: Mapping[str, Level],
) -> tuple[str, str]:
    """Build the (system, user) pair for one repair round."""
    sentences = list(report["sentences"])
    numbered = "\n".join(f"{index + 1}. {sentence}" for index, sentence in enumerate(sentences))
    flagged = grader.flagged_indices(report["tokens"])

    lines: list[str] = []
    for index in flagged:
        seen: set[str] = set()
        parts: list[str] = []
        for token in report["tokens"][index]:
            if token["status"] != "oov":
                continue
            word = str(token["w"])
            if word in seen:
                continue
            seen.add(word)
            candidates = vocab_mod.candidate_replacements(word, level, vocab)
            parts.append(
                f"{word}（{'、'.join(candidates) if candidates else '无候选，请换一种说法'}）"
            )
        lines.append(f"- 第 {index + 1} 句「{sentences[index]}」：超纲词 " + "；".join(parts))
    if not lines:
        lines.append("- （没有超纲词，只需按下面的篇幅要求调整）")

    banned = "、".join(str(item["word"]) for item in report["oov_words"])
    notes = [_length_note(report, target_length)]
    if not report["seed_present"]:
        notes.append(
            f"故事里没有出现「{seed}」，请在 append 中补一句自然的话，把「{seed}」写进去。"
        )

    user = (
        "故事（编号）：\n"
        + numbered
        + "\n\n需要改写的句子：\n"
        + "\n".join(lines)
        + "\n\n全文不能出现这些超纲词："
        + banned
        + "\n\n"
        + "\n".join(notes)
        + "\n只输出 JSON。"
    )
    system = REPAIR_SYSTEM.format(level=level, seed=seed)
    if vocab_mod.level_rank(level) == 1:
        system += (
            "\n提醒：HSK 1 只有约 300 个最基础的词，例如：我、你、他、我们、去、来、在、有、是、"
            "好、家、学校、吃、喝、买、看、说、高兴、朋友、爸爸、妈妈。"
            "不要使用这些词以外的难词；某个超纲词没有候选词时，就换一种说法避开它。"
        )
    return system, user


def apply_edits(
    data: Mapping[str, Any],
    report: Mapping[str, Any],
) -> tuple[list[str], list[int]]:
    """Apply the model's edits/append to a copy of the story.

    Only flagged sentences may be replaced; ``append`` sentences are added at
    the end.  Returns the new sentence list and the changed indices.
    """
    sentences = list(report["sentences"])
    flagged = set(grader.flagged_indices(report["tokens"]))
    changed: list[int] = []

    raw_edits = data.get("edits")
    if isinstance(raw_edits, list):
        for edit in raw_edits:
            if not isinstance(edit, dict):
                continue
            index = edit.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            position = index - 1
            if position not in flagged:
                logger.warning("ignoring edit for unflagged sentence %r", index)
                continue
            raw_sentence = edit.get("sentence")
            if not isinstance(raw_sentence, str):
                continue
            sentence = _clean_sentence(raw_sentence)
            if not sentence or sentence == sentences[position]:
                continue
            sentences[position] = sentence
            if position not in changed:
                changed.append(position)

    raw_append = data.get("append")
    if isinstance(raw_append, list):
        appended: list[str] = []
        for item in raw_append:
            if isinstance(item, str):
                sentence = _clean_sentence(item)
                if sentence:
                    appended.append(sentence)
        if len(appended) > MAX_APPEND:
            logger.warning("truncating append list from %d to %d", len(appended), MAX_APPEND)
            appended = appended[:MAX_APPEND]
        start = len(sentences)
        sentences.extend(appended)
        changed.extend(range(start, len(sentences)))

    return sentences, changed


# --- the loop -------------------------------------------------------------


def _log_entry(
    round_no: int,
    report: Mapping[str, Any],
    changed: Sequence[int],
    note: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "round": round_no,
        "oov": [str(item["word"]) for item in report["oov_words"]],
        "n_han": report["n_han"],
        "length_ok": report["length_ok"],
        "seed_present": report["seed_present"],
        "changed": [index + 1 for index in changed],
    }
    if note:
        entry["note"] = note
    return entry


def _snapshot(
    report: Mapping[str, Any], round_no: int, length: int
) -> dict[str, Any]:
    return {
        "report": report,
        "round": round_no,
        "length_gap": abs(int(report["n_han"]) - length),
    }


def _score(snapshot: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    report = snapshot["report"]
    return (
        0 if report["passes"] else 1,
        len(report["oov_words"]),
        0 if report["length_ok"] else 1,
        0 if report["seed_present"] else 1,
        int(snapshot["length_gap"]),
    )


def generate_story(
    level: Level,
    length: int,
    *,
    seed_noun: str | None = None,
    max_new_words: int = 0,
    rng_seed: int | None = None,
    vocab: Mapping[str, Level] | None = None,
    chat: ChatFn | None = None,
    noun_chat: ChatFn | None = None,
    max_rounds: int = DEFAULT_ROUNDS,
    noun_cache_path: str | Path | None = None,
    seeds_path: str | Path | None = None,
    noun_batch_size: int = NOUN_BATCH_SIZE,
) -> dict[str, Any]:
    """Run generate -> grade -> repair and return a JSON-ready report."""
    level = vocab_mod.normalize_level(level)
    if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
        raise ValueError("length must be a positive number of Han characters")
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 0:
        raise ValueError("max_rounds must be a non-negative integer")

    vocab_table = vocab if vocab is not None else vocab_mod.load_vocab()
    chat_fn = chat or llm.chat_json
    llm.reset_usage()

    if seed_noun is not None:
        seed = seed_noun.strip()
        if not seed:
            raise ValueError("seed_noun must not be empty")
    else:
        nouns = build_noun_list(
            level,
            vocab=vocab_table,
            chat=noun_chat or chat_fn,
            cache_path=noun_cache_path,
            batch_size=noun_batch_size,
        )
        seed = pick_seed_noun(
            level, nouns, rng=random.Random(rng_seed), seeds_path=seeds_path
        )

    if vocab_mod.word_level(seed, vocab_table) is None:
        logger.warning("seed %r is not on the HSK list", seed)

    title, sentences = generate_draft(level, length, seed, chat=chat_fn)
    current = grader.grade(
        sentences,
        level,
        target_length=length,
        seed=seed,
        max_new_words=max_new_words,
        vocab=vocab_table,
        chat=chat_fn,
    )
    rounds = 0
    log = [_log_entry(0, current, [])]
    snapshots = [_snapshot(current, 0, length)]

    while not current["passes"] and rounds < max_rounds:
        system, user = build_repair_request(
            current, level, seed, length, vocab_table
        )
        try:
            data = chat_fn(system, user, 0.3, max_tokens=_max_tokens(length))
        except llm.LLMError as exc:
            logger.error("repair round %d failed: %s", rounds + 1, exc)
            error_entry = _log_entry(rounds + 1, current, [])
            error_entry["error"] = str(exc)
            log.append(error_entry)
            break

        new_sentences, changed = apply_edits(data, current)
        rounds += 1
        if not changed:
            log.append(
                _log_entry(rounds, current, [], note="no usable edits")
            )
            logger.warning("repair round %d produced no usable edits; stopping", rounds)
            break

        current = grader.regrade(
            current,
            new_sentences,
            changed,
            level,
            target_length=length,
            seed=seed,
            max_new_words=max_new_words,
            vocab=vocab_table,
            chat=chat_fn,
        )
        log.append(_log_entry(rounds, current, changed))
        snapshots.append(_snapshot(current, rounds, length))

    best = min(snapshots, key=_score)
    report = best["report"]
    best_sentences = list(report["sentences"])
    converged = bool(report["passes"])

    return {
        "level": level,
        "seed_noun": seed,
        "title": title,
        "text": "".join(best_sentences),
        "sentences": best_sentences,
        "tokens": report["tokens"],
        "new_words": report["oov_words"],
        "metrics": {
            "n_han": report["n_han"],
            "oov_count": len(report["oov_words"]),
            "rounds": rounds,
            "converged": converged,
            "usage": llm.usage_snapshot(),
        },
        "log": log,
    }
