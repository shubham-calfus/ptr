import os
from pathlib import Path

from src.utils.runtime_env import get_runner_env_value, load_runner_local_env, parse_runner_dotenv


def test_parse_runner_dotenv_handles_export_quotes_and_comments(tmp_path: Path) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "# comment",
                "OPENAI_API_KEY=sk-proj-demo",
                'export OPENAI_BASE_URL="https://api.openai.com/v1"',
                "PTR_AI_SELF_REPAIR_ENABLED=true",
                "INVALID LINE",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_runner_dotenv(dotenv_path) == {
        "OPENAI_API_KEY": "sk-proj-demo",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "PTR_AI_SELF_REPAIR_ENABLED": "true",
    }


def test_load_runner_local_env_overrides_shell_for_openai_keys(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-from-shell")
    monkeypatch.setenv("PTR_AI_SELF_REPAIR_ENABLED", "false")
    monkeypatch.setenv("PTR_CAPTURE_STEPS", "true")

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-proj-from-dotenv",
                "PTR_AI_SELF_REPAIR_ENABLED=true",
                "PTR_CAPTURE_STEPS=false",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_runner_local_env(dotenv_path)

    assert loaded == {
        "OPENAI_API_KEY": "sk-proj-from-dotenv",
        "PTR_AI_SELF_REPAIR_ENABLED": "true",
    }
    assert os.getenv("OPENAI_API_KEY") == "sk-proj-from-dotenv"
    assert os.getenv("PTR_AI_SELF_REPAIR_ENABLED") == "true"
    assert os.getenv("PTR_CAPTURE_STEPS") == "true"


def test_get_runner_env_value_falls_back_to_dotenv_when_shell_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("OPENAI_API_KEY=sk-proj-from-dotenv\n", encoding="utf-8")

    assert get_runner_env_value("OPENAI_API_KEY", dotenv_path=dotenv_path) == "sk-proj-from-dotenv"
