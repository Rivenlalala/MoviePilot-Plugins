"""MetaTube HTTP client.

A small typed wrapper around the MetaTube server's ``/v1`` REST surface.
Pulled out of ``MyGirlfriends`` so the recognition-chain hijack layer (S02)
can compose multiple provider calls without dragging the plugin class
through tests.

All ``*_movie`` / ``*_actor`` / ``translate`` methods return ``None`` on
any failure (network error, non-2xx response, ``data.error`` set, or
empty payload) and log at WARNING/ERROR. The only method that does NOT
hit the network is :meth:`image_url`, which is a pure URL builder.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from app.log import logger

__all__ = ["MetaTubeClient"]


class MetaTubeClient:
    """Thin client for the MetaTube ``/v1`` API.

    Parameters
    ----------
    server_url:
        Base URL of the MetaTube server, e.g. ``http://localhost:8900``.
        Trailing slashes are stripped. The empty string is allowed at
        construction time but every networked call will short-circuit
        to ``None`` with a logged error until a real URL is set.
    token:
        Optional bearer token. When non-empty, an
        ``Authorization: Bearer <token>`` header is sent.
    timeout:
        Per-request timeout in seconds. Defaults to 30 to match the
        existing in-plugin behaviour.
    """

    def __init__(
        self,
        server_url: str,
        token: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self.server_url = (server_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    # --- internal -----------------------------------------------------

    def _request(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Any]:
        if not self.server_url:
            logger.error("我的女友们: 服务器地址未配置")
            return None
        url = f"{self.server_url}/v1{path}"
        headers: Dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error(f"我的女友们: 请求失败 {url} - {exc}")
            return None
        except ValueError as exc:  # JSON decode error
            logger.error(f"我的女友们: 解析响应失败 {url} - {exc}")
            return None

        if not isinstance(data, dict):
            logger.warning(f"我的女友们: 响应格式异常 {url} - {type(data).__name__}")
            return None
        if data.get("error"):
            logger.warning(f"JAV MetaTube API 错误: {data['error']}")
            return None
        return data.get("data")

    # --- public surface ----------------------------------------------

    def search_movie(self, number: str) -> Optional[List[dict]]:
        """``GET /v1/movies/search?q={number}&fallback=true``."""
        return self._request("/movies/search", params={"q": number, "fallback": "true"})

    def get_movie(self, provider: str, movie_id: str) -> Optional[dict]:
        """``GET /v1/movies/{provider}/{movie_id}?lazy=true``."""
        return self._request(f"/movies/{provider}/{movie_id}", params={"lazy": "true"})

    def search_actor(self, name: str) -> Optional[List[dict]]:
        """``GET /v1/actors/search?q={name}&fallback=true``."""
        return self._request("/actors/search", params={"q": name, "fallback": "true"})

    def get_actor(self, provider: str, actor_id: str) -> Optional[dict]:
        """``GET /v1/actors/{provider}/{actor_id}?lazy=true``."""
        return self._request(f"/actors/{provider}/{actor_id}", params={"lazy": "true"})

    def translate(self, text: str, to_lang: str, engine: str) -> Optional[str]:
        """``GET /v1/translate?q={text}&to={to_lang}&engine={engine}``.

        Returns the translated string on success, ``None`` otherwise.
        """
        data = self._request(
            "/translate",
            params={"q": text, "to": to_lang, "engine": engine},
        )
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            translated = data.get("translated") or data.get("text")
            if isinstance(translated, str):
                return translated
        return None

    def image_url(self, image_type: str, provider: str, movie_id: str) -> str:
        """Pure URL builder — no network call. Returns the proxy URL the
        MetaTube server serves images on for the given provider/movie.
        """
        return f"{self.server_url}/v1/images/{image_type}/{provider}/{movie_id}"
