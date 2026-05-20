"""Tests for the recognition-chain hijack methods on ``MyGirlfriends``.

These cover the S01 deliverable: ``recognize_media`` /
``async_recognize_media`` / ``get_module`` and the ``init_plugin`` hook
that registers the MetaTube server host with
``settings.SECURITY_IMAGE_DOMAINS``.

The repo-root ``conftest.py`` stubs ``app.*`` / ``apscheduler`` /
``MediaInfo`` / ``MediaType`` / ``MetaBase`` so the plugin package can be
imported without a real MoviePilot installation. The plugin dir's parent
(``MoviePilot-Plugins/plugins.v2``) is added to ``sys.path`` here so that
``from mygirlfriends import MyGirlfriends`` resolves the package.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add MoviePilot-Plugins/plugins.v2 to sys.path so the plugin package can be
# imported by name (the conftest only adds the plugin dir itself, which is
# enough for leaf-module tests like test_javcode.py / test_client.py but not
# for the package import this test needs).
_PLUGINS_V2 = Path(__file__).resolve().parents[2]
if str(_PLUGINS_V2) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_V2))

import pytest  # noqa: E402

from app.core.config import settings  # noqa: E402  (stubbed by conftest)
from app.schemas.types import MediaType  # noqa: E402

from mygirlfriends import MyGirlfriends  # noqa: E402


# --- Fixtures / helpers --------------------------------------------------


class _StubMeta:
    """Minimal stand-in for ``MetaBase`` — we only ever read ``.title``."""

    def __init__(self, title: str = "", name: str = ""):
        self.title = title
        self.name = name


class _StubClient:
    """Records calls and returns canned ``search_movie`` / ``get_movie`` payloads.

    The plugin only touches ``search_movie``, ``get_movie`` and ``image_url``
    on the client; nothing else is needed for the recognition path.
    """

    def __init__(self, search_results=None, movie_detail=None, server_url="http://meta.example:8900"):
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


def _make_plugin(enabled=True, recognition_mode="hijacking", client=None) -> MyGirlfriends:
    """Build a plugin without touching ``init_plugin``.

    We deliberately bypass ``init_plugin`` for the recognition-method tests
    because the construction would also instantiate a real ``MetaTubeClient``
    against a non-existent server; the dedicated ``init_plugin`` test
    below exercises that path separately.
    """
    plugin = MyGirlfriends()
    plugin._enabled = enabled
    plugin._recognition_mode = recognition_mode
    plugin._client = client
    plugin._server_url = client.server_url if client else ""
    plugin._translate = False
    return plugin


@pytest.fixture
def security_domains_snapshot():
    """Save and restore ``settings.SECURITY_IMAGE_DOMAINS`` around the test."""

    original = list(getattr(settings, "SECURITY_IMAGE_DOMAINS", []))
    yield
    settings.SECURITY_IMAGE_DOMAINS = original


_CANNED_SEARCH = [
    {"id": "ssis-001", "provider": "javbus", "title": "SSIS-001 Title"},
    {"id": "ssis-001-alt", "provider": "jav321", "title": "alt"},
]
_CANNED_DETAIL = {
    "id": "ssis-001",
    "number": "SSIS-001",
    "title": "Canned JAV Title",
    "summary": "Canned summary text.",
    "director": "Canned Director",
    "maker": "S1 NO.1 STYLE",
    "actors": ["Actress A", "Actress B"],
    "genres": ["Drama", "Solo"],
    "score": 4.2,
    "runtime": 120,
    "release_date": "2023-04-15",
}


# --- Positive case -------------------------------------------------------


def test_recognize_media_returns_populated_mediainfo_for_jav_title():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    result = plugin.recognize_media(meta=_StubMeta(title="SSIS-001"))

    assert result is not None
    assert result.source == "metatube"
    assert result.adult is True
    assert result.type == MediaType.MOVIE
    assert result.title == "SSIS-001"  # D-04: title overridden to canonical code
    assert result.original_title == "Canned JAV Title"
    assert result.overview == "Canned summary text."
    assert result.release_date == "2023-04-15"
    assert result.year == "2023"
    assert result.vote_average == 4.2
    assert result.runtime == 120

    # image_url uses the first search result's provider + id
    assert "/v1/images/primary/javbus/ssis-001" in result.poster_path
    assert "/v1/images/backdrop/javbus/ssis-001" in result.backdrop_path

    # actors/genres are mapped to MediaInfo dict shape
    assert {a["name"] for a in result.actors} == {"Actress A", "Actress B"}
    assert all(a["character"] == "" for a in result.actors)
    assert all(isinstance(a["id"], int) for a in result.actors)
    assert {g["name"] for g in result.genres} == {"Drama", "Solo"}

    # director / maker mapped to single-entry lists
    assert result.directors == [{"name": "Canned Director"}]
    assert result.production_companies == [{"name": "S1 NO.1 STYLE"}]

    # client was actually consulted with the parsed code
    assert client.search_calls == ["SSIS-001"]
    assert client.get_calls == [("javbus", "ssis-001")]


def test_recognize_media_parses_code_from_dirty_title():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    result = plugin.recognize_media(
        meta=_StubMeta(title="[Studio] SSIS-001 [Uncensored Leaked].mp4")
    )

    assert result is not None
    assert client.search_calls == ["SSIS-001"]


# --- Negative cases ------------------------------------------------------


def test_recognize_media_returns_none_for_non_jav_title():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    result = plugin.recognize_media(meta=_StubMeta(title="Inception.2010.1080p.BluRay.x264"))

    assert result is None
    # client must not be called for non-JAV titles
    assert client.search_calls == []
    assert client.get_calls == []


def test_recognize_media_returns_none_when_tmdbid_set():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    result = plugin.recognize_media(meta=_StubMeta(title="SSIS-001"), tmdbid=12345)

    assert result is None
    assert client.search_calls == []  # short-circuit before parsing


def test_recognize_media_returns_none_when_doubanid_set():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    assert plugin.recognize_media(meta=_StubMeta(title="SSIS-001"), doubanid="123") is None
    assert client.search_calls == []


def test_recognize_media_returns_none_when_bangumiid_set():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    assert plugin.recognize_media(meta=_StubMeta(title="SSIS-001"), bangumiid=42) is None
    assert client.search_calls == []


def test_recognize_media_returns_none_when_plugin_disabled():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(enabled=False, client=client)

    assert plugin.recognize_media(meta=_StubMeta(title="SSIS-001")) is None
    assert client.search_calls == []


def test_recognize_media_returns_none_when_mode_is_disabled():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(enabled=True, recognition_mode="disabled", client=client)

    assert plugin.recognize_media(meta=_StubMeta(title="SSIS-001")) is None
    assert client.search_calls == []


def test_recognize_media_returns_none_when_meta_is_missing():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    assert plugin.recognize_media(meta=None) is None
    assert client.search_calls == []


def test_recognize_media_returns_none_when_search_empty():
    client = _StubClient(search_results=[], movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    assert plugin.recognize_media(meta=_StubMeta(title="SSIS-001")) is None
    assert client.search_calls == ["SSIS-001"]
    assert client.get_calls == []  # never reached detail lookup


def test_recognize_media_returns_none_when_detail_lookup_fails():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=None)
    plugin = _make_plugin(client=client)

    assert plugin.recognize_media(meta=_StubMeta(title="SSIS-001")) is None
    assert client.search_calls == ["SSIS-001"]
    assert client.get_calls == [("javbus", "ssis-001")]


def test_recognize_media_swallows_client_exceptions():
    class _BoomClient(_StubClient):
        def search_movie(self, number):
            raise RuntimeError("simulated outage")

    plugin = _make_plugin(client=_BoomClient())

    # Must not raise — the chain may never see an exception from this path.
    assert plugin.recognize_media(meta=_StubMeta(title="SSIS-001")) is None


def test_recognize_media_handles_missing_client():
    plugin = _make_plugin(client=None)

    assert plugin.recognize_media(meta=_StubMeta(title="SSIS-001")) is None


# --- async wrapper -------------------------------------------------------


def test_async_recognize_media_mirrors_sync():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    result = asyncio.run(plugin.async_recognize_media(meta=_StubMeta(title="SSIS-001")))

    assert result is not None
    assert result.source == "metatube"
    assert client.search_calls == ["SSIS-001"]


def test_async_recognize_media_returns_none_for_non_jav():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    result = asyncio.run(plugin.async_recognize_media(meta=_StubMeta(title="Inception.2010")))

    assert result is None
    assert client.search_calls == []


# --- get_module ----------------------------------------------------------


def test_get_module_empty_when_disabled():
    plugin = _make_plugin(enabled=False)

    assert plugin.get_module() == {}


def test_get_module_empty_when_mode_disabled():
    plugin = _make_plugin(enabled=True, recognition_mode="disabled")

    assert plugin.get_module() == {}


def test_get_module_exposes_methods_when_hijacking():
    plugin = _make_plugin(enabled=True, recognition_mode="hijacking")

    modules = plugin.get_module()

    assert set(modules.keys()) == {
        "recognize_media", "async_recognize_media",
        "search_medias", "async_search_medias",
    }
    assert modules["recognize_media"] == plugin.recognize_media
    assert modules["async_recognize_media"] == plugin.async_recognize_media
    assert modules["search_medias"] == plugin.search_medias
    assert modules["async_search_medias"] == plugin.async_search_medias


# --- init_plugin: SECURITY_IMAGE_DOMAINS registration --------------------


def test_init_plugin_registers_metatube_host_in_security_image_domains(
    security_domains_snapshot,
):
    plugin = MyGirlfriends()
    settings.SECURITY_IMAGE_DOMAINS = ["image.tmdb.org"]

    plugin.init_plugin({
        "enabled": True,
        "server_url": "http://192.168.123.4:8900",
        "token": "",
        "recognize_media_enabled": True,
        "recognition_mode": "hijacking",
    })

    assert "192.168.123.4" in settings.SECURITY_IMAGE_DOMAINS
    # original entries are preserved
    assert "image.tmdb.org" in settings.SECURITY_IMAGE_DOMAINS


def test_init_plugin_does_not_duplicate_existing_host(security_domains_snapshot):
    plugin = MyGirlfriends()
    settings.SECURITY_IMAGE_DOMAINS = ["meta.example"]

    plugin.init_plugin({
        "enabled": True,
        "server_url": "http://meta.example:8900",
        "token": "",
    })

    assert settings.SECURITY_IMAGE_DOMAINS.count("meta.example") == 1


def test_init_plugin_skips_when_server_url_empty(security_domains_snapshot):
    plugin = MyGirlfriends()
    before = list(settings.SECURITY_IMAGE_DOMAINS)

    plugin.init_plugin({"enabled": False, "server_url": ""})

    assert list(settings.SECURITY_IMAGE_DOMAINS) == before


# --- Translation wiring --------------------------------------------------


def test_recognize_media_translates_title_when_enabled():
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)
    plugin._translate = True
    plugin._translate_to = "zh-CN"
    plugin._translate_engine = "google"

    # Patch the translation method directly — the actual translate() lives
    # on the client and is exercised in test_client.py; here we only care
    # that recognize_media routes the title through _translate_text.
    plugin._translate_text = lambda text: f"[ZH]{text}" if text else text

    result = plugin.recognize_media(meta=_StubMeta(title="SSIS-001"))

    assert result is not None
    assert result.original_title == "Canned JAV Title"
    assert result.title == "SSIS-001"  # D-04: title = code overrides translation


# --- S02 cross-checks (recognize_media now delegates to _search_and_merge) ---


def test_recognize_media_uses_search_and_merge_internally():
    """S02 T01 refactor: ``recognize_media`` now goes through ``_search_and_merge``,
    which means the built ``MediaInfo`` should carry ``imdb_id='jav:CODE'``."""
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)

    result = plugin.recognize_media(meta=_StubMeta(title="SSIS-001"))

    assert result is not None
    assert result.poster_path  # proves _build_mediainfo ran
    assert result.imdb_id == "jav:SSIS-001"  # proves the new code-threading path


def test_recognize_media_respects_providers_config():
    """When ``_providers=['jav321']`` is set, ``recognize_media`` must fetch
    the jav321 detail (not the top javbus result)."""
    client = _StubClient(search_results=_CANNED_SEARCH, movie_detail=_CANNED_DETAIL)
    plugin = _make_plugin(client=client)
    plugin._providers = ["jav321"]

    result = plugin.recognize_media(meta=_StubMeta(title="SSIS-001"))

    assert result is not None
    assert client.get_calls == [("jav321", "ssis-001-alt")]
