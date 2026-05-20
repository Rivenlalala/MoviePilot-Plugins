"""Tests for the loose JAV code parser used by the search path.

MoviePilot's MetaInfo normalises hyphens to spaces and title-cases the prefix
before invoking the plugin (e.g. STARS-944 -> "Stars 944"), so the search
matcher must accept hyphen / whitespace / no-separator forms.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from javcode import (  # noqa: E402
    JavCodeParser,
    extract_jav_code,
    extract_jav_code_loose,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        # MoviePilot-normalised forms (the bug this fixes)
        ("Stars 944", "STARS-944"),
        ("Star 944", "STAR-944"),
        ("Ssis 001", "SSIS-001"),
        # Hyphenated (also hit by strict parser, fast-path)
        ("STARS-944", "STARS-944"),
        ("stars-944", "STARS-944"),
        ("SSIS-001", "SSIS-001"),
        # No separator
        ("STARS944", "STARS-944"),
        ("stars944", "STARS-944"),
        ("star944", "STAR-944"),
        ("ssis001", "SSIS-001"),
        # Padded with extra context
        ("search: stars 944 please", "STARS-944"),
        ("STARS-944 [uncensored]", "STARS-944"),
    ],
)
def test_loose_positive(text: str, expected: str) -> None:
    assert extract_jav_code_loose(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "H264",
        "H265",
        "X264",
        "X265",
        "AV1",
        "AC3",
        "DTS",
        "AAC",
        "FLAC",
        "BDRIP",
        "WEBRIP",
        "HDR10",
        "HDR",
        "REMUX",
        "BLURAY",
        # episode/season tokens — too few digits, regex requires 3+
        "S01",
        "E01",
        "S01E01",
        # bare text with no candidate
        "Some Random Movie",
        "",
    ],
)
def test_loose_negative(text: str) -> None:
    assert extract_jav_code_loose(text) is None


def test_loose_falls_back_to_strict_first() -> None:
    # Strict-matchable input should round-trip via the strict path.
    assert extract_jav_code_loose("SSIS-001") == extract_jav_code("SSIS-001")
    assert extract_jav_code_loose("FC2-PPV-1234567") == extract_jav_code("FC2-PPV-1234567")


def test_strict_parser_unchanged() -> None:
    # Strict parser must still reject hyphenless and whitespace-separated forms
    # so filename parsing in _build_mediainfo / on_transfer_rename keeps its
    # original false-positive guards.
    assert extract_jav_code("star944") is None
    assert extract_jav_code("Stars 944") is None
    assert extract_jav_code("STARS944") is None


def test_class_wrapper_extract_loose() -> None:
    assert JavCodeParser.extract_loose("Stars 944") == "STARS-944"
    assert JavCodeParser.extract_loose("H264") is None


def test_blocklist_skipped_when_real_code_follows() -> None:
    # If a blocklist token sits next to a real code, the real code wins.
    assert extract_jav_code_loose("H264 STARS-944") == "STARS-944"
    assert extract_jav_code_loose("H264 stars 944") == "STARS-944"
