import json
from argparse import Namespace

import sync_supabase


def write_json(path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_load_local_forces_file_stem_and_reports_problems(tmp_path):
    write_json(tmp_path / "bank-l1-abc.json", {"id": "other", "title": "t"})
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

    stories, problems = sync_supabase.load_local(tmp_path)

    assert [story["id"] for story in stories] == ["bank-l1-abc"]
    assert any("id 'other'" in problem for problem in problems)
    assert any("bad.json" in problem for problem in problems)


def test_push_dry_run_touches_no_network(tmp_path, monkeypatch):
    write_json(tmp_path / "s1.json", {"id": "s1", "title": "t"})

    def explode(*args, **kwargs):
        raise AssertionError("network call during dry run")

    monkeypatch.setattr(sync_supabase.supabase_store, "save_stories", explode)
    args = Namespace(dir=str(tmp_path), limit=0, dry_run=True)
    assert sync_supabase.cmd_push(args) == 0


def test_push_uploads_every_local_story(tmp_path, monkeypatch):
    for index in range(3):
        write_json(tmp_path / f"s{index}.json", {"id": f"s{index}", "title": "t"})
    sent = []

    def fake_save(chunk, batch_size=0):
        sent.extend(chunk)
        return len(chunk)

    monkeypatch.setattr(sync_supabase.supabase_store, "save_stories", fake_save)
    args = Namespace(dir=str(tmp_path), limit=0, dry_run=False)
    assert sync_supabase.cmd_push(args) == 0
    assert sorted(story["id"] for story in sent) == ["s0", "s1", "s2"]


def test_push_honours_limit(tmp_path, monkeypatch):
    for index in range(3):
        write_json(tmp_path / f"s{index}.json", {"id": f"s{index}", "title": "t"})
    sent = []

    def fake_save(chunk, batch_size=0):
        sent.extend(chunk)
        return len(chunk)

    monkeypatch.setattr(sync_supabase.supabase_store, "save_stories", fake_save)
    args = Namespace(dir=str(tmp_path), limit=2, dry_run=False)
    assert sync_supabase.cmd_push(args) == 0
    assert [story["id"] for story in sent] == ["s0", "s1"]


def test_pull_writes_remote_stories(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sync_supabase.supabase_store,
        "fetch_stories",
        lambda: [{"id": "r1", "title": "云端故事"}],
    )
    args = Namespace(out=str(tmp_path / "restore"), dry_run=False)
    assert sync_supabase.cmd_pull(args) == 0
    saved = json.loads((tmp_path / "restore" / "r1.json").read_text(encoding="utf-8"))
    assert saved["title"] == "云端故事"


def test_main_requires_configuration(monkeypatch):
    for name in (
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    assert sync_supabase.main(["status"]) == 2
