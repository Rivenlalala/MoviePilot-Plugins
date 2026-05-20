"""Tests for Phase 4: init_plugin category.yaml startup check (_check_category_yaml)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Add MoviePilot-Plugins/plugins.v2 to sys.path so the plugin package can be
# imported by name (the conftest only adds the plugin dir itself).
_PLUGINS_V2 = Path(__file__).resolve().parents[2]
if str(_PLUGINS_V2) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_V2))

import mygirlfriends  # noqa: E402
from mygirlfriends import MyGirlfriends  # noqa: E402

# ---------------------------------------------------------------------------
# Helper: build fake app.modules.themoviedb.category module
# ---------------------------------------------------------------------------

_CATEGORY_MODULE_KEY = "app.modules.themoviedb.category"


def _make_category_module(movie_categorys: dict) -> types.ModuleType:
    """Return a fake module exposing a CategoryHelper with given movie_categorys."""
    mod = types.ModuleType(_CATEGORY_MODULE_KEY)

    class _FakeCategoryHelper:
        @property
        def movie_categorys(self) -> dict:
            return movie_categorys

    mod.CategoryHelper = _FakeCategoryHelper
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_check_category_yaml_logs_when_jav_absent(monkeypatch):
    """When JAV key is absent from movie_categorys, logger.info is called once."""
    fake_mod = _make_category_module({"动画电影": {}})
    monkeypatch.setitem(sys.modules, _CATEGORY_MODULE_KEY, fake_mod)

    info_mock = MagicMock()
    monkeypatch.setattr(mygirlfriends.logger, "info", info_mock)

    plugin = MyGirlfriends()
    plugin._check_category_yaml()

    assert info_mock.call_count == 1, (
        f"Expected exactly 1 logger.info call, got {info_mock.call_count}"
    )
    call_arg = info_mock.call_args[0][0]
    assert "category.yaml" in call_arg, f"Expected 'category.yaml' in log message: {call_arg!r}"
    assert "JAV" in call_arg, f"Expected 'JAV' in log message: {call_arg!r}"


def test_check_category_yaml_silent_when_jav_present(monkeypatch):
    """When JAV key IS in movie_categorys, logger.info is NOT called."""
    fake_mod = _make_category_module({"JAV": {}, "动画电影": {}})
    monkeypatch.setitem(sys.modules, _CATEGORY_MODULE_KEY, fake_mod)

    info_mock = MagicMock()
    monkeypatch.setattr(mygirlfriends.logger, "info", info_mock)

    plugin = MyGirlfriends()
    plugin._check_category_yaml()

    assert info_mock.call_count == 0, (
        f"Expected 0 logger.info calls when JAV present, got {info_mock.call_count}"
    )


def test_check_category_yaml_swallows_import_error(monkeypatch):
    """When the import fails (no stub installed), _check_category_yaml returns cleanly."""
    # Ensure the module is NOT in sys.modules so ModuleNotFoundError is raised.
    monkeypatch.delitem(sys.modules, _CATEGORY_MODULE_KEY, raising=False)

    info_mock = MagicMock()
    monkeypatch.setattr(mygirlfriends.logger, "info", info_mock)

    plugin = MyGirlfriends()
    # Must not raise any exception.
    plugin._check_category_yaml()

    assert info_mock.call_count == 0, (
        f"Expected 0 logger.info calls on import error, got {info_mock.call_count}"
    )
