"""Tests for the JAV code parser.

Negative cases come from real-world release tags that the previous
``[A-Za-z]{2,10}-?\\d{3,8}`` regex used to mis-identify as JAV codes.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the parent mygirlfriends/ directory importable so `import javcode` works.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from javcode import JavCodeParser, extract_jav_code  # noqa: E402


# --- Positive cases ------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        # Standard codes
        ("SSIS-001", "SSIS-001"),
        ("ssis-001", "SSIS-001"),
        ("IPX-177", "IPX-177"),
        ("ABP-123456", "ABP-123456"),
        ("[Studio] SSIS-001 [Uncensored Leaked].mp4", "SSIS-001"),
        ("/library/jav/SSIS-001/SSIS-001-C.mp4", "SSIS-001"),
        ("/library/jav/SSIS-001/movie.mp4", "SSIS-001"),  # parent fallback
        # FC2-PPV
        ("FC2-PPV-1234567.mp4", "FC2-PPV-1234567"),
        ("FC2PPV-1234567", "FC2-PPV-1234567"),
        ("fc2ppv-987654", "FC2-PPV-987654"),
        # Uncensored families
        ("1PONDO-010122_001", "1PONDO-010122_001"),
        ("1pondo-010122_001.mp4", "1PONDO-010122_001"),
        ("CARIB-010122-001", "CARIB-010122-001"),
        ("CARIBBEAN-010122-001", "CARIBBEAN-010122-001"),
        ("CARIBBEANCOM-010122-001", "CARIBBEANCOM-010122-001"),
        ("HEYZO-2345", "HEYZO-2345"),
        ("heyzo-2345.mp4", "HEYZO-2345"),
        ("10MUSUME-010122_01", "10MUSUME-010122_01"),
        # Zero-prefix preserved
        ("ABC-001", "ABC-001"),
        ("XYZ-000123", "XYZ-000123"),
    ],
)
def test_positive_cases(text, expected):
    assert extract_jav_code(text) == expected


def test_class_wrapper_matches_module_function():
    assert JavCodeParser.extract("SSIS-001") == extract_jav_code("SSIS-001")


# --- Negative cases ------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "",
        "H264",
        "H265",
        "AC3",
        "AAC2",
        "DTS5",
        "EAC3",
        "S01E01",
        "1080P",
        "2160P",
        "4K",
        "x265",
        "x264",
        "BD25",
        "BDRip",
        "HEVC10",
        "10bit",
        "5CH",
        "2CH",
        "Some.Movie.2024.1080p.BluRay.x264-GROUP.mkv",
    ],
)
def test_negative_cases(text):
    assert extract_jav_code(text) is None


def test_none_for_empty_path():
    assert extract_jav_code("") is None


def test_does_not_match_naked_letters_digits_without_hyphen():
    # Old regex would have matched "ABC123" as ABC-123. New parser requires hyphen.
    assert extract_jav_code("ABC123") is None


def test_parent_dir_fallback():
    assert extract_jav_code("/data/SSIS-001/random_filename.mp4") == "SSIS-001"
