"""The .env reader: parsing, precedence, and the safety rails around it.

These are pure-file tests - no database, no browser.
"""

import os
import stat

import pytest

from app import env


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #

def test_parses_the_shapes_people_actually_write():
    parsed = env.parse(
        """
        # a comment
        PLAIN=value
        SPACED  =  value with spaces
        export EXPORTED=from-shell-style
        DOUBLE="quoted value"
        SINGLE='single quoted'
        TRAILING=value # a trailing comment
        QUOTED_HASH="keep # this"
        EMPTY=
        NOEQUALS
        """
    )

    assert parsed["PLAIN"] == "value"
    assert parsed["SPACED"] == "value with spaces"
    assert parsed["EXPORTED"] == "from-shell-style"
    assert parsed["DOUBLE"] == "quoted value"
    assert parsed["SINGLE"] == "single quoted"
    assert parsed["TRAILING"] == "value"
    assert parsed["QUOTED_HASH"] == "keep # this"
    assert parsed["EMPTY"] == ""
    assert "NOEQUALS" not in parsed


def test_a_connection_string_survives_parsing():
    """Atlas URIs contain :, /, ?, = and sometimes # - none may be mangled."""
    uri = ("mongodb+srv://user:p%40ss@cluster0.abcd.mongodb.net/xtimeline"
           "?retryWrites=true&w=majority")
    parsed = env.parse(f'XTIMELINE_MONGO_URI="{uri}"')
    assert parsed["XTIMELINE_MONGO_URI"] == uri


# --------------------------------------------------------------------------- #
#  Loading
# --------------------------------------------------------------------------- #

def test_loads_values_and_counts_them(tmp_path, monkeypatch):
    monkeypatch.delenv("XTIMELINE_SKIP_DOTENV", raising=False)
    monkeypatch.delenv("DEMO_ONE", raising=False)
    monkeypatch.delenv("DEMO_TWO", raising=False)

    path = tmp_path / ".env"
    path.write_text("DEMO_ONE=1\nDEMO_TWO=2\n", encoding="utf-8")

    assert env.load_dotenv(path) == path
    assert os.environ["DEMO_ONE"] == "1"
    assert env.loaded_count() == 2
    assert env.loaded_from() == path


def test_the_real_environment_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("XTIMELINE_SKIP_DOTENV", raising=False)
    monkeypatch.setenv("DEMO_THREE", "from-shell")

    path = tmp_path / ".env"
    path.write_text("DEMO_THREE=from-file\n", encoding="utf-8")

    env.load_dotenv(path)
    assert os.environ["DEMO_THREE"] == "from-shell"

    # ...unless you explicitly ask for the file to win.
    env.load_dotenv(path, override=True)
    assert os.environ["DEMO_THREE"] == "from-file"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert env.load_dotenv(tmp_path / "nope.env") is None


def test_the_skip_switch_disables_loading(tmp_path, monkeypatch):
    monkeypatch.setenv("XTIMELINE_SKIP_DOTENV", "1")
    monkeypatch.delenv("DEMO_FOUR", raising=False)

    path = tmp_path / ".env"
    path.write_text("DEMO_FOUR=4\n", encoding="utf-8")

    assert env.load_dotenv(path) is None
    assert "DEMO_FOUR" not in os.environ


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_a_world_readable_file_is_flagged(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("XTIMELINE_SKIP_DOTENV", raising=False)
    path = tmp_path / ".env"
    path.write_text("DEMO_FIVE=5\n", encoding="utf-8")
    path.chmod(0o644)

    with caplog.at_level("WARNING"):
        env.load_dotenv(path)

    assert any("chmod 600" in rec.getMessage() for rec in caplog.records)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_a_private_file_is_not_flagged(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("XTIMELINE_SKIP_DOTENV", raising=False)
    path = tmp_path / ".env"
    path.write_text("DEMO_SIX=6\n", encoding="utf-8")
    path.chmod(0o600)

    with caplog.at_level("WARNING"):
        env.load_dotenv(path)

    assert not any("chmod 600" in rec.getMessage() for rec in caplog.records)


# --------------------------------------------------------------------------- #
#  The shipped example
# --------------------------------------------------------------------------- #

def test_the_example_file_parses_and_documents_every_key():
    example = env.BACKEND_DIR / ".env.example"
    assert example.exists(), "backend/.env.example should ship with the app"

    parsed = env.parse(example.read_text(encoding="utf-8"))
    for key in (
        "XTIMELINE_PASSWORD",
        "XTIMELINE_MONGO_URI",
        "XTIMELINE_STORE",
        "XTIMELINE_SCROLL_DELAY_MS",
    ):
        assert key in parsed, f"{key} is missing from .env.example"

    # Nothing in the example may be a real credential.
    assert parsed["XTIMELINE_PASSWORD"] == "changeme"
    assert "user:password" in parsed["XTIMELINE_MONGO_URI"] or \
        parsed["XTIMELINE_MONGO_URI"].startswith("mongodb://127.0.0.1")


def test_tests_never_read_a_developers_real_env():
    """conftest sets the skip flag; a stray backend/.env must not leak in."""
    assert os.environ.get("XTIMELINE_SKIP_DOTENV") == "1"


# --------------------------------------------------------------------------- #
#  The default password is judged by value, not by presence
# --------------------------------------------------------------------------- #

def test_copying_the_example_still_warns_about_the_default_password(tmp_path, monkeypatch):
    from app import auth

    monkeypatch.delenv("XTIMELINE_SKIP_DOTENV", raising=False)
    monkeypatch.delenv("XTIMELINE_PASSWORD", raising=False)

    path = tmp_path / ".env"
    path.write_text("XTIMELINE_PASSWORD=changeme\n", encoding="utf-8")
    env.load_dotenv(path, override=True)

    # The variable now exists - but it is still the default password.
    assert auth.password() == "changeme"
    assert auth.using_default_password() is True

    monkeypatch.setenv("XTIMELINE_PASSWORD", "something-else")
    assert auth.using_default_password() is False
