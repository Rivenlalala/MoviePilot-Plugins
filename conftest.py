"""Repo-root pytest config for MoviePilot-Plugins.

Runs before test discovery so we can install stub modules for MoviePilot
runtime dependencies. Without these stubs, importing
``plugins.v2/mygirlfriends/__init__.py`` (which pytest does during package
inference for any test inside that tree) fails on the ``apscheduler`` /
``app.*`` imports the plugin needs at runtime.

The stubs are intentionally minimal — just enough to satisfy import-time
side effects. Tests that need real MoviePilot behaviour should mock more
specifically inside the test module.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def _ensure_stub(name: str, attrs: "dict | None" = None) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    for attr_name, attr_value in (attrs or {}).items():
        setattr(module, attr_name, attr_value)
    sys.modules[name] = module
    return module


# Third-party deps the plugin imports at module load time.
_ensure_stub("pytz", {"timezone": lambda *_a, **_kw: None})

_ensure_stub("apscheduler")
_ensure_stub("apscheduler.schedulers")
_ensure_stub(
    "apscheduler.schedulers.background",
    {"BackgroundScheduler": type("BackgroundScheduler", (), {})},
)
_ensure_stub("apscheduler.triggers")
_ensure_stub(
    "apscheduler.triggers.cron",
    {"CronTrigger": type("CronTrigger", (), {})},
)

# MoviePilot ``app.*`` modules — provide minimal surface used at import time.
_ensure_stub("app")
_ensure_stub("app.core")
_ensure_stub(
    "app.core.config",
    {"settings": types.SimpleNamespace(TZ="UTC", SECURITY_IMAGE_DOMAINS=[])},
)


class _StubMediaInfo:  # noqa: D401
    """Stub for ``app.core.context.MediaInfo``.

    The real MediaInfo is a dataclass with a custom ``__setattr__`` that just
    writes to ``__dict__``; the stub here matches that behaviour so the
    plugin's attribute-assignment pattern works unchanged under test.
    """

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


_ensure_stub("app.core.context", {"MediaInfo": _StubMediaInfo})


class _StubMediaType:  # noqa: D401
    """Stub for ``app.schemas.types.MediaType``."""

    MOVIE = "MOVIE"
    TV = "TV"
    COLLECTION = "COLLECTION"
    UNKNOWN = "UNKNOWN"


_ensure_stub("app.core.meta", {"MetaBase": type("MetaBase", (), {})})
_ensure_stub("app.core.meta.metabase", {"MetaBase": type("MetaBase", (), {})})

_ensure_stub(
    "app.core.event",
    {
        # ``register`` is used as a decorator factory in the plugin
        # (``@eventmanager.register(EventType.TransferComplete)``), so it
        # must return a callable that wraps the decorated function.
        "eventmanager": types.SimpleNamespace(
            register=lambda *_a, **_kw: (lambda fn: fn)
        ),
        "Event": type("Event", (), {}),
    },
)
_ensure_stub(
    "app.log",
    {
        "logger": types.SimpleNamespace(
            info=lambda *_a, **_kw: None,
            warning=lambda *_a, **_kw: None,
            warn=lambda *_a, **_kw: None,
            error=lambda *_a, **_kw: None,
            debug=lambda *_a, **_kw: None,
        )
    },
)
_ensure_stub(
    "app.plugins",
    {"_PluginBase": type("_PluginBase", (), {"update_config": lambda *_a, **_kw: None})},
)
_ensure_stub("app.schemas", {"NotificationType": type("NotificationType", (), {})})
_ensure_stub(
    "app.schemas.types",
    {
        "EventType": type("EventType", (), {"TransferComplete": "TransferComplete"}),
        "MediaType": _StubMediaType,
        "ChainEventType": type(
            "ChainEventType",
            (),
            {
                "MediaRecognizeConvert": "MediaRecognizeConvert",
                "TransferRename": "TransferRename",
            },
        ),
    },
)
_ensure_stub("app.utils")
_ensure_stub("app.utils.dom", {"DomUtils": type("DomUtils", (), {})})

# Make the standalone parser module importable without going through the
# plugin package (which pulls in heavier surface).
_PLUGIN_DIR = Path(__file__).resolve().parent / "plugins.v2" / "mygirlfriends"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
