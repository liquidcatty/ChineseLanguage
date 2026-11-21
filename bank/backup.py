"""Create a verified, self-contained snapshot of the story bank.

This never touches the generation pipeline or the live ``stories/`` files.  It
writes a zip, a checksummed manifest, and can mirror the snapshot to a second
location for safe keeping.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STORIES_DIR = ROOT / "stories"
BANK_DIR = ROOT / "bank"
METADATA_FILES = (
    ROOT / "nouns_cache.json",
    ROOT / "seeds_used.json",
    ROOT / "hsk30_vocab.csv",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_snapshot(mirror: str | None = None) -> dict[str, Any]:
    BANK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_name = f"hsk-story-bank-{stamp}.zip"
    archive_path = BANK_DIR / archive_name

    story_paths = sorted(STORIES_DIR.glob("*.json"))
    if not story_paths:
        raise SystemExit("no stories found; nothing to back up")

    stories: list[dict[str, Any]] = []
    level_counts: dict[str, int] = {}
    length_counts: dict[str, int] = {}
    question_count = 0
    bank_count = 0
    finished_count = 0

    for path in story_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit(f"{path} is not a JSON object")
        raw = path.read_bytes()
        stories.append(
            {
                "path": f"stories/{path.name}",
                "bytes": len(raw),
                "sha256": sha256_bytes(raw),
            }
        )
        level = str(data.get("level"))
        level_counts[level] = level_counts.get(level, 0) + 1
        target = data.get("target_length")
        if target is not None:
            length_counts[str(target)] = length_counts.get(str(target), 0) + 1
        question_count += len(data.get("questions") or [])
        published = bool(data.get("bank"))
        bank_count += int(published)
        finished_count += int(bool(data.get("finished")))

    metadata: list[dict[str, Any]] = []
    for path in METADATA_FILES:
        if path.is_file():
            metadata.append(
                {
                    "path": f"metadata/{path.name}",
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )

    manifest: dict[str, Any] = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "archive": archive_name,
        "story_count": len(stories),
        "bank_count": bank_count,
        "question_count": question_count,
        "finished_count": finished_count,
        "levels": dict(sorted(level_counts.items())),
        "target_lengths": dict(sorted(length_counts.items())),
        "stories": stories,
        "metadata": metadata,
    }

    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in story_paths:
            archive.write(path, f"stories/{path.name}")
        for path in METADATA_FILES:
            if path.is_file():
                archive.write(path, f"metadata/{path.name}")
        archive.writestr(
            "metadata/manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )

    manifest["archive_sha256"] = sha256_file(archive_path)
    manifest_path = BANK_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        for entry in stories:
            if entry["path"] not in names:
                raise RuntimeError(f"missing from archive: {entry['path']}")
            if sha256_bytes(archive.read(entry["path"])) != entry["sha256"]:
                raise RuntimeError(f"checksum mismatch: {entry['path']}")

    if mirror:
        destination = Path(mirror)
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive_path, destination / archive_name)
        shutil.copy2(manifest_path, destination / "manifest.json")
        manifest["mirror"] = str(destination)

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mirror",
        default=None,
        help="optional second folder to copy the archive and manifest into",
    )
    args = parser.parse_args()
    manifest = build_snapshot(args.mirror)

    print("snapshot:", manifest["archive"])
    print("stories:", manifest["story_count"], "| bank:", manifest["bank_count"])
    print("questions:", manifest["question_count"], "| finished:", manifest["finished_count"])
    print("levels:", manifest["levels"])
    print("target lengths:", manifest["target_lengths"])
    print("sha256:", manifest["archive_sha256"])
    if manifest.get("mirror"):
        print("mirror:", manifest["mirror"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
