"""Tests for S02: search_medias + multi-provider merge + MediaRecognizeConvert.

Covers the new public surface added in commit 03f1730:
- ``search_medias`` / ``async_search_medias`` (S02 T01)
- ``_search_and_merge`` shared helper (S02 T01)
- ``_merge_details`` field-wise non-empty selection (S02 T01)
- ``_providers`` filtering with fallback to top result (S02 T01)
- ``imdb_id = "jav:CODE"`` threading on ``_build_mediainfo`` (S02 T01)
- ``async_media_recognize_convert`` event handler (S02 T02)
- True async ``async_recognize_media`` with parallel ``get_movie`` (S02 T02)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

# Make `mygirlfriends` importable by package name (mirrors test_recognize.py).
_PLUGINS_V2 = Path(__file__).resolve().parents[2]
if str(_PLUGINS_V2) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_V2))

from mygirlfriends import MyGirlfriends  # noqa: E402


# --- Fixtures / helpers --------------------------------------------------


class _StubMeta:
    """Minimal stand-in for ``MetaBase``. ``search_medias`` reads ``name`` first,
    then ``title`` — both are kept on the stub so individual tests can choose."""

    def __init__(self, title: str = "", name: str = ""):
        self.title = title
        self.name = name


class _StubClient:
    """Records ``search_movie`` / ``get_movie`` calls and replays canned data."""

    def __init__(
        self,
        search_results=None,
        movie_detail=None,
        server_url="http://meta.example:8900",
    ):
        self._search_results = search_results
        self._movie_detail = movie_detail
        self.server_url = server_url.rstrip("/")
        self.search_calls = []
        self.get_calls = []

    def search_movie(self, number):
        self.search_calls.append(number)
        return self._search_results

    def get_movie(self, provider, movie_id):
        self.get_calls.append((provider, movie_id))
        return self._movie_detail

    def image_url(self, image_type, provider, movie_id):
        return f"{self.server_url}/v1/images/{image_type}/{provider}/{movie_id}"


def _make_plugin(
    enabled: bool = True,
    recognition_mode: str = "hijacking",
    client=None,
    providers=None,
) -> MyGirlfriends:
    """Build a plugin bypassing ``init_plugin`` so tests stay hermetic."""
    plugin = MyGirlfriends()
    plugin._enabled = enabled
    plugin._recognition_mode = recognition_mode
    plugin._client = client
    plugin._server_url = client.server_url if client else ""
    plugin._translate = False
    plugin._providers = list(providers) if providers else []
    return plugin


def _make_event(mediaid):
    """Build a duck-typed ``Event`` with the ``.event_data.mediaid`` attribute
    that ``async_media_recognize_convert`` reads."""
    return SimpleNamespace(event_data=SimpleNamespace(mediaid=mediaid))


_SEARCH_TWO = [
    {"id": "ssis-001", "provider": "javbus", "title": "SSIS-001 javbus"},
    {"id": "ssis-001-alt", "provider": "jav321", "title": "SSIS-001 jav321"},
]
_DETAIL_RICH = {
    "title": "Canned JAV Title",
    "summary": "Canned summary.",
    "release_date": "2023-04-15",
    "score": 4.2,
    "runtime": 120,
    "genres": ["Drama"],
    "actors": ["Actress A"],
    "director": "Canned Director",
    "maker": "S1 NO.1 STYLE",
}


# === search_medias public surface ========================================


def test_search_medias_returns_mediainfo_for_valid_jav_code():
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client)

    result = plugin.search_medias(meta=_StubMeta(title="SSIS-001"))

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 1
    media = result[0]
    assert media.title == "SSIS-001"  # D-04: title overridden to canonical code
    assert media.original_title == "Canned JAV Title"
    assert "/v1/images/primary/javbus/ssis-001" in media.poster_path
    # imdb_id threading from T01: search_medias goes through _build_mediainfo(code=...)
    assert media.imdb_id == "jav:SSIS-001"


def test_search_medias_returns_none_for_non_jav_title():
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client)

    assert plugin.search_medias(meta=_StubMeta(title="H264")) is None
    # The client must never be consulted for a non-JAV title — that's the
    # contract that lets MoviePilot's TMDB fallback run unimpeded.
    assert client.search_calls == []


def test_search_medias_returns_none_when_disabled():
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(enabled=False, client=client)

    assert plugin.search_medias(meta=_StubMeta(title="SSIS-001")) is None
    assert client.search_calls == []


def test_search_medias_returns_none_when_client_none():
    plugin = _make_plugin(client=None)
    plugin._server_url = "http://meta.example:8900"

    assert plugin.search_medias(meta=_StubMeta(title="SSIS-001")) is None


def test_search_medias_returns_none_when_mode_disabled():
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(recognition_mode="disabled", client=client)

    assert plugin.search_medias(meta=_StubMeta(title="SSIS-001")) is None
    assert client.search_calls == []


def test_search_medias_swallows_exceptions():
    class _BoomClient(_StubClient):
        def search_movie(self, number):
            raise RuntimeError("simulated outage")

    plugin = _make_plugin(client=_BoomClient())

    # Must not raise — the chain may never see an exception from this path.
    assert plugin.search_medias(meta=_StubMeta(title="SSIS-001")) is None


def test_search_medias_returns_none_when_search_empty():
    client = _StubClient(search_results=[], movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client)

    assert plugin.search_medias(meta=_StubMeta(title="SSIS-001")) is None
    assert client.search_calls == ["SSIS-001"]
    assert client.get_calls == []  # never reached the detail step


def test_search_medias_uses_meta_name_attribute_when_title_empty():
    """``MetaInfo('SSIS-001')`` sometimes populates ``.name`` (no ``.title``)
    — the plugin's ``getattr(meta, 'name') or getattr(meta, 'title')`` chain
    must surface it."""
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client)

    result = plugin.search_medias(meta=_StubMeta(name="SSIS-001", title=""))

    assert result is not None
    assert len(result) == 1


def test_search_medias_returns_none_when_meta_is_none():
    plugin = _make_plugin(client=_StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH))

    assert plugin.search_medias(meta=None) is None


def test_search_medias_returns_none_when_title_and_name_both_blank():
    plugin = _make_plugin(client=_StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH))

    assert plugin.search_medias(meta=_StubMeta(title="", name="")) is None


def test_search_medias_parses_code_from_dirty_title():
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client)

    result = plugin.search_medias(
        meta=_StubMeta(title="[Studio] SSIS-001 [Uncensored Leaked].mp4")
    )

    assert result is not None
    assert client.search_calls == ["SSIS-001"]


def test_search_medias_returns_none_when_all_details_fail():
    """Negative path (Q7): every ``get_movie`` returns ``None`` → no MediaInfo."""
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=None)
    plugin = _make_plugin(client=client, providers=["javbus", "jav321"])

    assert plugin.search_medias(meta=_StubMeta(title="SSIS-001")) is None


# === get_module exposure =================================================


def test_get_module_exposes_search_medias_in_hijacking_mode():
    plugin = _make_plugin(enabled=True, recognition_mode="hijacking")

    modules = plugin.get_module()

    assert "search_medias" in modules
    assert "async_search_medias" in modules
    assert modules["search_medias"] == plugin.search_medias
    assert modules["async_search_medias"] == plugin.async_search_medias


def test_get_module_hides_search_medias_when_plugin_disabled():
    plugin = _make_plugin(enabled=False)

    assert plugin.get_module() == {}


def test_get_module_hides_search_medias_when_mode_disabled():
    plugin = _make_plugin(enabled=True, recognition_mode="disabled")

    assert plugin.get_module() == {}


# === imdb_id threading on _build_mediainfo ==============================


def test_imdb_id_set_on_built_mediainfo_when_code_passed():
    plugin = _make_plugin(client=_StubClient())

    media = plugin._build_mediainfo(
        {"title": "Anything"}, "javbus", "ssis-001", code="SSIS-001"
    )

    assert media.imdb_id == "jav:SSIS-001"


def test_imdb_id_not_set_when_code_omitted():
    """When the caller omits ``code`` (e.g. legacy callers from S01), no
    ``imdb_id`` attribute should be written — preserves backward compat."""
    plugin = _make_plugin(client=_StubClient())

    media = plugin._build_mediainfo({"title": "Anything"}, "javbus", "ssis-001")

    assert not hasattr(media, "imdb_id") or media.imdb_id is None


# === _merge_details ======================================================


def test_merge_details_picks_first_nonempty_title():
    plugin = _make_plugin(client=_StubClient())

    merged = plugin._merge_details([{"title": "First"}, {"title": "Second"}])

    assert merged["title"] == "First"


def test_merge_details_fills_gap_from_second_provider():
    plugin = _make_plugin(client=_StubClient())

    merged = plugin._merge_details(
        [
            {"title": "First", "summary": ""},
            {"title": "Second", "summary": "Real summary"},
        ]
    )

    assert merged["title"] == "First"
    assert merged["summary"] == "Real summary"


def test_merge_details_handles_empty_list():
    plugin = _make_plugin(client=_StubClient())

    assert plugin._merge_details([]) == {}


def test_merge_details_skips_empty_string():
    plugin = _make_plugin(client=_StubClient())

    merged = plugin._merge_details(
        [{"title": ""}, {"title": "Real Title"}]
    )

    assert merged["title"] == "Real Title"


def test_merge_details_skips_empty_list_field():
    plugin = _make_plugin(client=_StubClient())

    merged = plugin._merge_details(
        [{"actors": []}, {"actors": ["Alice"]}]
    )

    assert merged["actors"] == ["Alice"]


def test_merge_details_skips_zero_score():
    """``0`` is treated as empty per the merge contract — a real provider
    delivering ``score=0`` would be indistinguishable from a missing score."""
    plugin = _make_plugin(client=_StubClient())

    merged = plugin._merge_details([{"score": 0}, {"score": 4.5}])

    assert merged["score"] == 4.5


def test_merge_details_field_absent_when_all_empty():
    """Negative (Q7): if every provider lacks a field, it must not appear in
    the merged dict — ``_build_mediainfo`` then gracefully skips it."""
    plugin = _make_plugin(client=_StubClient())

    merged = plugin._merge_details([{"title": ""}, {"title": ""}])

    assert "title" not in merged


# === provider filtering in _search_and_merge ============================


def test_search_and_merge_filters_by_providers_config():
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client, providers=["jav321"])

    result = plugin._search_and_merge("SSIS-001")

    assert result is not None
    # With ``providers=['jav321']`` only the jav321 detail must be fetched.
    assert client.get_calls == [("jav321", "ssis-001-alt")]


def test_search_and_merge_uses_first_result_when_no_providers_config():
    """D008: default to top result only when ``_providers`` is empty."""
    three_results = [
        {"id": "a", "provider": "javbus"},
        {"id": "b", "provider": "jav321"},
        {"id": "c", "provider": "javdb"},
    ]
    client = _StubClient(search_results=three_results, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client, providers=[])

    plugin._search_and_merge("SSIS-001")

    assert client.get_calls == [("javbus", "a")]


def test_search_and_merge_falls_back_to_top_result_when_providers_not_in_results():
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client, providers=["nonexistent"])

    result = plugin._search_and_merge("SSIS-001")

    assert result is not None
    assert client.get_calls == [("javbus", "ssis-001")]


def test_search_and_merge_respects_provider_order():
    """``_providers=['jav321','javbus']`` should fetch jav321 first."""
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client, providers=["jav321", "javbus"])

    result = plugin._search_and_merge("SSIS-001")

    assert result is not None
    # Order matters — the first-tuple provider drives image URLs
    assert client.get_calls[0] == ("jav321", "ssis-001-alt")
    media = result[0]
    assert "/v1/images/primary/jav321/ssis-001-alt" in media.poster_path


# === async_search_medias ================================================


def test_async_search_medias_returns_same_shape_as_sync():
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client)

    sync_result = plugin.search_medias(meta=_StubMeta(title="SSIS-001"))
    async_result = asyncio.run(
        plugin.async_search_medias(meta=_StubMeta(title="SSIS-001"))
    )

    assert sync_result is not None
    assert async_result is not None
    assert len(async_result) == len(sync_result)
    assert async_result[0].title == sync_result[0].title
    assert async_result[0].imdb_id == sync_result[0].imdb_id


# === MediaRecognizeConvert handler =======================================


def test_media_recognize_convert_ignores_non_jav_prefix():
    plugin = _make_plugin(client=_StubClient())

    # Handler returns None on non-``imdb:jav:`` prefixes; must not raise.
    asyncio.run(plugin.async_media_recognize_convert(_make_event("tmdb:12345")))


def test_media_recognize_convert_ignores_when_disabled():
    plugin = _make_plugin(enabled=False, client=_StubClient())

    asyncio.run(
        plugin.async_media_recognize_convert(_make_event("imdb:jav:SSIS-001"))
    )


def test_media_recognize_convert_accepts_jav_prefix():
    plugin = _make_plugin(client=_StubClient())

    asyncio.run(
        plugin.async_media_recognize_convert(_make_event("imdb:jav:SSIS-001"))
    )


def test_media_recognize_convert_ignores_empty_mediaid():
    plugin = _make_plugin(client=_StubClient())

    asyncio.run(plugin.async_media_recognize_convert(_make_event("")))


def test_media_recognize_convert_ignores_jav_prefix_with_blank_code():
    plugin = _make_plugin(client=_StubClient())

    asyncio.run(plugin.async_media_recognize_convert(_make_event("imdb:jav:")))


# === async_recognize_media (T02 true-async path) =========================


def test_async_recognize_media_parallel_providers_fetches_all():
    """T02: ``async_recognize_media`` with ``_providers=['javbus','jav321']``
    should issue both ``get_movie`` calls (in parallel via ``asyncio.gather``)."""
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client, providers=["javbus", "jav321"])

    result = asyncio.run(
        plugin.async_recognize_media(meta=_StubMeta(title="SSIS-001"))
    )

    assert result is not None
    assert set(client.get_calls) == {
        ("javbus", "ssis-001"),
        ("jav321", "ssis-001-alt"),
    }


def test_async_recognize_media_swallows_per_provider_exception():
    """``asyncio.gather(return_exceptions=True)`` means one provider failing
    must not cancel the others — surviving providers still merge."""

    class _PartialBoomClient(_StubClient):
        def get_movie(self, provider, movie_id):
            self.get_calls.append((provider, movie_id))
            if provider == "javbus":
                raise RuntimeError("simulated outage")
            return _DETAIL_RICH

    client = _PartialBoomClient(search_results=_SEARCH_TWO, movie_detail=None)
    plugin = _make_plugin(client=client, providers=["javbus", "jav321"])

    result = asyncio.run(
        plugin.async_recognize_media(meta=_StubMeta(title="SSIS-001"))
    )

    assert result is not None
    assert len(client.get_calls) == 2  # both attempted


def test_async_recognize_media_returns_none_when_all_providers_raise():
    class _AllBoomClient(_StubClient):
        def get_movie(self, provider, movie_id):
            self.get_calls.append((provider, movie_id))
            raise RuntimeError("dead")

    client = _AllBoomClient(search_results=_SEARCH_TWO, movie_detail=None)
    plugin = _make_plugin(client=client, providers=["javbus", "jav321"])

    result = asyncio.run(
        plugin.async_recognize_media(meta=_StubMeta(title="SSIS-001"))
    )

    assert result is None


def test_async_recognize_media_returns_none_when_search_raises():
    """Q7 negative path: top-level ``search_movie`` exception is swallowed."""

    class _BoomSearchClient(_StubClient):
        def search_movie(self, number):
            raise RuntimeError("dead search")

    plugin = _make_plugin(client=_BoomSearchClient(search_results=None, movie_detail=None))

    assert (
        asyncio.run(plugin.async_recognize_media(meta=_StubMeta(title="SSIS-001")))
        is None
    )


def test_async_recognize_media_returns_none_when_client_missing():
    plugin = _make_plugin(client=None)
    plugin._server_url = "http://meta.example:8900"

    assert (
        asyncio.run(plugin.async_recognize_media(meta=_StubMeta(title="SSIS-001")))
        is None
    )


def test_async_recognize_media_sets_imdb_id_jav_prefix():
    """End-to-end async path must also surface ``imdb_id='jav:CODE'``."""
    client = _StubClient(search_results=_SEARCH_TWO, movie_detail=_DETAIL_RICH)
    plugin = _make_plugin(client=client)

    result = asyncio.run(
        plugin.async_recognize_media(meta=_StubMeta(title="SSIS-001"))
    )

    assert result is not None
    assert result.imdb_id == "jav:SSIS-001"


# === _build_mediainfo category + title overrides (D-01, D-04) ===========


def test_build_mediainfo_sets_category_jav():
    """D-01: category must be 'JAV' when a canonical code is provided."""
    plugin = _make_plugin(client=_StubClient())

    media = plugin._build_mediainfo(
        {"title": "Japanese Title"}, "javbus", "ssis-001", code="SSIS-001"
    )

    assert media.category == "JAV"


def test_build_mediainfo_sets_title_to_code():
    """D-04: title must equal the canonical code (overrides translation)."""
    plugin = _make_plugin(client=_StubClient())

    media = plugin._build_mediainfo(
        {"title": "Japanese Title"}, "javbus", "ssis-001", code="SSIS-001"
    )

    assert media.title == "SSIS-001"


def test_build_mediainfo_preserves_original_title():
    """original_title must remain the MetaTube detail title (not overwritten)."""
    plugin = _make_plugin(client=_StubClient())

    media = plugin._build_mediainfo(
        {"title": "Japanese Title"}, "javbus", "ssis-001", code="SSIS-001"
    )

    assert media.original_title == "Japanese Title"


def test_build_mediainfo_no_code_skips_category_and_title_override():
    """When code is None, new overrides must NOT fire (non-JAV path unchanged)."""
    plugin = _make_plugin(client=_StubClient())

    media = plugin._build_mediainfo(
        {"title": "Some Movie"}, "javbus", "abc", code=None
    )

    assert getattr(media, "category", None) != "JAV"
    assert getattr(media, "title", None) != "SSIS-001"
