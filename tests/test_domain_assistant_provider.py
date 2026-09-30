"""Offline tests for the generation-provider layer of domain_assistant.py.

No network access: ``domain_assistant.OpenAI`` is replaced by a fake client,
and every provider environment variable is reset per test so the developer's
real ``.env`` (loaded at import time) never leaks into assertions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import domain_assistant as da  # noqa: E402

PROVIDER_VARS = (
    "AI_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_COMPATIBLE_BASE_URL",
    "OPENAI_COMPATIBLE_API_KEY",
    "OPENAI_COMPATIBLE_MODEL",
)
OPENAI_KEY = "sk-test-openai-SECRET-123"
COMPAT_KEY = "compat-test-SECRET-456"
COMPAT_URL = "http://localhost:8000/v1"
COMPAT_MODEL = "my-model"
CORPUS_DIR = ROOT / "data" / "technology_store"


class FakeOpenAI:
    """Records constructor kwargs and serves canned responses."""

    instances: list[FakeOpenAI] = []
    chat_content: Any = "Test answer"
    chat_choices_empty: bool = False

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.chat_calls: list[dict[str, Any]] = []
        self.responses_calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat_create))
        self.responses = SimpleNamespace(create=self._responses_create)
        FakeOpenAI.instances.append(self)

    def _chat_create(self, **kwargs: Any) -> Any:
        self.chat_calls.append(kwargs)
        if FakeOpenAI.chat_choices_empty:
            return SimpleNamespace(choices=[])
        message = SimpleNamespace(content=FakeOpenAI.chat_content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def _responses_create(self, **kwargs: Any) -> Any:
        self.responses_calls.append(kwargs)
        return SimpleNamespace(output_text="OpenAI answer")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)
    FakeOpenAI.instances = []
    FakeOpenAI.chat_content = "Test answer"
    FakeOpenAI.chat_choices_empty = False
    monkeypatch.setattr(da, "OpenAI", FakeOpenAI)


def _set_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")


def _set_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "compatible")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", COMPAT_URL)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", COMPAT_KEY)
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", COMPAT_MODEL)


# --- Provider selection ------------------------------------------------------

def test_openai_provider_creates_openai_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_openai(monkeypatch)
    generator = da.create_generator()
    assert isinstance(generator, da.OpenAIGenerator)
    assert FakeOpenAI.instances[0].kwargs == {"api_key": OPENAI_KEY}
    assert generator.generate("prompt") == "OpenAI answer"
    assert FakeOpenAI.instances[0].responses_calls[0]["model"] == "gpt-4o-mini"


def test_unset_provider_defaults_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    assert isinstance(da.create_generator(), da.OpenAIGenerator)


def test_compatible_provider_creates_compatible_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_compatible(monkeypatch)
    assert isinstance(da.create_generator(), da.OpenAICompatibleGenerator)


def test_provider_value_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_compatible(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "  Compatible ")
    assert isinstance(da.create_generator(), da.OpenAICompatibleGenerator)


def test_unsupported_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "abc")
    with pytest.raises(RuntimeError) as excinfo:
        da.create_generator()
    message = str(excinfo.value)
    assert "Unsupported AI_PROVIDER: abc" in message
    assert "openai, compatible" in message
    assert FakeOpenAI.instances == []


# --- Env validation ----------------------------------------------------------

def test_missing_openai_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_openai(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        da.create_generator()


def test_missing_openai_model_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_openai(monkeypatch)
    monkeypatch.delenv("OPENAI_MODEL")
    with pytest.raises(RuntimeError, match="OPENAI_MODEL"):
        da.create_generator()


@pytest.mark.parametrize(
    "missing",
    ["OPENAI_COMPATIBLE_BASE_URL", "OPENAI_COMPATIBLE_API_KEY", "OPENAI_COMPATIBLE_MODEL"],
)
def test_missing_compatible_var_raises(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _set_compatible(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError) as excinfo:
        da.create_generator()
    message = str(excinfo.value)
    assert missing in message
    assert "AI_PROVIDER=compatible" in message
    assert COMPAT_KEY not in message
    assert FakeOpenAI.instances == []


def test_compatible_does_not_fall_back_to_openai_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_openai(monkeypatch)  # official vars present ...
    monkeypatch.setenv("AI_PROVIDER", "compatible")  # ... but compatible selected
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", COMPAT_URL)
    with pytest.raises(RuntimeError) as excinfo:
        da.create_generator()
    message = str(excinfo.value)
    assert "OPENAI_COMPATIBLE_API_KEY" in message
    assert "OPENAI_COMPATIBLE_MODEL" in message


def test_invalid_base_url_raises_without_echoing_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_compatible(monkeypatch)
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "internal-host:8000/v1")
    with pytest.raises(RuntimeError, match="OPENAI_COMPATIBLE_BASE_URL") as excinfo:
        da.create_generator()
    assert "internal-host" not in str(excinfo.value)


# --- Compatible request ------------------------------------------------------

def test_compatible_client_uses_compatible_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_compatible(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)  # must be ignored
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")  # must be ignored
    generator = da.create_generator()
    answer = generator.generate("hello prompt")

    client = FakeOpenAI.instances[0]
    assert client.kwargs == {"api_key": COMPAT_KEY, "base_url": COMPAT_URL}
    call = client.chat_calls[0]
    assert call["model"] == COMPAT_MODEL
    assert call["messages"] == [{"role": "user", "content": "hello prompt"}]
    assert call["temperature"] == 0
    assert client.responses_calls == []
    assert answer == "Test answer"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com/v1", "https://example.com/v1"),
        ("  https://example.com/v1/  ", "https://example.com/v1"),
        ("http://localhost:8000/v1", "http://localhost:8000/v1"),
        ("http://127.0.0.1:1234/v1/", "http://127.0.0.1:1234/v1"),
        ("https://example.com/api/openai/v1", "https://example.com/api/openai/v1"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert da.normalize_base_url(raw) == expected


# --- Output parsing ----------------------------------------------------------

def test_compatible_output_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_compatible(monkeypatch)
    FakeOpenAI.chat_content = "  Test answer \n"
    assert da.create_generator().generate("p") == "Test answer"


@pytest.mark.parametrize("content", ["", "   ", None])
def test_compatible_empty_content_raises(
    monkeypatch: pytest.MonkeyPatch, content: Any
) -> None:
    _set_compatible(monkeypatch)
    FakeOpenAI.chat_content = content
    with pytest.raises(RuntimeError, match="empty answer"):
        da.create_generator().generate("p")


def test_compatible_no_choices_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_compatible(monkeypatch)
    FakeOpenAI.chat_choices_empty = True
    with pytest.raises(RuntimeError, match="no choices"):
        da.create_generator().generate("p")


# --- Dependency injection ----------------------------------------------------

class FakeGenerator:
    model = "fake-model"

    def generate(self, prompt: str) -> str:
        return "fake answer"


def _write_dataset(tmp_path: Path) -> Path:
    manifest = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
    dataset = {
        "corpus_id": manifest["corpus_id"],
        "qa_pairs": [{"id": "T01", "question": "What is the return window?"}],
    }
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    return path


def test_injected_generator_skips_provider_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "abc")  # would fail if env were read
    artifact = da.generate_actual_answers(
        _write_dataset(tmp_path), CORPUS_DIR, generator=FakeGenerator()
    )
    assert FakeOpenAI.instances == []
    assert artifact["answers"][0]["actual_answer"] == "fake answer"
    assert artifact["agent"]["model"] == "fake-model"
    assert artifact["agent"]["provider"] == "custom"


# --- Secret safety -----------------------------------------------------------

def test_artifact_and_logs_contain_no_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_compatible(monkeypatch)
    logs: list[str] = []
    artifact = da.generate_actual_answers(
        _write_dataset(tmp_path), CORPUS_DIR, progress=logs.append
    )

    agent = artifact["agent"]
    assert agent["provider"] == "compatible"
    assert agent["model"] == COMPAT_MODEL
    assert set(agent) == {"name", "provider", "model", "top_k", "prompt_version"}

    dumped = json.dumps(artifact)
    assert COMPAT_KEY not in dumped
    assert COMPAT_URL not in dumped
    assert all(COMPAT_KEY not in line for line in logs)
    assert any("provider=compatible" in line for line in logs)


def test_main_redacts_secret_in_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_compatible(monkeypatch)

    def leaky(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(f"401 Unauthorized for key {COMPAT_KEY}")

    monkeypatch.setattr(da, "generate_actual_answers", leaky)
    monkeypatch.setattr(sys, "argv", ["domain_assistant.py"])
    assert da.main() == 2
    output = capsys.readouterr().out
    assert COMPAT_KEY not in output
    assert "***" in output
