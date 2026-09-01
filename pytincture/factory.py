"""ASGI application factory with isolated backend module state."""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import uuid
from importlib.machinery import SourceFileLoader
from types import ModuleType
from typing import Mapping, Optional

from fastapi import FastAPI

from .configuration import PytinctureConfig, configuration_context


_FACTORY_LOCK = threading.RLock()


class _ConfigurationContextMiddleware:
    def __init__(self, app, config: PytinctureConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        with configuration_context(self.config):
            await self.app(scope, receive, send)


def _load_backend(config: PytinctureConfig) -> ModuleType:
    backend_path = os.path.join(os.path.dirname(__file__), "backend", "app.py")
    module_name = f"pytincture.backend._instance_{uuid.uuid4().hex}"
    loader = SourceFileLoader(module_name, backend_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise RuntimeError("Unable to create the Pytincture backend module")
    module = importlib.util.module_from_spec(spec)
    # The backend constructs its module-local environment facade from this
    # already validated configuration. Never publish instance settings through
    # the process-global environment, even briefly.
    module._PYTINCTURE_CONFIG = config
    sys.modules[module_name] = module
    try:
        with configuration_context(config):
            loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def create_app(
    config: Optional[PytinctureConfig | Mapping[str, object]] = None,
) -> FastAPI:
    """Create an isolated Pytincture FastAPI application.

    ``None`` reads the current process environment. A mapping is treated as
    explicit dataclass field overrides on top of environment-derived values.
    Passing ``PytinctureConfig`` uses only that object's values.
    """

    if config is None:
        resolved = PytinctureConfig.from_env()
    elif isinstance(config, PytinctureConfig):
        resolved = config
    elif isinstance(config, Mapping):
        resolved = PytinctureConfig.from_env(**dict(config))
    else:
        raise TypeError("config must be PytinctureConfig, a mapping, or None")

    with _FACTORY_LOCK:
        backend = _load_backend(resolved)
    application = backend.app
    application.add_middleware(_ConfigurationContextMiddleware, config=resolved)
    application.state.pytincture_config = resolved
    application.state.pytincture_backend = backend
    return application
