"""Minimal FastAPI backend for the HSK reader.

Generation is synchronous: POST /api/story returns only when the story and its
questions are ready, and saves the result under ``stories/<id>.json``.  Reading
and checking never call an LLM.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import checker
import llm
import questions
import story
import supabase_store
import vocab as vocab_mod

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STORIES_DIR = BASE_DIR / "stories"
STATIC_DIR.mkdir(exist_ok=True)
STORIES_DIR.mkdir(exist_ok=True)

app = FastAPI(title="HSK Reader")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCAL_ONLY = {"files", "file", "local", "disk"}
_REMOTE_ONLY = {"supabase", "cloud"}


def _use_supabase() -> bool:
    """Pick the storage backend (``STORY_BACKEND=files|supabase|auto``)."""
    mode = os.environ.get("STORY_BACKEND", "auto").strip().lower()
    if mode in _LOCAL_ONLY:
        return False
    if mode in _REMOTE_ONLY:
        return True
    return supabase_store.configured()


def _mirror_enabled() -> bool:
    """Keep a local JSON copy of every story (default on; bank tooling uses it)."""
    return os.environ.get("STORY_LOCAL_MIRROR", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _story_path(story_id: str) -> Path:
    if not _ID_RE.match(story_id or ""):
        raise HTTPException(status_code=404, detail="story not found")
    return STORIES_DIR / f"{story_id}.json"


def _read_local_story(story_id: str) -> dict[str, Any]:
    path = _story_path(story_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="story not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"cannot read story: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="story file is not an object")
    return data


def _write_local_story(story_id: str, data: dict[str, Any]) -> Path:
    path = _story_path(story_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _read_story(story_id: str) -> dict[str, Any]:
    _story_path(story_id)  # validate the id before any network call
    if _use_supabase():
        try:
            data = supabase_store.get_story(story_id)
        except supabase_store.SupabaseError as exc:
            logger.warning(
                "Supabase read failed for %s (%s); using the local copy", story_id, exc
            )
            return _read_local_story(story_id)
        if data is not None:
            return data
        # Not migrated yet (or deleted remotely): fall back to the local mirror.
        logger.info("story %s is not in Supabase; using the local copy", story_id)
    return _read_local_story(story_id)


def _write_story(story_id: str, data: dict[str, Any]) -> Path | None:
    path: Path | None = None
    if not _use_supabase() or _mirror_enabled():
        path = _write_local_story(story_id, data)
    if _use_supabase():
        try:
            supabase_store.save_story(data)
        except supabase_store.SupabaseError as exc:
            logger.error("Supabase write failed for %s: %s", story_id, exc)
            raise HTTPException(
                status_code=502,
                detail=f"story saved locally but not to Supabase: {exc}",
            ) from exc
    return path


def _list_local_stories() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in STORIES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        items.append(
            {
                "id": str(data.get("id") or path.stem),
                "title": str(data.get("title") or "无题"),
                "level": data.get("level"),
                "created": str(data.get("created") or ""),
                "seed_noun": data.get("seed_noun"),
                "bank": bool(data.get("bank")),
                "finished": bool(data.get("finished", False)),
                "n_han": (data.get("metrics") or {}).get("n_han"),
                "n_questions": len(data.get("questions") or []),
            }
        )
    items.sort(key=lambda item: item["created"], reverse=True)
    return items


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/read/{story_id}")
def reader(story_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "reader.html", media_type="text/html")


@app.get("/listen/{story_id}")
def listening(story_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "listening.html", media_type="text/html")


@app.get("/api/stories")
def list_stories() -> list[dict[str, Any]]:
    if _use_supabase():
        try:
            return supabase_store.list_stories()
        except supabase_store.SupabaseError as exc:
            logger.warning(
                "Supabase list failed (%s); falling back to local stories", exc
            )
    return _list_local_stories()


@app.get("/api/stories/{story_id}")
def get_story(story_id: str) -> dict[str, Any]:
    return _read_story(story_id)


@app.post("/api/stories/{story_id}/finished")
def set_finished(
    story_id: str, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    data = _read_story(story_id)
    finished = bool(payload.get("finished"))
    data["finished"] = finished
    _write_story(story_id, data)
    return {"id": story_id, "finished": finished}


@app.post("/api/story")
def create_story(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        level = vocab_mod.normalize_level(payload.get("level"))
        length = int(payload.get("length"))
        n_questions = int(payload.get("n_questions", 3))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid request: {exc}") from exc

    if length < 1:
        raise HTTPException(status_code=400, detail="length must be positive")
    if n_questions < 1:
        raise HTTPException(status_code=400, detail="n_questions must be positive")

    try:
        result = story.generate_story(level, length)
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    quiz_error: str | None = None
    try:
        quiz = questions.generate_quiz(result, n_questions)
    except (llm.LLMError, ValueError) as exc:
        quiz = {
            "questions": [],
            "metrics": {
                "rounds": 0,
                "failed_by_reason": {"api": 1},
                "usage": llm.usage_snapshot(),
            },
            "complete": False,
        }
        quiz_error = str(exc)

    result["questions"] = quiz["questions"]
    result["quiz_metrics"] = quiz["metrics"]
    result["quiz_complete"] = quiz["complete"]
    if quiz_error:
        result["quiz_error"] = quiz_error

    result["finished"] = False
    now = datetime.now(timezone.utc)
    story_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    result["id"] = story_id
    result["created"] = now.isoformat(timespec="seconds")
    _write_story(story_id, result)
    return result


@app.post("/api/check")
def check_story(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    story_id = payload.get("story_id")
    answers = payload.get("answers")
    if not isinstance(story_id, str) or not story_id:
        raise HTTPException(status_code=400, detail="story_id is required")
    if not isinstance(answers, list):
        raise HTTPException(status_code=400, detail="answers must be a list")

    data = _read_story(story_id)
    problem_set = data.get("questions")
    if not isinstance(problem_set, list) or not problem_set:
        raise HTTPException(status_code=400, detail="story has no questions")
    return checker.check_answers(problem_set, answers, data)
