"""Tests for the lightpanda-based JavaScript renderer."""

from __future__ import annotations

import shutil
import subprocess
import types

import pytest

from cursustrace import renderer


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub_subprocess(monkeypatch: pytest.MonkeyPatch, run: object) -> None:
    namespace = types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(renderer, "subprocess", namespace)


def test_render_html_returns_document(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _run(command: list[str], **kwargs: object) -> _FakeCompleted:
        captured["command"] = command
        captured.update(kwargs)
        return _FakeCompleted(stdout="<html><body>Rendered</body></html>")

    monkeypatch.setattr(renderer, "find_binary", lambda: "/usr/bin/lightpanda")
    _stub_subprocess(monkeypatch, _run)

    result = renderer.render_html("https://example.com/job")

    assert result == "<html><body>Rendered</body></html>"
    command = captured["command"]
    assert isinstance(command, list)
    assert command[:4] == ["/usr/bin/lightpanda", "fetch", "https://example.com/job", "--dump"]
    assert "html" in command
    assert "done" in command
    assert captured["timeout"] == renderer.RENDER_TIMEOUT_SECONDS


def test_render_html_returns_none_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "find_binary", lambda: None)

    def _unexpected(command: list[str], **kwargs: object) -> _FakeCompleted:
        raise AssertionError("subprocess must not run without a binary")

    _stub_subprocess(monkeypatch, _unexpected)
    assert renderer.render_html("https://example.com/job") is None


def test_render_html_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "find_binary", lambda: "/usr/bin/lightpanda")

    def _timeout(command: list[str], **kwargs: object) -> _FakeCompleted:
        raise subprocess.TimeoutExpired(command, renderer.RENDER_TIMEOUT_SECONDS)

    _stub_subprocess(monkeypatch, _timeout)
    assert renderer.render_html("https://example.com/job") is None


def test_render_html_returns_none_on_failed_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "find_binary", lambda: "/usr/bin/lightpanda")
    _stub_subprocess(
        monkeypatch, lambda *args, **kwargs: _FakeCompleted(returncode=1, stderr="boom")
    )
    assert renderer.render_html("https://example.com/job") is None


def test_render_html_returns_none_on_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "find_binary", lambda: "/usr/bin/lightpanda")
    _stub_subprocess(monkeypatch, lambda *args, **kwargs: _FakeCompleted(stdout="   \n"))
    assert renderer.render_html("https://example.com/job") is None


def test_find_binary_prefers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _which(name: str) -> str | None:
        seen.append(name)
        return "/opt/bin/lightpanda"

    monkeypatch.setenv(renderer.ENV_BINARY, "/opt/bin/lightpanda")
    monkeypatch.setattr(shutil, "which", _which)

    assert renderer.find_binary() == "/opt/bin/lightpanda"
    assert seen == ["/opt/bin/lightpanda"]


def test_find_binary_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _which(name: str) -> str | None:
        seen.append(name)
        return "/usr/local/bin/lightpanda" if name == "lightpanda" else None

    monkeypatch.delenv(renderer.ENV_BINARY, raising=False)
    monkeypatch.setattr(shutil, "which", _which)

    assert renderer.find_binary() == "/usr/local/bin/lightpanda"
    assert seen == ["lightpanda"]
