# HSK 3.0 story generator

Generates short stories in simplified Chinese at a target HSK 3.0 level with an
iterative **generate → segment → rate → repair** loop powered by DeepSeek, plus
a browser library, a reading interface, a listening preview and a shared
comprehension quiz.

## Requirements

- Python 3.11+
- `pip install -r requirements.txt`
- `python -m playwright install chromium` (for the browser checks)
- `DEEPSEEK_API_KEY` in the environment or in `.env` (copy `.env.example`;
  never commit the real key).  The placeholder value in the template counts as
  "not configured".

Optional: `DEEPSEEK_MODEL` (default `deepseek-flash`, i.e. DeepSeek-V4.1-Flash).

Optional: a Supabase project for cloud story storage (see
[Supabase storage](#supabase-storage)).  Without it the app reads and writes
`stories/*.json` exactly as before.

## Web app

```bash
uvicorn app:app --reload
# /                -> story library
# /read/<id>       -> reading interface
# /listen/<id>     -> listening interface
```

There are three pages:

- **Library** (`/`): all saved stories as title tiles, grouped by HSK level,
  with a level filter and title/noun search. Tabs switch between **Reading**
  and **Listening**; the same tiles point to `/read/<id>` or `/listen/<id>`.
  Every tile has a shared **Finished** checkbox.
- **Reader** (`/read/<id>`): story title/HSK/character metadata, pinyin toggle
  (off / new words / all), tap-to-translate word popovers and the
  comprehension quiz when the story has one.
- **Listening** (`/listen/<id>`): browser speech-synthesis **Play** preview,
  show/hide transcript, the same comprehension quiz and the shared Finished
  checkbox.

There are **no generation controls in the web UI**; the backend
`POST /api/story`, `generate.py` and the resumable bank scripts still work.
`stories/sample.json` is a committed fixture, so the library, reader and
listening preview work without an API key. All word data is enriched once at
generation time; the reading UI never calls an LLM.

Tokens look like:

```json
{"w": "奶奶", "pinyin": ["nǎi", "nai"], "gloss_pl": "babcia",
 "gloss_en": "grandma", "level": 2, "status": "ok", "proper": false}
```

`finished` is shared through `POST /api/stories/<id>/finished`.

## Usage

```bash
python generate.py --level 3 --length 200
python generate.py --level 1 --length 180 --seed-noun 书 --out story.json
python generate.py --level 4 --length 400 --max-new-words 2 --rng-seed 7
python generate.py --level 3 --length 200 --questions 3 --out story.json
python quiz.py story.json
```

`--level` accepts `1`-`6` or `7-9` (levels 7-9 share one syllabus list).
`--length` counts Han characters only (punctuation excluded) with a ±15% window.
`--questions N` adds N three-option (A/B/C) multiple-choice questions to the
output, at every level.

The JSON report contains `level`, `seed_noun`, `title`, `text`, `sentences`,
`tokens` (per-word pinyin, Polish/English gloss, level and status), `new_words`
(remaining OOV words), `finished`, `metrics` (`n_han`, `oov_count`, `rounds`,
`converged`, `usage`) and `log` (OOV words per round).  With `--questions` it
also contains `questions` (with enriched `question_tokens` / `option_tokens`),
`quiz_metrics` (`rounds`, `failed_by_reason`, `usage`) and `quiz_complete`.

Each question is checked before it is accepted: schema (three options, an
A/B/C answer key and in-range evidence indices), vocabulary (no above-level
word the reader has not already seen in the story) and a blind answer from a
second model call that never sees the key.  Failed questions are regenerated
with feedback for up to four rounds.

## Story bank

The reading bank was generated once from the HSK 1–3 nouns and is treated as a
fixed data asset:

| HSK level | Nouns | Stories |
|---|---:|---:|
| 1 | 93 | 93 |
| 2 | 58 | 58 |
| 3 | 173 | 173 |
| **Total** | **324** | **324** |

Length allocation was the requested **0 / 85 / 15** over **60 / 150 / 500**
characters:

| Target | Stories |
|---:|---:|
| 60 | 0 |
| 150 | 274 |
| 500 | 50 |

Adding the sample and two earlier stories, `stories/` contains **327 stories**
and **981 questions** (every stored story has three A/B/C questions).

### How the bank was generated

1. **Vocabulary and nouns.** `hsk30_vocab.csv` holds the 10,896 unique words
   from the 2025 HSK 3.0 syllabus. For each of HSK 1, 2 and 3, the exact-level
   words were sent to DeepSeek in batches of 200 and filtered down to concrete
   nouns; the result is cached in `nouns_cache.json`. This produced 93, 58 and
   173 nouns respectively.
2. **Length choice.** Each noun deterministically draws one length with the
   weights 0/85/15. That yielded 274 stories at 150 characters and 50 at 500;
   no 60-character stories were generated.
3. **Story generation.** For every noun, `story.generate_story` was called with
   the noun forced as `seed_noun`, `max_new_words=3`, and no question
   generation. The draft is written at temperature 1.0; segmentation by the
   model (temperature 0) returns pinyin and Polish/English glosses for every
   word; the grader flags OOV words; repairs rewrite flagged sentences at 0.3
   for at most six rounds. Pinyin arrays that fail the one-syllable-per-
   character check fall back to `pypinyin`.
4. **Bank metadata.** Each story was saved atomically as
   `stories/bank-l<level>-<hash>.json` with `bank`, `bank_level`, `bank_noun`,
   `target_length`, `finished: false`, `created` and `id`. The builder is
   resumable and ran with several workers; existing files were skipped.
5. **Questions.** A second resumable pass (`add_questions.py`) generated three
   A/B/C questions per story, validated schema (three options, in-range
   evidence, A/B/C key), vocabulary (no unseen above-level words) and a blind
   answer from a second model call. Failed questions were regenerated for up to
   four rounds; repeated passes got every bank story to 3/3.
6. **Finished state.** `finished` is stored in every story JSON and shared by
   the library, reader and listening pages through
   `POST /api/stories/<id>/finished`. It was migrated to all existing stories
   with `false` as the default.

The generation process itself is intentionally unchanged after producing the
bank: the stories are the source of truth, and future work should add data
fields or companion resources rather than rewriting generated text.

### Rebuilding or adding to the bank

```bash
python build_bank.py --dry-run                 # noun counts + length distribution
python build_bank.py --workers 6               # resumable, one story per noun
python add_questions.py --workers 8            # add the three questions in place
```

`build_bank.py` skips existing story IDs; `add_questions.py` skips stories that
already have three questions and retries incomplete ones.

## Storage and restore

A verified snapshot of the finished bank lives in `bank/`:

- `bank/hsk-story-bank-2026-11-21.zip` — all 327 stories, the noun/seed
  caches, `hsk30_vocab.csv` and an embedded manifest.
- `bank/manifest.json` — counts, level/length distribution, SHA-256 for every
  story, and the archive checksum.
- `bank/README.md` — restore instructions.
- A second copy is mirrored at
  `C:\Users\liquidcat\Documents\HSK-Story-Bank-Backup\`.

Create a fresh snapshot (and a second copy) at any time:

```bash
python bank/backup.py --mirror "C:\Users\liquidcat\Documents\HSK-Story-Bank-Backup"
```

To restore into a working directory:

```powershell
Expand-Archive bank/hsk-story-bank-2026-11-21.zip -DestinationPath restore
Copy-Item restore/stories/*.json stories/ -Force
```

`stories/*.json` is git-ignored except `stories/sample.json`; the `bank/`
snapshot and manifest are not ignored, so they can be committed or copied
off-machine. The archive is 1.68 MB.  When Supabase is configured the local
files are a mirror of the cloud rows (see [Supabase storage](#supabase-storage));
`.env`, which holds the project URL and secret key, is git-ignored.

## Supabase storage

Stories can live in Supabase instead of (or in addition to) the local
`stories/*.json` files.  The FastAPI backend is the only component that talks to
Supabase: it keeps the project's **secret key** on the server and proxies
`GET /api/stories`, `GET /api/stories/<id>`, `POST /api/story` and the
`finished` toggle, so the browser never receives a key.

1. Create a project at <https://supabase.com>.
2. Open **SQL Editor**, paste [`supabase_schema.sql`](supabase_schema.sql) and
   run it.  It creates `public.stories` (indexed summary columns plus a `data
   jsonb` payload) and enables RLS with **no** public policy, so only the secret
   key can read or write it.
3. Put the project URL and the secret key in `.env` (git-ignored; start from
   `.env.example`):

   ```dotenv
   SUPABASE_URL=https://<project-ref>.supabase.co
   SUPABASE_KEY=sb_secret_...
   ```

   `SUPABASE_KEY` may also be provided as `SUPABASE_SECRET_KEY` or the legacy
   `SUPABASE_SERVICE_ROLE_KEY`.  The publishable key is **not** used by the
   server and cannot read the table.
4. Upload the existing story bank:

   ```bash
   python sync_supabase.py status   # local vs remote ids
   python sync_supabase.py push     # upsert stories/*.json in batches
   ```

After that, `uvicorn app:app` reads and writes Supabase automatically.  Backend
selection:

| `STORY_BACKEND` | behaviour |
| --- | --- |
| unset / `auto` | Supabase when `SUPABASE_URL` and a key are set, else local files |
| `files` | force `stories/*.json` (the test suite uses this) |
| `supabase` | force Supabase |

With Supabase in use, every write still mirrors to `stories/<id>.json`
(`STORY_LOCAL_MIRROR=0` disables that), so `bank/backup.py`,
`add_questions.py`, `build_bank.py` and the CLI keep working unchanged.  Reads
fall back to the local file when a row is missing or the network is down, so an
offline checkout keeps serving the last known copy.  `python
sync_supabase.py pull --out restore` writes remote stories back as JSON files.
`finished` is stored in the `finished` column and inside `data.finished`; the
API updates both together.

## Future aspects

The story JSON is the shared substrate for every presentation layer:

- **Reading** uses `sentences`, `tokens`, `questions` and `finished`.
- **Listening** currently uses the same text/tokens/questions with browser
  speech synthesis; a future audio module can add `audio` (and timing) fields
  without changing the text or question data.
- **Questions** are typed (`mc` now; TF is handled by the UI for legacy data);
  future exercise types can add new `type` values while reusing the same
  evidence/token model.
- **Progress**: `finished` is a shared story-level flag. Per-user progress,
  scores or playback positions should live in a separate store keyed by story
  ID rather than overloading the generated story.
- **Library/reader/listening** are views over the API; generation stays in
  `generate.py`, `POST /api/story`, `build_bank.py` and `add_questions.py`.
  New features should be additive and backward-compatible, with `finished`
  defaulting to `false` for older stories.
- **Backups** are part of the workflow: snapshot after every batch or new
  derived resource, and keep the mirror off the project disk.

## Files

| file | purpose |
| --- | --- |
| `vocab.py` | loads `hsk30_vocab.csv`, level ranks, replacement candidates |
| `llm.py` | OpenAI-compatible DeepSeek client, JSON mode, retries, usage log |
| `env_loader.py` | tiny `.env` reader shared by the DeepSeek and Supabase clients |
| `.env.example` | committed template with placeholders (real `.env` is git-ignored) |
| `grader.py` | LLM segmentation, Han-only length, per-word HSK rating |
| `story.py` | seed nouns, prompts, repair loop, output schema |
| `questions.py` | question generation, schema/vocabulary/blind validation |
| `checker.py` | deterministic answer normalization, scoring and evidence |
| `app.py` | FastAPI server: library, reader, listening, story/check/finished APIs |
| `supabase_store.py` | PostgREST client and story-row mapping used by the API and sync script |
| `sync_supabase.py` | `status` / `push` / `pull` migration between `stories/` and Supabase |
| `supabase_schema.sql` | `stories` table, `updated_at` trigger and RLS lockdown for Supabase |
| `static/` | library, reader and listening pages (`index.html`, `reader.html`, `listening.html`, JS, CSS) |
| `stories/` | live saved stories; `sample.json` is the committed offline fixture |
| `build_bank.py` | resumable one-story-per-noun bank builder |
| `add_questions.py` | adds the three questions to existing stories in place |
| `bank/` | verified snapshot + checksummed manifest + restore notes |
| `bank/backup.py` | creates a compressed, checksummed snapshot and optional mirror |
| `generate.py` | command-line story + question generator |
| `quiz.py` | interactive terminal quiz |
| `tests/` | pytest suite (mocked LLM) plus Playwright browser checks |

`hsk30_vocab.csv` (10,896 unique words, levels 1-6 and 7-9) is derived from the
November 2025 HSK 3.0 syllabus data published in
`chelsea6502/hsk-2025-data`; duplicate homographs are collapsed to the lowest
level.

`nouns_cache.json` and `seeds_used.json` are created on first real run and are
git-ignored.

## Tests

```bash
python -m pytest -q
```

The unit tests mock every LLM call, so they run offline and without a key.  The
Playwright tests start a local server on port 8765 with the sample story and
check the title library (Reading/Listening), finished checkboxes, the reader
popover/quiz, the listening transcript/quiz and mobile layout.  They write
`screenshots/library-desktop.png`, `library-listening.png`,
`library-mobile.png`, `listening.png`, `desktop.png`, `mobile.png`,
`mobile-popover.png` and `quiz.png`.  Install Chromium once with
`python -m playwright install chromium`.
