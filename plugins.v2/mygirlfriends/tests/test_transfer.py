"""Tests for Phase 4: TransferRename handler + module-level _detect_jav_suffix / _detect_jav_part helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Make `mygirlfriends` importable by package name (mirrors test_search.py).
_PLUGINS_V2 = Path(__file__).resolve().parents[2]
if str(_PLUGINS_V2) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_V2))

from mygirlfriends import MyGirlfriends, _detect_jav_suffix, _detect_jav_part  # noqa: E402


# --- Fixtures / helpers --------------------------------------------------


def _make_plugin(enabled: bool = True, recognition_mode: str = "hijacking") -> MyGirlfriends:
    """Build a plugin bypassing ``init_plugin`` for hermetic unit tests."""
    plugin = MyGirlfriends()
    plugin._enabled = enabled
    plugin._recognition_mode = recognition_mode
    plugin._translate = False
    return plugin


def _make_event(rename_dict=None, source_path=None, updated=False, updated_str="", render_str=""):
    """Build a duck-typed Event with TransferRenameEventData fields."""
    event_data = SimpleNamespace(
        rename_dict=rename_dict or {},
        source_path=source_path,
        updated=updated,
        updated_str=updated_str,
        render_str=render_str,
        path=None,
        source=None,
    )
    return SimpleNamespace(event_data=event_data)


# --- Handler tests --------------------------------------------------------


def test_transfer_rename_rewrites_jav_path():
    """Happy path: JAV imdbid with .mkv source produces correct updated_str."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path="/dl/SSIS-001.mkv",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is True
    assert event.event_data.updated_str == "SSIS-001/SSIS-001.mkv"
    assert event.event_data.source == "MyGirlfriends"


def test_transfer_rename_ignores_non_jav():
    """Non-JAV imdbid (no 'jav:' prefix) leaves updated=False."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "tt1234567", "fileExt": ".mkv"},
        source_path="/dl/Inception.mkv",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is False


def test_transfer_rename_skips_when_disabled():
    """Disabled plugin leaves updated=False."""
    plugin = _make_plugin(enabled=False)
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path="/dl/SSIS-001.mkv",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is False


def test_transfer_rename_skips_when_mode_disabled():
    """Non-hijacking recognition_mode leaves updated=False."""
    plugin = _make_plugin(recognition_mode="disabled")
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path="/dl/SSIS-001.mkv",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is False


def test_transfer_rename_handles_none_source_path():
    """source_path=None must not raise; produces <CODE>/<CODE><ext> from fileExt."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path=None,
    )
    plugin.on_transfer_rename(event)
    # No exception raised; updated_str uses fileExt from rename_dict
    assert event.event_data.updated is True
    assert event.event_data.updated_str == "SSIS-001/SSIS-001.mkv"


def test_transfer_rename_swallows_exceptions():
    """Handler logs error and returns without raising when event_data raises."""

    class _BoomData:
        updated = False

        @property
        def rename_dict(self):
            raise RuntimeError("boom")

    event = SimpleNamespace(event_data=_BoomData())
    plugin = _make_plugin()
    plugin.on_transfer_rename(event)  # must not raise
    assert event.event_data.updated is False


def test_full_filename_with_suffix_and_part():
    """Source with both suffix (UC) and part (CD2) produces correct updated_str."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path="/dl/SSIS-001-UC-CD2.mkv",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is True
    assert event.event_data.updated_str == "SSIS-001/SSIS-001-UC-CD2.mkv"


def test_handler_uses_class_name_as_source():
    """event_data.source is set to the plugin's class name (not a hardcoded string)."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path="/dl/SSIS-001.mkv",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.source == plugin.__class__.__name__
    assert event.event_data.source == "MyGirlfriends"


def test_handler_prefers_fileExt_from_rename_dict_over_path_suffix():
    """fileExt from rename_dict takes priority over source_path's extension (Pitfall 6)."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path="/dl/SSIS-001.mp4",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is True
    assert event.event_data.updated_str.endswith(".mkv")


def test_handler_falls_back_to_path_suffix_when_fileExt_absent():
    """When rename_dict has no fileExt, extension comes from source_path (Pitfall 6)."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001"},
        source_path="/dl/SSIS-001.mp4",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is True
    assert event.event_data.updated_str.endswith(".mp4")


def test_handler_with_suffix_uc_from_brackets():
    """UC suffix detected from bracket-encoded source name like [FHD]SSIS-001UC[1080p]."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path="/dl/[FHD]SSIS-001UC[1080p].mkv",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is True
    assert event.event_data.updated_str == "SSIS-001/SSIS-001-UC.mkv"


def test_handler_with_part_cd2():
    """CD2 part token is appended to the filename."""
    plugin = _make_plugin()
    event = _make_event(
        rename_dict={"imdbid": "jav:SSIS-001", "fileExt": ".mkv"},
        source_path="/dl/SSIS-001-CD2.mkv",
    )
    plugin.on_transfer_rename(event)
    assert event.event_data.updated is True
    assert event.event_data.updated_str == "SSIS-001/SSIS-001-CD2.mkv"


# --- Pure helper: _detect_jav_suffix -------------------------------------


@pytest.mark.parametrize("stem,expected", [
    ("[FHD]SSIS-001UC[1080p]", "UC"),
    ("SSIS-001-C.extra",       "C"),
    ("SSIS-001-UC",            "UC"),
    ("SSIS-001-CH",            "CH"),
    ("SSIS-001-U",             "U"),
    ("SSIS-001-leak",          ""),
    ("SSIS-001",               ""),
])
def test_suffix_detection(stem, expected):
    """_detect_jav_suffix returns the whitelisted suffix after code position, or ''."""
    assert _detect_jav_suffix("SSIS-001", stem) == expected


def test_suffix_C_not_swallowed_by_CD1_part_token():
    """Assumption A2 regression guard: 'C' before digit must NOT match as suffix.

    The lookahead (?=[-_ \\[]|$) excludes digits, so 'CD1' is not a suffix match.
    If this test fails the suffix regex has a defect — do NOT loosen this assertion.
    """
    result = _detect_jav_suffix("SSIS-001", "SSIS-001-CD1")
    assert result == "", (
        f"Expected '' but got {result!r} — "
        "the suffix regex lookahead must exclude digits to avoid C/CD1 collision"
    )


# --- Pure helper: _detect_jav_part ---------------------------------------


@pytest.mark.parametrize("stem,expected", [
    ("SSIS-001-CD2",    "CD2"),
    ("SSIS-001-CD1",    "CD1"),
    ("SSIS-001-DISC1",  "DISC1"),
    ("SSIS-001-PART1",  "PART1"),
    ("SSIS-001",        ""),
    ("SSIS-001-UC-CD2", "CD2"),
])
def test_part_detection(stem, expected):
    """_detect_jav_part extracts part tokens from the source stem, or ''."""
    assert _detect_jav_part(stem) == expected


# --- Importability check -------------------------------------------------


def test_helpers_are_module_level_importable():
    """_detect_jav_suffix and _detect_jav_part are module-level (not methods)."""
    assert callable(_detect_jav_suffix)
    assert callable(_detect_jav_part)
    # Verify they work correctly as imported module-level functions
    assert _detect_jav_suffix("SSIS-001", "[FHD]SSIS-001UC[1080p]") == "UC"
    assert _detect_jav_part("SSIS-001-CD2") == "CD2"
