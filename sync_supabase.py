"""Push and pull the story JSON files to/from Supabase.

Typical migration of an existing checkout:

    python sync_supabase.py status        # local vs remote ids
    python sync_supabase.py push          # upload stories/*.json (idempotent)
    python sync_supabase.py pull --out restore

``push`` upserts by story id, so it is safe to run again after new stories are
generated.  ``pull`` writes the remote payloads back as ``<id>.json`` files,
which keeps the bank snapshot and offline tooling usable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import supabase_store

ROOT = Path(__file__).resolve().parent
STORIES_DIR = ROOT / "stories"
PUSH_BATCH = 10


def local_paths(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json"))


def load_local(directory: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Load local story JSON files, forcing ``id`` to match the file stem."""
    stories: list[dict[str, Any]] = []
    problems: list[str] = []
    for path in local_paths(directory):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        if not isinstance(data, dict):
            problems.append(f"{path.name}: not a JSON object")
            continue
        story_id = path.stem
        stored_id = str(data.get("id") or "")
        if stored_id != story_id:
            if stored_id:
                problems.append(f"{path.name}: id {stored_id!r} -> using file stem")
            data["id"] = story_id
        stories.append(data)
    return stories, problems


def write_story(directory: Path, data: dict[str, Any]) -> Path:
    story_id = str(data.get("id") or "").strip()
    if not story_id:
        raise SystemExit("remote story without an id")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{story_id}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def cmd_status(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    stories, problems = load_local(directory)
    local_ids = {str(story["id"]) for story in stories}
    remote_ids = supabase_store.story_ids()

    print(f"local : {len(local_ids)} stories in {directory}")
    print(f"remote: {len(remote_ids)} stories in Supabase table {supabase_store.table()!r}")

    only_local = sorted(local_ids - remote_ids)
    only_remote = sorted(remote_ids - local_ids)
    print(f"only local : {len(only_local)}")
    for ident in only_local[:20]:
        print("  -", ident)
    if len(only_local) > 20:
        print(f"  … and {len(only_local) - 20} more")
    print(f"only remote: {len(only_remote)}")
    for ident in only_remote[:20]:
        print("  -", ident)
    if len(only_remote) > 20:
        print(f"  … and {len(only_remote) - 20} more")
    for problem in problems:
        print("warning:", problem)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    stories, problems = load_local(directory)
    for problem in problems:
        print("warning:", problem)
    if args.limit:
        stories = stories[: args.limit]
    if not stories:
        print("nothing to push")
        return 0
    if args.dry_run:
        print(f"would upload {len(stories)} stories from {directory}")
        return 0

    sent = 0
    for start in range(0, len(stories), PUSH_BATCH):
        chunk = stories[start : start + PUSH_BATCH]
        sent += supabase_store.save_stories(chunk, batch_size=PUSH_BATCH)
        print(f"[{sent}/{len(stories)}] uploaded", flush=True)
    print(f"pushed {sent} stories to Supabase")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    stories = supabase_store.fetch_stories()
    if not stories:
        print("no stories in Supabase")
        return 0
    destination = Path(args.out) if args.out else STORIES_DIR
    if args.dry_run:
        print(f"would write {len(stories)} stories to {destination}")
        return 0
    for data in stories:
        write_story(destination, data)
    print(f"pulled {len(stories)} stories into {destination}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="compare local files with Supabase rows")
    status.add_argument("--dir", default=str(STORIES_DIR), help="local story folder")
    status.set_defaults(func=cmd_status)

    push = sub.add_parser("push", help="upload local stories (upsert by id)")
    push.add_argument("--dir", default=str(STORIES_DIR), help="local story folder")
    push.add_argument("--limit", type=int, default=0, help="only send the first N stories")
    push.add_argument("--dry-run", action="store_true", help="show what would be sent")
    push.set_defaults(func=cmd_push)

    pull = sub.add_parser("pull", help="download stories as <id>.json files")
    pull.add_argument("--out", default=None, help="destination folder (default stories/)")
    pull.add_argument("--dry-run", action="store_true", help="show what would be written")
    pull.set_defaults(func=cmd_pull)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover - exotic streams
            pass

    args = build_parser().parse_args(argv)
    if not supabase_store.configured():
        print(
            "Supabase is not configured: set SUPABASE_URL and SUPABASE_KEY in .env",
            file=sys.stderr,
        )
        return 2
    try:
        return args.func(args)
    except supabase_store.SupabaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

