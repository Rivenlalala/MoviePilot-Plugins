"""Smoke tests for MyGirlfriends.get_form() shape and required fields."""

from __future__ import annotations

import sys
from pathlib import Path

# Add MoviePilot-Plugins/plugins.v2 to sys.path so the plugin package can be
# imported by name (the conftest only adds the plugin dir itself).
_PLUGINS_V2 = Path(__file__).resolve().parents[2]
if str(_PLUGINS_V2) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_V2))

import pytest  # noqa: E402

from mygirlfriends import MyGirlfriends  # noqa: E402


def _walk_form(nodes, results=None):
    """Recursively collect all component dicts from the form tree."""
    if results is None:
        results = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        results.append(node)
        children = node.get("content") or node.get("children") or []
        _walk_form(children, results)
    return results


def test_get_form_returns_two_tuple():
    form, defaults = MyGirlfriends().get_form()
    assert isinstance(form, list), "form should be a list"
    assert isinstance(defaults, dict), "defaults should be a dict"


def test_form_is_non_empty():
    form, _ = MyGirlfriends().get_form()
    assert len(form) > 0


def test_defaults_contain_required_keys():
    _, defaults = MyGirlfriends().get_form()
    for key in ("enabled", "server_url", "token", "recognize_media_enabled", "recognition_mode"):
        assert key in defaults, f"defaults missing key: {key!r}"


def test_form_contains_recognition_mode_vselect():
    form, _ = MyGirlfriends().get_form()
    all_nodes = _walk_form(form)
    matches = [
        n for n in all_nodes
        if n.get("component") == "VSelect"
        and (n.get("props") or {}).get("model") == "recognition_mode"
    ]
    assert matches, "No VSelect with model='recognition_mode' found in form"


def test_form_contains_recognize_media_enabled_vswitch():
    form, _ = MyGirlfriends().get_form()
    all_nodes = _walk_form(form)
    matches = [
        n for n in all_nodes
        if n.get("component") == "VSwitch"
        and (n.get("props") or {}).get("model") == "recognize_media_enabled"
    ]
    assert matches, "No VSwitch with model='recognize_media_enabled' found in form"


def test_recognition_mode_has_hijacking_option():
    form, _ = MyGirlfriends().get_form()
    all_nodes = _walk_form(form)
    select = next(
        (n for n in all_nodes
         if n.get("component") == "VSelect"
         and (n.get("props") or {}).get("model") == "recognition_mode"),
        None,
    )
    assert select is not None
    items = select["props"]["items"]
    values = [item["value"] for item in items]
    assert "hijacking" in values, f"recognition_mode items missing 'hijacking': {values}"
    assert "disabled" in values, f"recognition_mode items missing 'disabled': {values}"
