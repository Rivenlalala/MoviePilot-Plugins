"""JAV code parser.

Extracts a canonical JAV identifier (``PREFIX-NUMBER``) from a filename, path
component, or arbitrary text. Designed to avoid false positives on the
release-tag tokens that commonly appear alongside video filenames
(``H264``, ``AC3``, ``S01E01``, ``1080P``, ``x265``, etc.) by *requiring* a
hyphen between prefix and number for the generic case.

Supported families
------------------
* Standard codes: ``[A-Z]{2,6}-\\d{3,6}`` (hyphen required).
* FC2-PPV codes: ``FC2-PPV-######`` and ``FC2PPV-######``.
* Uncensored studio formats:
  - ``1PONDO-######_###`` (date_segment)
  - ``CARIB(BEAN(COM)?)?-######-###``
  - ``HEYZO-####``
  - ``10MUSUME-######_##``

All patterns are evaluated in priority order; the first family that matches a
candidate substring wins. The returned value is always uppercased and
canonicalised, with the original digit groups (and any zero prefix)
preserved.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

__all__ = ["extract_jav_code", "extract_jav_code_loose", "JavCodeParser"]


# Release-tag tokens the loose matcher must NOT treat as JAV codes. MoviePilot's
# MetaInfo strips most of these before search reaches our plugin, but defending
# here lets the loose parser stay safe if a user pastes a raw filename into the
# search box.
_LOOSE_BLOCKLIST = frozenset({
    "H264", "H265", "X264", "X265", "AV1",
    "AC3", "DTS", "AAC", "FLAC",
    "MP4", "MKV", "AVI",
    "BD", "BDRIP", "BLURAY", "WEBRIP", "HDRIP", "DVDRIP", "REMUX",
    "HDR", "HDR10",
})

# Loose matcher: prefix and number separated by hyphen, whitespace, or nothing.
# Used only by extract_jav_code_loose for user-typed search input. Filename
# parsing keeps the strict _STANDARD_PATTERN above.
_STANDARD_PATTERN_LOOSE = re.compile(r"\b([A-Za-z]{2,6})[\s\-]*(\d{3,6})\b")


_UNCENSORED_PATTERNS = (
    re.compile(r"\b(FC2-?PPV)-?(\d{6,8})\b", re.IGNORECASE),
    re.compile(r"\b(1PONDO)-(\d{6})_(\d{3})\b", re.IGNORECASE),
    re.compile(r"\b(CARIB(?:BEAN(?:COM)?)?)-(\d{6})-(\d{3})\b", re.IGNORECASE),
    re.compile(r"\b(10MUSUME)-(\d{6})_(\d{2})\b", re.IGNORECASE),
    re.compile(r"\b(HEYZO)-(\d{4})\b", re.IGNORECASE),
)

_STANDARD_PATTERN = re.compile(r"\b([A-Za-z]{2,6})-(\d{3,6})\b")


def _normalize_standard(match: "re.Match[str]") -> str:
    return f"{match.group(1).upper()}-{match.group(2)}"


def _normalize_uncensored(match: "re.Match[str]") -> str:
    prefix = match.group(1).upper()
    if prefix == "FC2PPV":
        prefix = "FC2-PPV"
    tail = "-".join(g for g in match.groups()[1:] if g is not None)
    # 1pondo and 10musume use underscore between date and segment; rebuild faithfully.
    if prefix in ("1PONDO", "10MUSUME"):
        return f"{prefix}-{match.group(2)}_{match.group(3)}"
    if prefix.startswith("CARIB"):
        return f"{prefix}-{match.group(2)}-{match.group(3)}"
    return f"{prefix}-{tail}"


def _search_in(text: str) -> Optional[str]:
    if not text:
        return None
    for pattern in _UNCENSORED_PATTERNS:
        m = pattern.search(text)
        if m:
            return _normalize_uncensored(m)
    m = _STANDARD_PATTERN.search(text)
    if m:
        return _normalize_standard(m)
    return None


def extract_jav_code(text: str) -> Optional[str]:
    """Return canonical ``PREFIX-NUMBER`` form or ``None`` if no JAV code is found.

    Accepts a bare string or a path-like string. When given a path, both the
    file stem and the immediate parent directory name are searched (mirrors
    the historic ``_extract_jav_number`` behaviour).
    """
    if not text:
        return None

    # Try as-is first (covers bare titles like "SSIS-001 [uncensored]").
    code = _search_in(text)
    if code:
        return code

    # Path-aware fallback: stem then parent name.
    try:
        p = Path(text)
    except (TypeError, ValueError):
        return None

    stem_code = _search_in(p.stem)
    if stem_code:
        return stem_code

    parent = p.parent.name
    if parent and parent != text:
        return _search_in(parent)
    return None


def extract_jav_code_loose(text: str) -> Optional[str]:
    """Like :func:`extract_jav_code` but tolerates user-typed search input.

    Tries the strict parser first; on miss, falls back to a looser pattern that
    accepts hyphen, whitespace, or no separator between prefix and number. Used
    by ``search_medias`` because MoviePilot's ``MetaInfo`` normalises hyphens
    to spaces and title-cases the prefix before the plugin is invoked
    (e.g. ``STARS-944`` -> ``Stars 944``).

    Blocks common release-tag tokens (``H264``, ``X265`` etc.) to keep
    false-positive risk near zero. Strict callers (filename parsing in
    ``_build_mediainfo`` / ``on_transfer_rename``) keep using
    :func:`extract_jav_code` directly.
    """
    if not text:
        return None
    strict = extract_jav_code(text)
    if strict:
        return strict
    for m in _STANDARD_PATTERN_LOOSE.finditer(text):
        prefix = m.group(1).upper()
        number = m.group(2)
        if prefix in _LOOSE_BLOCKLIST:
            continue
        return f"{prefix}-{number}"
    return None


class JavCodeParser:
    """Object wrapper around :func:`extract_jav_code` for callers that prefer a class."""

    @staticmethod
    def extract(text: str) -> Optional[str]:
        return extract_jav_code(text)

    @staticmethod
    def extract_loose(text: str) -> Optional[str]:
        return extract_jav_code_loose(text)
