"""Tests for :class:`MetaTubeClient`.

These tests monkeypatch :func:`requests.get` so they exercise the real
client logic (URL composition, bearer-header injection, envelope
unwrapping, error handling) without making any network calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the parent mygirlfriends/ directory importable so `import client` works
# without dragging in the plugin __init__.py (per MEM007).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
import requests  # noqa: E402

from client import MetaTubeClient  # noqa: E402


class _FakeResponse:
    def __init__(self, payload, status_code=200, raise_on_status=False):
        self._payload = payload
        self.status_code = status_code
        self._raise_on_status = raise_on_status

    def raise_for_status(self):
        if self._raise_on_status:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _RequestRecorder:
    """Stand-in for ``requests.get`` that records call args and returns a fake."""

    def __init__(self, response):
        self.response = response
        self.calls = []  # list of (url, params, headers, timeout)

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": params, "headers": headers or {}, "timeout": timeout}
        )
        return self.response


@pytest.fixture
def patched_get(monkeypatch):
    """Return a helper that installs a recorder with the given fake response."""

    def _install(payload, status_code=200, raise_on_status=False):
        rec = _RequestRecorder(_FakeResponse(payload, status_code, raise_on_status))
        monkeypatch.setattr(requests, "get", rec)
        return rec

    return _install


# --- URL composition / param shape ---------------------------------------


def test_search_movie_builds_correct_url_and_params(patched_get):
    rec = patched_get({"data": [{"provider": "javbus", "id": "SSIS-001"}]})
    client = MetaTubeClient("http://meta.example:8900")

    result = client.search_movie("SSIS-001")

    assert result == [{"provider": "javbus", "id": "SSIS-001"}]
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["url"] == "http://meta.example:8900/v1/movies/search"
    assert call["params"] == {"q": "SSIS-001", "fallback": "true"}
    assert call["timeout"] == 30


def test_get_movie_builds_correct_url_with_lazy(patched_get):
    rec = patched_get({"data": {"id": "SSIS-001", "title": "X"}})
    client = MetaTubeClient("http://meta.example:8900")

    result = client.get_movie("javbus", "SSIS-001")

    assert result == {"id": "SSIS-001", "title": "X"}
    assert rec.calls[0]["url"] == "http://meta.example:8900/v1/movies/javbus/SSIS-001"
    assert rec.calls[0]["params"] == {"lazy": "true"}


def test_search_actor_builds_correct_url(patched_get):
    rec = patched_get({"data": [{"name": "Aaa"}]})
    client = MetaTubeClient("http://meta.example:8900")

    client.search_actor("Aaa")

    assert rec.calls[0]["url"] == "http://meta.example:8900/v1/actors/search"
    assert rec.calls[0]["params"] == {"q": "Aaa", "fallback": "true"}


def test_get_actor_builds_correct_url_with_lazy(patched_get):
    rec = patched_get({"data": {"name": "Aaa"}})
    client = MetaTubeClient("http://meta.example:8900")

    client.get_actor("javbus", "actor-123")

    assert rec.calls[0]["url"] == "http://meta.example:8900/v1/actors/javbus/actor-123"
    assert rec.calls[0]["params"] == {"lazy": "true"}


def test_trailing_slash_in_server_url_is_normalised(patched_get):
    rec = patched_get({"data": []})
    client = MetaTubeClient("http://meta.example:8900/")

    client.search_movie("X")

    assert rec.calls[0]["url"] == "http://meta.example:8900/v1/movies/search"


# --- Bearer header injection ---------------------------------------------


def test_bearer_header_added_when_token_set(patched_get):
    rec = patched_get({"data": []})
    client = MetaTubeClient("http://meta.example:8900", token="secret-token")

    client.search_movie("X")

    assert rec.calls[0]["headers"].get("Authorization") == "Bearer secret-token"


def test_bearer_header_omitted_when_token_blank(patched_get):
    rec = patched_get({"data": []})
    client = MetaTubeClient("http://meta.example:8900", token="")

    client.search_movie("X")

    assert "Authorization" not in rec.calls[0]["headers"]


def test_bearer_header_omitted_when_token_is_none(patched_get):
    rec = patched_get({"data": []})
    client = MetaTubeClient("http://meta.example:8900")

    client.search_movie("X")

    assert "Authorization" not in rec.calls[0]["headers"]


# --- Envelope unwrapping -------------------------------------------------


def test_returns_inner_data_field(patched_get):
    patched_get({"data": {"id": "movie-1"}})
    client = MetaTubeClient("http://meta.example:8900")

    assert client.get_movie("javbus", "movie-1") == {"id": "movie-1"}


def test_returns_none_when_data_missing(patched_get):
    patched_get({})
    client = MetaTubeClient("http://meta.example:8900")

    assert client.search_movie("X") is None


# --- Error handling ------------------------------------------------------


def test_returns_none_on_error_envelope(patched_get):
    patched_get({"error": "upstream-bad-gateway"})
    client = MetaTubeClient("http://meta.example:8900")

    assert client.search_movie("X") is None


def test_returns_none_on_network_exception(monkeypatch):
    def boom(*_a, **_kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)
    client = MetaTubeClient("http://meta.example:8900")

    assert client.search_movie("X") is None


def test_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *_a, **_kw: _FakeResponse({}, 500, raise_on_status=True)
    )
    client = MetaTubeClient("http://meta.example:8900")

    assert client.search_movie("X") is None


def test_returns_none_on_non_dict_response(patched_get):
    patched_get([1, 2, 3])
    client = MetaTubeClient("http://meta.example:8900")

    assert client.search_movie("X") is None


def test_returns_none_when_server_url_missing():
    client = MetaTubeClient("")

    assert client.search_movie("X") is None
    assert client.get_movie("javbus", "x") is None
    assert client.search_actor("name") is None
    assert client.get_actor("javbus", "x") is None
    assert client.translate("hi", "zh-CN", "google") is None


# --- translate -----------------------------------------------------------


def test_translate_returns_string_payload(patched_get):
    rec = patched_get({"data": "你好"})
    client = MetaTubeClient("http://meta.example:8900")

    assert client.translate("hello", "zh-CN", "google") == "你好"
    assert rec.calls[0]["url"] == "http://meta.example:8900/v1/translate"
    assert rec.calls[0]["params"] == {"q": "hello", "to": "zh-CN", "engine": "google"}


def test_translate_returns_dict_with_translated_field(patched_get):
    patched_get({"data": {"translated": "你好"}})
    client = MetaTubeClient("http://meta.example:8900")

    assert client.translate("hello", "zh-CN", "google") == "你好"


def test_translate_returns_none_on_error_envelope(patched_get):
    patched_get({"error": "quota-exceeded"})
    client = MetaTubeClient("http://meta.example:8900")

    assert client.translate("hello", "zh-CN", "google") is None


# --- image_url builder (no network) --------------------------------------


def test_image_url_composes_path():
    client = MetaTubeClient("http://meta.example:8900")

    assert (
        client.image_url("primary", "javbus", "SSIS-001")
        == "http://meta.example:8900/v1/images/primary/javbus/SSIS-001"
    )


def test_image_url_strips_trailing_slash():
    client = MetaTubeClient("http://meta.example:8900/")

    assert (
        client.image_url("backdrop", "javbus", "SSIS-001")
        == "http://meta.example:8900/v1/images/backdrop/javbus/SSIS-001"
    )


def test_image_url_does_not_call_network(monkeypatch):
    def boom(*_a, **_kw):
        raise AssertionError("image_url must not perform a network call")

    monkeypatch.setattr(requests, "get", boom)
    client = MetaTubeClient("http://meta.example:8900")

    client.image_url("primary", "javbus", "SSIS-001")


# --- timeout override ----------------------------------------------------


def test_custom_timeout_is_propagated(patched_get):
    rec = patched_get({"data": []})
    client = MetaTubeClient("http://meta.example:8900", timeout=5)

    client.search_movie("X")

    assert rec.calls[0]["timeout"] == 5
