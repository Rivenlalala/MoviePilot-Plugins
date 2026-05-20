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


def test_form_contains_jav_setup_valert():
    """Test A: form contains a warning VAlert titled 'JAV 整理配置（必需）'."""
    form, _ = MyGirlfriends().get_form()
    all_nodes = _walk_form(form)
    matches = [
        n for n in all_nodes
        if n.get("component") == "VAlert"
        and (n.get("props") or {}).get("title") == "JAV 整理配置（必需）"
    ]
    assert len(matches) == 1, (
        f"Expected exactly 1 VAlert with title='JAV 整理配置（必需）', found {len(matches)}"
    )
    valert = matches[0]
    assert (valert.get("props") or {}).get("type") == "warning", (
        f"Expected props.type=='warning', got {(valert.get('props') or {}).get('type')!r}"
    )
    assert (valert.get("props") or {}).get("variant") == "tonal", (
        f"Expected props.variant=='tonal', got {(valert.get('props') or {}).get('variant')!r}"
    )


def test_jav_setup_valert_has_four_step_spans():
    """Test B: the JAV setup VAlert has four span children with the required key substrings."""
    form, _ = MyGirlfriends().get_form()
    all_nodes = _walk_form(form)
    matches = [
        n for n in all_nodes
        if n.get("component") == "VAlert"
        and (n.get("props") or {}).get("title") == "JAV 整理配置（必需）"
    ]
    assert len(matches) == 1
    valert = matches[0]
    content = valert.get("content") or []
    assert len(content) == 4, f"Expected 4 span children, got {len(content)}"
    for i, child in enumerate(content):
        assert child.get("component") == "span", (
            f"child {i}: expected component=='span', got {child.get('component')!r}"
        )
        assert isinstance(child.get("text"), str) and len(child["text"]) > 0, (
            f"child {i}: expected non-empty text string"
        )
    s = "\n".join(c["text"] for c in content)
    required_substrings = [
        "category.yaml", "movie:", "电影", "JAV媒体库", "刮削=关闭",
        "劫持", "hijacking", "Jellyfin", "MetaTube", "<CODE>",
    ]
    for sub in required_substrings:
        assert sub in s, f"Concatenated span text missing required substring: {sub!r}"
    assert "transfer_type=link" not in s, "Span text must not prescribe transfer_type=link (D-11)"
    assert "transfer_type=hardlink" not in s, "Span text must not prescribe transfer_type=hardlink (D-11)"
