"""First-run setup: the .env file and the API key, without any launcher involved."""

from pathlib import Path

from cap import cli
from cap.setup import backend, ensure_env, read_key, write_key

EXAMPLE = "# comment\nCAP_DEV_REVIEWER_HEADER=true\nCAP_MODEL_BACKEND=mock\n# CAP_ANTHROPIC_API_KEY=\nCAP_RENDERER=video\n"


def _env(tmp_path: Path) -> Path:
    (tmp_path / ".env.example").write_text(EXAMPLE)
    return tmp_path / ".env"


def test_first_run_copies_example_and_asks(tmp_path: Path) -> None:
    env = _env(tmp_path)
    said: list[str] = []
    out = ensure_env(env, ask=lambda _: "sk-ant-test", out=said.append)
    assert out == "anthropic"
    assert read_key(env) == "sk-ant-test"
    text = env.read_text()
    assert "CAP_MODEL_BACKEND=anthropic" in text
    assert "CAP_RENDERER=video" in text  # other lines kept
    assert text.count("CAP_ANTHROPIC_API_KEY") == 1
    assert "sk-ant-test" not in "\n".join(said)  # never echoed


def test_second_run_does_not_ask_but_says_so(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ensure_env(env, ask=lambda _: "sk-ant-test", out=lambda _: None)
    said: list[str] = []
    calls: list[str] = []

    def ask(prompt: str) -> str:
        calls.append(prompt)
        return "sk-other"

    assert ensure_env(env, ask=ask, out=said.append) == "anthropic"
    assert calls == []
    assert any("already" in s or "saved in" in s for s in said)
    assert read_key(env) == "sk-ant-test"


def test_force_changes_the_key(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ensure_env(env, ask=lambda _: "sk-one", out=lambda _: None)
    ensure_env(env, force=True, ask=lambda _: "sk-two", out=lambda _: None)
    assert read_key(env) == "sk-two"
    assert env.read_text().count("CAP_ANTHROPIC_API_KEY") == 1


def test_blank_means_mock(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert ensure_env(env, ask=lambda _: "", out=lambda _: None) == "mock"
    assert read_key(env) is None
    assert "CAP_MODEL_BACKEND=mock" in env.read_text()
    assert backend(env) == "mock"


def test_non_interactive_never_blocks(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = _env(tmp_path)
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: False)})())
    assert ensure_env(env, out=lambda _: None) == "mock"
    assert env.exists()


def test_key_with_quotes_and_whitespace(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_key(env, '  "sk-quoted"  ')
    assert read_key(env) == "sk-quoted"


def test_cap_key_command(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text(EXAMPLE)
    monkeypatch.setattr("getpass.getpass", lambda _: "sk-cli")
    assert cli.main(["key", "--no-check"]) == 0
    assert read_key(tmp_path / ".env") == "sk-cli"
    out = capsys.readouterr().out
    assert "sk-cli" not in out
    assert "restart" in out
