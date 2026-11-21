import os

from env_loader import load_dotenv


def test_load_dotenv_reads_values_without_overriding(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# comment",
                "FOO=from-file",
                'BAR="quoted value"',
                "export BAZ=exported",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FOO", "from-env")
    monkeypatch.delenv("BAR", raising=False)
    monkeypatch.delenv("BAZ", raising=False)

    load_dotenv(path)

    assert os.environ["FOO"] == "from-env"
    assert os.environ["BAR"] == "quoted value"
    assert os.environ["BAZ"] == "exported"
    os.environ.pop("BAR", None)
    os.environ.pop("BAZ", None)


def test_load_dotenv_ignores_a_missing_file(tmp_path):
    load_dotenv(tmp_path / "nope.env")
