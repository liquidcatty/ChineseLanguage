"""Make the project root importable when pytest is run from anywhere."""

import os
import sys
from pathlib import Path

# Unit/UI tests must never touch a real Supabase project, even when a developer
# has a configured .env in the working tree.
os.environ["STORY_BACKEND"] = "files"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
