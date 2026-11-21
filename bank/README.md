# Story bank snapshot

This folder contains a verified snapshot of the generated HSK story bank. The
snapshot is read-only by convention; the live working copy is `../stories/`.

## Snapshot

- Archive: `hsk-story-bank-2026-11-21.zip`
- Size: 1,684,383 bytes (1.68 MB)
- SHA-256: `6c3fccdae2eec2ce38520201daee3c1cdc2accdd270d37c6185447ee4fb67c42`
- Manifest: `manifest.json` (per-story SHA-256 + counts)
- Stories: **327** total (**324 bank** + sample + 2 earlier stories)
- Questions: **981** (every stored story has 3 A/B/C questions)
- Bank levels: HSK 1 = 93, HSK 2 = 58, HSK 3 = 173
- Target lengths: 150 = 274, 500 = 50, 60 = 0
- Finished flags: 0 marked at snapshot time

The archive contains:

```
stories/*.json                  all 327 story files
metadata/nouns_cache.json       noun lists used by the bank builder
metadata/seeds_used.json        seed history
metadata/hsk30_vocab.csv        syllabus vocabulary
metadata/manifest.json          embedded copy of the manifest
```

The second copy is mirrored at:

`C:\Users\liquidcat\Documents\HSK-Story-Bank-Backup\`

## Restore

Restore into a temporary folder first, then copy the stories you need:

```powershell
Expand-Archive hsk-story-bank-2026-11-21.zip -DestinationPath restore
Copy-Item restore/stories/*.json ../stories/ -Force
```

The working copies are ordinary JSON; `stories/sample.json` remains the
committed fixture. Back up the current `../stories/` before overwriting it.

## Verify

```powershell
Get-FileHash hsk-story-bank-2026-11-21.zip -Algorithm SHA256
```

Compare the result with `archive_sha256` in `manifest.json`. Each story's
`sha256` in the manifest can be checked against the extracted file.

## Make a new snapshot

Run this from the project root after adding or modifying stories:

```powershell
python bank/backup.py --mirror "C:\Users\liquidcat\Documents\HSK-Story-Bank-Backup"
```

The script creates a timestamped zip, a checksummed manifest, verifies every
story checksum, and optionally copies both to the mirror folder. It does not
change story generation or the live story files.

## Future aspects

The stored story JSON is the shared resource for reading, listening and future
exercise types. Future modules should add fields or companion resources
(`audio`, timing, per-user progress, additional question types) without
rewriting the generated text. `finished` is story-level today; per-user
progress belongs in a separate store keyed by story ID.
