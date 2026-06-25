"""Tests for environment-driven settings."""

from __future__ import annotations

import pytest

from frbe_tools.config import DEFAULT_WEB_PORT, load_settings


def test_web_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRBE_WEB_PORT", "9000")
    assert load_settings().web_port == 9000


def test_invalid_web_port_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo must not crash load_settings(), which every command calls.
    monkeypatch.setenv("FRBE_WEB_PORT", "oops")
    assert load_settings().web_port == DEFAULT_WEB_PORT


def test_blank_web_port_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRBE_WEB_PORT", "")
    assert load_settings().web_port == DEFAULT_WEB_PORT
