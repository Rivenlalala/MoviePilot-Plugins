import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from xml.dom import minidom

import pytz
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.core.meta import MetaBase
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import ChainEventType, EventType, MediaType
from app.utils.dom import DomUtils

from .client import MetaTubeClient
from .javcode import extract_jav_code


class MyGirlfriends(_PluginBase):
    plugin_name = "我的女友们"
    plugin_desc = "通过 MetaTube 服务器刮削 JAV 元数据，生成 NFO 文件和下载封面图片。"
    plugin_icon = "metatube.png"
    plugin_version = "1.1"
    plugin_author = "ruiwen"
    author_url = "https://github.com/Rivenlalala"
    plugin_config_prefix = "mygirlfriends_"
    plugin_order = 30
    auth_level = 2

    _enabled = False
    _server_url = ""
    _token = ""
    _auto_scrape = False
    _recognize_media_enabled = False
    _recognition_mode = "disabled"
    _translate = False
    _translate_engine = "google"
    _translate_to = "zh-CN"
    _overwrite = False
    _cron = ""
    _onlyonce = False
    _notify = False
    _scan_paths = ""
    _exclude_paths = ""
    _providers: List[str] = []

    _scheduler: Optional[BackgroundScheduler] = None
    _event = threading.Event()
    _client: Optional[MetaTubeClient] = None

    def init_plugin(self, config: dict = None):
        self.stop_service()

        if config:
            self._enabled = config.get("enabled", False)
            self._server_url = config.get("server_url", "").rstrip("/")
            self._token = config.get("token", "")
            self._auto_scrape = config.get("auto_scrape", False)
            self._recognize_media_enabled = config.get("recognize_media_enabled", False)
            self._recognition_mode = config.get("recognition_mode", "disabled") or "disabled"
            self._translate = config.get("translate", False)
            self._translate_engine = config.get("translate_engine", "google")
            self._translate_to = config.get("translate_to", "zh-CN")
            self._overwrite = config.get("overwrite", False)
            self._cron = config.get("cron", "")
            self._onlyonce = config.get("onlyonce", False)
            self._notify = config.get("notify", False)
            self._scan_paths = config.get("scan_paths", "")
            self._exclude_paths = config.get("exclude_paths", "")
            raw_providers = config.get("providers", "")
            self._providers = [p.strip() for p in raw_providers.splitlines() if p.strip()]

        self._client = MetaTubeClient(self._server_url, self._token) if self._server_url else None

        # 将 MetaTube 服务器主机加入安全图片域名白名单，使得封面/背景图代理 URL
        # 能被 MoviePilot 的图片缓存层信任。重启或重新启用时会重复尝试，所以
        # 这里做幂等的去重。
        if self._server_url:
            try:
                host = urlparse(self._server_url).hostname
                allowed = getattr(settings, "SECURITY_IMAGE_DOMAINS", None)
                if host and isinstance(allowed, list) and host not in allowed:
                    allowed.append(host)
            except Exception as exc:
                logger.warning(f"我的女友们: 注册图片域名失败 {self._server_url} - {exc}")

        if self._enabled and self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info("我的女友们: 立即执行一次全量扫描")
            self._scheduler.add_job(
                func=self._full_scan,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="我的女友们 全量扫描",
            )
            self._onlyonce = False
            self.update_config({
                "enabled": self._enabled,
                "server_url": self._server_url,
                "token": self._token,
                "auto_scrape": self._auto_scrape,
                "recognize_media_enabled": self._recognize_media_enabled,
                "recognition_mode": self._recognition_mode,
                "translate": self._translate,
                "translate_engine": self._translate_engine,
                "translate_to": self._translate_to,
                "overwrite": self._overwrite,
                "cron": self._cron,
                "onlyonce": False,
                "notify": self._notify,
                "scan_paths": self._scan_paths,
                "exclude_paths": self._exclude_paths,
                "providers": "\n".join(self._providers),
            })
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    # --- Recognition chain integration ---------------------------------

    def get_module(self) -> Dict[str, Any]:
        """声明插件向 MoviePilot 识别链注入的模块方法。

        仅当插件启用且工作模式为 hijacking 时返回 ``recognize_media`` /
        ``async_recognize_media``；否则返回空字典，识别链走默认 TMDB/豆瓣
        路径。
        """
        modules: Dict[str, Any] = {}
        if self._enabled and self._recognition_mode == "hijacking":
            modules["recognize_media"] = self.recognize_media
            modules["async_recognize_media"] = self.async_recognize_media
            modules["search_medias"] = self.search_medias
            modules["async_search_medias"] = self.async_search_medias
        return modules

    def recognize_media(
        self,
        meta: MetaBase = None,
        mtype: MediaType = None,
        **kwargs,
    ) -> Optional[MediaInfo]:
        """劫持 MoviePilot 识别链:从 meta.title 中提取番号并查询 MetaTube。

        - 未启用 / 模式非 hijacking → ``None`` 让默认链继续。
        - 已显式提供 tmdbid/doubanid/bangumiid → ``None``（尊重外部 ID）。
        - meta.title 未匹配到番号 → DEBUG 一行后 ``None``，避免淹没 TMDB 路径。
        - MetaTube 查询无果或抛错 → WARNING/ERROR + ``None``，链不会上抛异常。
        """
        code: Optional[str] = None
        try:
            if not self._enabled or self._recognition_mode != "hijacking":
                return None
            if kwargs.get("tmdbid") or kwargs.get("doubanid") or kwargs.get("bangumiid"):
                return None
            if not meta:
                return None

            title = getattr(meta, "title", None)
            code = extract_jav_code(title) if title else None
            if not code:
                logger.debug(f"我的女友们: 标题未匹配番号，跳过 - {title!r}")
                return None

            logger.info(f"我的女友们: 识别命中番号 {code}，调用 MetaTube")

            infos = self._search_and_merge(code)
            return infos[0] if infos else None
        except Exception as exc:  # noqa: BLE001 - chain must never raise
            logger.error(
                f"我的女友们: 识别链异常 (番号 {code or '<unknown>'}) - {exc}"
            )
            return None

    async def async_recognize_media(
        self,
        meta: MetaBase = None,
        mtype: MediaType = None,
        **kwargs,
    ) -> Optional[MediaInfo]:
        """真正的异步实现：parallel get_movie calls via asyncio.gather + to_thread."""
        import asyncio

        if not self._enabled or self._recognition_mode != "hijacking":
            return None
        if kwargs.get("tmdbid") or kwargs.get("doubanid") or kwargs.get("bangumiid"):
            return None
        if not meta:
            return None
        title = getattr(meta, "title", None)
        code = extract_jav_code(title) if title else None
        if not code:
            logger.debug(f"我的女友们: 标题未匹配番号，跳过 - {title!r}")
            return None
        logger.info(f"我的女友们: 识别命中番号 {code}，调用 MetaTube (async)")
        client = self._client
        if client is None:
            logger.warning(f"我的女友们: 客户端未初始化，跳过番号 {code}")
            return None
        try:
            results = await asyncio.to_thread(client.search_movie, code)
            if not results:
                logger.warning(f"我的女友们: 搜索番号 {code} 无结果")
                return None
            want = self._providers
            ordered = (
                [r for p in want for r in results if r.get("provider") == p]
                if want
                else results[:1]
            )
            if not ordered:
                ordered = results[:1]
            coros = [
                asyncio.to_thread(client.get_movie, r.get("provider"), r.get("id"))
                for r in ordered
                if r.get("provider") and r.get("id")
            ]
            raw = await asyncio.gather(*coros, return_exceptions=True)
            details = [
                (ordered[i].get("provider"), ordered[i].get("id"), d)
                for i, d in enumerate(raw)
                if not isinstance(d, Exception) and d
            ]
            if not details:
                logger.warning(f"我的女友们: 所有 provider 详情获取失败 (番号 {code})")
                return None
            merged = self._merge_details([d for _, _, d in details])
            best_prov, best_mid, _ = details[0]
            return self._build_mediainfo(merged, best_prov, best_mid, code=code)
        except Exception as exc:
            logger.error(f"我的女友们: 异步识别链异常 (番号 {code}) - {exc}")
            return None

    @eventmanager.register(ChainEventType.MediaRecognizeConvert)
    async def async_media_recognize_convert(self, event: Event) -> None:
        """Handle jav: prefix media IDs fired after search_medias sets imdb_id='jav:CODE'.

        JAV content has no TMDB/Douban ID, so the ID-based torrent search path
        (search_by_id_stream) cannot be populated here. Title-based search
        (search_by_title_stream with the JAV code) is the correct path — S03/S04 scope.
        """
        if not self._enabled:
            return
        event_data = event.event_data
        mediaid = (event_data.mediaid or "") if event_data else ""
        # search endpoint fires mediaid as 'imdb:jav:CODE'
        if not mediaid.startswith("imdb:jav:"):
            return
        code = mediaid[len("imdb:jav:"):]
        if not code:
            return
        logger.debug(
            f"我的女友们: MediaRecognizeConvert 收到番号 {code}，"
            "ID 转换不适用于 JAV 内容（无 TMDB/Douban ID）"
        )

    def search_medias(self, meta: MetaBase = None, **kwargs) -> Optional[List[MediaInfo]]:
        """在 MoviePilot 搜索栏命中番号时返回 MetaTube 搜索结果列表。"""
        if not self._enabled or self._recognition_mode != "hijacking":
            return None
        title = getattr(meta, "name", None) or getattr(meta, "title", None)
        code = extract_jav_code(title) if title else None
        if not code:
            logger.debug(f"我的女友们: search_medias 标题未匹配番号，跳过 - {title!r}")
            return None
        logger.info(f"我的女友们: search_medias 命中番号 {code}")
        try:
            return self._search_and_merge(code)
        except Exception as exc:
            logger.error(f"我的女友们: search_medias 异常 (番号 {code}) - {exc}")
            return None

    async def async_search_medias(self, meta: MetaBase = None, **kwargs) -> Optional[List[MediaInfo]]:
        """异步入口，调用同步 search_medias。"""
        return self.search_medias(meta=meta, **kwargs)

    def _build_mediainfo(
        self, detail: dict, provider: str, movie_id: str, code: str = None
    ) -> MediaInfo:
        """将 MetaTube 详情字典映射成 MoviePilot ``MediaInfo``。

        字段对应表见 S01-RESEARCH.md。``original_title`` 保留 MetaTube 原始
        标题；``title`` 在开启翻译后是译文，否则与 original_title 一致。
        """
        media = MediaInfo()
        media.source = "metatube"
        media.type = MediaType.MOVIE
        media.adult = True

        original_title = detail.get("title") or ""
        media.original_title = original_title
        media.title = self._translate_text(original_title) if original_title else ""

        summary = detail.get("summary")
        if summary:
            media.overview = summary

        release_date = detail.get("release_date") or ""
        if release_date:
            media.release_date = release_date
            if len(release_date) >= 4:
                media.year = release_date[:4]

        score = detail.get("score")
        if score is not None:
            media.vote_average = score

        runtime = detail.get("runtime")
        if runtime is not None:
            media.runtime = runtime

        client = self._client
        if client is not None:
            media.poster_path = client.image_url("primary", provider, movie_id)
            media.backdrop_path = client.image_url("backdrop", provider, movie_id)

        media.genres = [
            {"id": hash(g) & 0x7FFFFFFF, "name": g}
            for g in (detail.get("genres") or [])
            if g
        ]
        media.actors = [
            {"id": hash(name) & 0x7FFFFFFF, "name": name, "character": ""}
            for name in (detail.get("actors") or [])
            if name
        ]

        director = detail.get("director")
        media.directors = [{"name": director}] if director else []

        maker = detail.get("maker")
        media.production_companies = [{"name": maker}] if maker else []

        if code:
            media.imdb_id = f"jav:{code}"

        return media

    def _merge_details(self, details: List[dict]) -> dict:
        """合并多个 provider 的详情，每个字段取第一个非空值。"""
        merged: dict = {}
        fields = (
            "title", "summary", "release_date", "score", "runtime",
            "genres", "actors", "director", "maker", "series", "label",
        )
        for field in fields:
            for detail in details:
                val = detail.get(field)
                if val is None or val == "" or val == [] or val == 0:
                    continue
                merged[field] = val
                break
        return merged

    def _search_and_merge(self, code: str) -> Optional[List[MediaInfo]]:
        """搜索番号并合并多 provider 详情，返回 MediaInfo 列表。"""
        client = self._client
        if client is None:
            logger.warning(f"我的女友们: 客户端未初始化，跳过番号 {code}")
            return None

        results = client.search_movie(code)
        if not results:
            logger.warning(f"我的女友们: 搜索番号 {code} 无结果")
            return None

        if self._providers:
            ordered = [
                r for p in self._providers for r in results if r.get("provider") == p
            ]
        else:
            ordered = results[:1]

        if not ordered:
            ordered = results[:1]

        details = []
        for r in ordered:
            prov = r.get("provider") or ""
            mid = r.get("id") or ""
            if not prov or not mid:
                logger.warning(f"我的女友们: 搜索结果缺少 provider/id (番号 {code}): {r!r}")
                continue
            detail = client.get_movie(prov, mid)
            if detail is None:
                logger.warning(f"我的女友们: 获取 {prov}/{mid} 详情失败 (番号 {code})")
                continue
            details.append((prov, mid, detail))

        if not details:
            return None

        merged = self._merge_details([d for _, _, d in details])
        best_prov, best_mid, _ = details[0]
        return [self._build_mediainfo(merged, best_prov, best_mid, code=code)]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/scrape",
                "endpoint": self._api_scrape,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "手动刮削JAV元数据",
                "description": "传入文件路径或番号进行手动刮削",
            },
            {
                "path": "/search",
                "endpoint": self._api_search,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "搜索JAV信息",
                "description": "通过番号搜索JAV元数据",
            },
            {
                "path": "/history",
                "endpoint": self._api_history,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取刮削历史",
                "description": "返回最近刮削记录",
            },
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            return [
                {
                    "id": "MyGirlfriends",
                    "name": "我的女友们 定时扫描",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self._full_scan,
                    "kwargs": {},
                }
            ]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        translate_engines = [
            {"title": "Google 翻译", "value": "google"},
            {"title": "Google Free 翻译", "value": "googlefree"},
            {"title": "百度翻译", "value": "baidu"},
            {"title": "DeepL 翻译", "value": "deepl"},
            {"title": "OpenAI 翻译", "value": "openai"},
        ]
        translate_langs = [
            {"title": "简体中文", "value": "zh-CN"},
            {"title": "繁体中文", "value": "zh-TW"},
            {"title": "English", "value": "en"},
            {"title": "日本語", "value": "ja"},
        ]
        recognition_modes = [
            {"title": "关闭", "value": "disabled"},
            {"title": "劫持模式（推荐）", "value": "hijacking"},
        ]
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "auto_scrape",
                                            "label": "整理后自动刮削",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "发送通知",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "server_url",
                                            "label": "MetaTube 服务器地址",
                                            "placeholder": "http://localhost:8080",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "token",
                                            "label": "Token（可选）",
                                            "placeholder": "Bearer Token",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "translate",
                                            "label": "翻译标题",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "translate_engine",
                                            "label": "翻译引擎",
                                            "items": translate_engines,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "translate_to",
                                            "label": "目标语言",
                                            "items": translate_langs,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "recognize_media_enabled",
                                            "label": "识别接管（劫持 MoviePilot 识别链）",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "recognition_mode",
                                            "label": "识别模式",
                                            "items": recognition_modes,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "启用后，文件名中包含 JAV 番号（如 SSIS-001）将通过 MetaTube 识别，不再由 TMDB 处理；其他文件不受影响。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "providers",
                                            "label": "识别优先提供商（每行一个）",
                                            "placeholder": "javbus\njav321",
                                            "rows": 3,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "overwrite",
                                            "label": "覆盖已有文件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "定时扫描周期",
                                            "placeholder": "0 3 * * *",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "scan_paths",
                                            "label": "扫描目录",
                                            "placeholder": "每行一个目录路径",
                                            "rows": 3,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "exclude_paths",
                                            "label": "排除目录",
                                            "placeholder": "每行一个目录路径",
                                            "rows": 3,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "server_url": "",
            "token": "",
            "auto_scrape": False,
            "recognize_media_enabled": False,
            "recognition_mode": "disabled",
            "translate": False,
            "translate_engine": "google",
            "translate_to": "zh-CN",
            "overwrite": False,
            "cron": "",
            "onlyonce": False,
            "notify": False,
            "scan_paths": "",
            "exclude_paths": "",
            "providers": "",
        }

    def get_page(self) -> List[dict]:
        history = self.get_data("history") or []
        if not history:
            return [
                {
                    "component": "div",
                    "text": "暂无刮削记录",
                    "props": {
                        "class": "text-center text-grey pa-4",
                    },
                }
            ]
        contents = []
        for record in history[:20]:
            contents.append(
                {
                    "component": "VCard",
                    "props": {"class": "mb-2"},
                    "content": [
                        {
                            "component": "VCardText",
                            "content": [
                                {
                                    "component": "VRow",
                                    "content": [
                                        {
                                            "component": "VCol",
                                            "props": {"cols": 3, "md": 2},
                                            "content": [
                                                {
                                                    "component": "VImg",
                                                    "props": {
                                                        "src": record.get("poster", ""),
                                                        "height": 120,
                                                        "cover": True,
                                                        "class": "rounded",
                                                    },
                                                }
                                            ],
                                        },
                                        {
                                            "component": "VCol",
                                            "props": {"cols": 9, "md": 10},
                                            "content": [
                                                {
                                                    "component": "div",
                                                    "text": f"{record.get('number', '')} - {record.get('title', '')}",
                                                    "props": {"class": "text-subtitle-1 font-weight-bold"},
                                                },
                                                {
                                                    "component": "div",
                                                    "text": f"演员: {record.get('actors', '')}",
                                                    "props": {"class": "text-body-2"},
                                                },
                                                {
                                                    "component": "div",
                                                    "text": f"时间: {record.get('time', '')}",
                                                    "props": {"class": "text-caption text-grey"},
                                                },
                                            ],
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            )
        return contents

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                self._scheduler = None
                self._event.clear()
        except Exception as e:
            logger.error(f"我的女友们: 停止服务失败 - {e}")

    # --- MetaTube API calls (delegating wrappers around MetaTubeClient) ---

    def _ensure_client(self) -> Optional[MetaTubeClient]:
        if not self._server_url:
            logger.error("我的女友们: 服务器地址未配置")
            return None
        if self._client is None:
            self._client = MetaTubeClient(self._server_url, self._token)
        return self._client

    def _search_movie(self, number: str) -> Optional[List[dict]]:
        client = self._ensure_client()
        if client is None:
            return None
        return client.search_movie(number)

    def _get_movie(self, provider: str, movie_id: str) -> Optional[dict]:
        client = self._ensure_client()
        if client is None:
            return None
        return client.get_movie(provider, movie_id)

    def _search_actor(self, name: str) -> Optional[List[dict]]:
        client = self._ensure_client()
        if client is None:
            return None
        return client.search_actor(name)

    def _get_actor(self, provider: str, actor_id: str) -> Optional[dict]:
        client = self._ensure_client()
        if client is None:
            return None
        return client.get_actor(provider, actor_id)

    def _translate_text(self, text: str) -> Optional[str]:
        if not self._translate or not text:
            return text
        client = self._ensure_client()
        if client is None:
            return text
        translated = client.translate(text, self._translate_to, self._translate_engine)
        if translated:
            return translated
        return text

    def _get_image_url(self, image_type: str, provider: str, movie_id: str) -> str:
        client = self._ensure_client()
        if client is None:
            # Preserve historic behaviour: produce a URL even when no client
            # is configured (callers may compose this string into NFO output
            # before the user fills in the server URL).
            return f"{self._server_url}/v1/images/{image_type}/{provider}/{movie_id}"
        return client.image_url(image_type, provider, movie_id)

    # --- JAV number extraction ---

    def _extract_jav_number(self, filepath: str) -> Optional[str]:
        return extract_jav_code(filepath)

    # --- NFO generation ---

    def _gen_nfo(self, movie_info: dict) -> Optional[bytes]:
        doc = minidom.Document()
        root = DomUtils.add_node(doc, doc, "movie")

        DomUtils.add_node(doc, root, "title", movie_info.get("title", ""))
        DomUtils.add_node(doc, root, "originaltitle", movie_info.get("title", ""))
        DomUtils.add_node(doc, root, "sorttitle", movie_info.get("number", ""))
        DomUtils.add_node(doc, root, "num", movie_info.get("number", ""))

        uniqueid = DomUtils.add_node(doc, root, "uniqueid", movie_info.get("id", ""))
        uniqueid.setAttribute("type", "metatube")
        uniqueid.setAttribute("default", "true")

        xplot = DomUtils.add_node(doc, root, "plot")
        xplot.appendChild(doc.createCDATASection(movie_info.get("summary", "")))
        xoutline = DomUtils.add_node(doc, root, "outline")
        xoutline.appendChild(doc.createCDATASection(movie_info.get("summary", "")))

        if movie_info.get("director"):
            DomUtils.add_node(doc, root, "director", movie_info["director"])

        DomUtils.add_node(doc, root, "studio", movie_info.get("maker", ""))
        DomUtils.add_node(doc, root, "label", movie_info.get("label", ""))
        DomUtils.add_node(doc, root, "series", movie_info.get("series", ""))

        if movie_info.get("release_date"):
            release_date = movie_info["release_date"]
            DomUtils.add_node(doc, root, "premiered", release_date)
            DomUtils.add_node(doc, root, "releasedate", release_date)
            if len(release_date) >= 4:
                DomUtils.add_node(doc, root, "year", release_date[:4])

        if movie_info.get("runtime"):
            DomUtils.add_node(doc, root, "runtime", str(movie_info["runtime"]))

        if movie_info.get("score"):
            DomUtils.add_node(doc, root, "rating", str(movie_info["score"]))

        for genre in movie_info.get("genres", []):
            DomUtils.add_node(doc, root, "genre", genre)
            DomUtils.add_node(doc, root, "tag", genre)

        for actor_name in movie_info.get("actors", []):
            xactor = DomUtils.add_node(doc, root, "actor")
            DomUtils.add_node(doc, xactor, "name", actor_name)
            DomUtils.add_node(doc, xactor, "type", "Actor")

        if movie_info.get("cover_url"):
            DomUtils.add_node(doc, root, "art")
            xposter = DomUtils.add_node(doc, root, "poster")
            xposter.appendChild(doc.createTextNode(movie_info["cover_url"]))
            xfanart = DomUtils.add_node(doc, root, "fanart")
            if movie_info.get("big_cover_url"):
                xfanart.appendChild(doc.createTextNode(movie_info["big_cover_url"]))
            elif movie_info.get("cover_url"):
                xfanart.appendChild(doc.createTextNode(movie_info["cover_url"]))

        if movie_info.get("homepage"):
            DomUtils.add_node(doc, root, "website", movie_info["homepage"])

        return doc.toprettyxml(indent="  ", encoding="utf-8")

    # --- Image downloading ---

    def _download_image(self, url: str, save_path: Path) -> bool:
        if not url:
            return False
        if save_path.exists() and not self._overwrite:
            return True
        try:
            headers = {}
            if self._token and url.startswith(self._server_url):
                headers["Authorization"] = f"Bearer {self._token}"
            resp = requests.get(url, headers=headers, timeout=60, stream=True)
            resp.raise_for_status()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except Exception as e:
            logger.error(f"我的女友们: 下载图片失败 {url} - {e}")
            return False

    # --- Core scraping logic ---

    def _scrape_file(self, filepath: str) -> Optional[dict]:
        number = self._extract_jav_number(filepath)
        if not number:
            return None

        logger.info(f"我的女友们: 识别到番号 {number}，文件: {filepath}")

        results = self._search_movie(number)
        if not results:
            logger.warning(f"我的女友们: 搜索番号 {number} 无结果")
            return None

        best = results[0]
        provider = best.get("provider", "")
        movie_id = best.get("id", "")

        movie_info = self._get_movie(provider, movie_id)
        if not movie_info:
            logger.warning(f"我的女友们: 获取 {provider}/{movie_id} 详情失败")
            return None

        if self._translate and movie_info.get("title"):
            translated = self._translate_text(movie_info["title"])
            if translated and translated != movie_info["title"]:
                movie_info["original_title"] = movie_info["title"]
                movie_info["title"] = translated

        if self._translate and movie_info.get("summary"):
            translated_summary = self._translate_text(movie_info["summary"])
            if translated_summary:
                movie_info["summary"] = translated_summary

        file_path = Path(filepath)
        parent_dir = file_path.parent

        nfo_content = self._gen_nfo(movie_info)
        if nfo_content:
            nfo_path = parent_dir / f"{file_path.stem}.nfo"
            if not nfo_path.exists() or self._overwrite:
                nfo_path.write_bytes(nfo_content)
                logger.info(f"我的女友们: 已生成 NFO - {nfo_path}")

        poster_url = movie_info.get("cover_url") or movie_info.get("thumb_url")
        if poster_url:
            if poster_url.startswith("http") and not poster_url.startswith(self._server_url):
                proxy_url = self._get_image_url("primary", provider, movie_id)
            else:
                proxy_url = poster_url
            poster_path = parent_dir / f"{file_path.stem}-poster.jpg"
            self._download_image(proxy_url, poster_path)

        fanart_url = movie_info.get("big_cover_url") or movie_info.get("cover_url")
        if fanart_url:
            if fanart_url.startswith("http") and not fanart_url.startswith(self._server_url):
                proxy_url = self._get_image_url("backdrop", provider, movie_id)
            else:
                proxy_url = fanart_url
            fanart_path = parent_dir / f"{file_path.stem}-fanart.jpg"
            self._download_image(proxy_url, fanart_path)

        thumb_url = movie_info.get("thumb_url")
        if thumb_url:
            if thumb_url.startswith("http") and not thumb_url.startswith(self._server_url):
                proxy_url = self._get_image_url("thumb", provider, movie_id)
            else:
                proxy_url = thumb_url
            thumb_path = parent_dir / f"{file_path.stem}-thumb.jpg"
            self._download_image(proxy_url, thumb_path)

        record = {
            "number": movie_info.get("number", number),
            "title": movie_info.get("title", ""),
            "actors": ", ".join(movie_info.get("actors", [])),
            "poster": poster_url or "",
            "filepath": filepath,
            "provider": provider,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._add_history(record)

        if self._notify:
            actors_str = ", ".join(movie_info.get("actors", []))
            self.post_message(
                mtype=NotificationType.MediaServer,
                title="【我的女友们完成】",
                text=(
                    f"番号: {movie_info.get('number', number)}\n"
                    f"标题: {movie_info.get('title', '')}\n"
                    f"演员: {actors_str}\n"
                    f"文件: {filepath}"
                ),
            )

        return movie_info

    def _add_history(self, record: dict):
        history = self.get_data("history") or []
        history.insert(0, record)
        history = history[:100]
        self.save_data("history", history)

    # --- Event handler ---

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event):
        if not self._enabled or not self._auto_scrape:
            return
        if not self._server_url:
            return

        event_data = event.event_data or {}
        transferinfo = event_data.get("transferinfo")
        if not transferinfo:
            return

        target_path = None
        if hasattr(transferinfo, "target_path"):
            target_path = transferinfo.target_path
        elif hasattr(transferinfo, "file_list_new"):
            file_list = transferinfo.file_list_new
            if file_list:
                target_path = file_list[0] if isinstance(file_list, list) else file_list
        elif isinstance(transferinfo, dict):
            target_path = transferinfo.get("target_path") or ""
            if not target_path:
                file_list = transferinfo.get("file_list_new", [])
                if file_list:
                    target_path = file_list[0] if isinstance(file_list, list) else file_list

        if not target_path:
            return

        target_path = str(target_path)
        number = self._extract_jav_number(target_path)
        if not number:
            return

        if self._exclude_paths:
            for exc in self._exclude_paths.splitlines():
                exc = exc.strip()
                if exc and target_path.startswith(exc):
                    logger.info(f"我的女友们: 路径 {target_path} 在排除列表中，跳过")
                    return

        logger.info(f"我的女友们: 整理完成事件，开始刮削 {target_path}")
        self._scrape_file(target_path)

    # --- Full scan ---

    def _full_scan(self):
        if not self._scan_paths:
            logger.warning("我的女友们: 未配置扫描目录")
            return

        self._event.clear()
        total = 0
        success = 0

        media_exts = set(settings.RMT_MEDIAEXT) if hasattr(settings, "RMT_MEDIAEXT") else {
            ".mp4", ".mkv", ".avi", ".wmv", ".rmvb", ".rm", ".mov", ".flv",
            ".ts", ".m2ts", ".iso", ".strm",
        }

        for scan_dir in self._scan_paths.splitlines():
            scan_dir = scan_dir.strip()
            if not scan_dir:
                continue
            scan_path = Path(scan_dir)
            if not scan_path.exists():
                logger.warning(f"我的女友们: 扫描目录不存在 {scan_dir}")
                continue

            logger.info(f"我的女友们: 开始扫描 {scan_dir}")

            for media_file in scan_path.rglob("*"):
                if self._event.is_set():
                    logger.info("我的女友们: 扫描被中断")
                    return

                if not media_file.is_file():
                    continue
                if media_file.suffix.lower() not in media_exts:
                    continue

                filepath = str(media_file)

                if self._exclude_paths:
                    excluded = False
                    for exc in self._exclude_paths.splitlines():
                        exc = exc.strip()
                        if exc and filepath.startswith(exc):
                            excluded = True
                            break
                    if excluded:
                        continue

                number = self._extract_jav_number(filepath)
                if not number:
                    continue

                nfo_path = media_file.parent / f"{media_file.stem}.nfo"
                if nfo_path.exists() and not self._overwrite:
                    continue

                total += 1
                result = self._scrape_file(filepath)
                if result:
                    success += 1
                time.sleep(1)

        logger.info(f"我的女友们: 扫描完成，共 {total} 个文件，成功 {success} 个")

        if self._notify and total > 0:
            self.post_message(
                mtype=NotificationType.MediaServer,
                title="【JAV MetaTube 扫描完成】",
                text=f"扫描文件: {total}\n成功刮削: {success}",
            )

    # --- API endpoints ---

    def _api_scrape(self, filepath: str = None, number: str = None) -> dict:
        if not self._server_url:
            return {"success": False, "message": "MetaTube 服务器未配置"}

        if filepath:
            result = self._scrape_file(filepath)
            if result:
                return {"success": True, "data": result}
            return {"success": False, "message": "刮削失败"}

        if number:
            results = self._search_movie(number)
            if not results:
                return {"success": False, "message": f"番号 {number} 无搜索结果"}
            best = results[0]
            movie_info = self._get_movie(best["provider"], best["id"])
            if movie_info:
                return {"success": True, "data": movie_info}
            return {"success": False, "message": "获取详情失败"}

        return {"success": False, "message": "请提供 filepath 或 number 参数"}

    def _api_search(self, q: str = "") -> dict:
        if not q:
            return {"success": False, "message": "请提供搜索关键词"}
        results = self._search_movie(q)
        if results:
            return {"success": True, "data": results}
        return {"success": False, "message": "无搜索结果"}

    def _api_history(self) -> dict:
        history = self.get_data("history") or []
        return {"success": True, "data": history}
