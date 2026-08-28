"""ASGI application factory with isolated backend module state."""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import uuid
from contextlib import contextmanager
from importlib.machinery import SourceFileLoader
from types import ModuleType
from typing import Iterator, Mapping, Optional

from fastapi import FastAPI

from .configuration import PytinctureConfig, configuration_context


_FACTORY_LOCK = threading.RLock()


class _ConfiguredOS:
    """Delegate OS operations while resolving environment reads per app."""

    def __init__(self, values: Mapping[str, str]):
        self._values = dict(values)
        # A backend instance must not see configuration belonging to another
        # instance through the process-global environment.
        self.environ = self._values

    def getenv(self, key, default=None):
        return self._values.get(key, default)

    def __getattr__(self, name):
        return getattr(os, name)


class _ConfigurationContextMiddleware:
    def __init__(self, app, config: PytinctureConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        with configuration_context(self.config):
            await self.app(scope, receive, send)


@contextmanager
def _temporary_environment(
    values: Mapping[str, str], managed_names: set[str]
) -> Iterator[None]:
    missing = object()
    previous = {name: os.environ.get(name, missing) for name in managed_names}
    for name in managed_names:
        if name in values:
            os.environ[name] = values[name]
        else:
            os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is missing:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _load_backend(config: PytinctureConfig) -> ModuleType:
    backend_path = os.path.join(os.path.dirname(__file__), "backend", "app.py")
    module_name = f"pytincture.backend._instance_{uuid.uuid4().hex}"
    loader = SourceFileLoader(module_name, backend_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise RuntimeError("Unable to create the Pytincture backend module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    environment = config.to_environ()
    managed_names = set(PytinctureConfig.environment_names()) | set(config.environment)
    try:
        with configuration_context(config), _temporary_environment(environment, managed_names):
            loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    module.os = _ConfiguredOS(environment)
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
