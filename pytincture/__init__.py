"""
pyTincture uvicorn launcher
"""

__version__ = "1.0.0rc3"

from multiprocessing import Process, freeze_support
from copy import deepcopy
import ipaddress
import logging
import os
import signal
import shutil
import zipfile
import asyncio

try:
    import uvloop  # type: ignore
except ImportError:  # pragma: no cover - depends on installation
    uvloop = None

import uvicorn

MODULES_PATH = os.environ.get("MODULES_PATH")


def set_modules_path(path=None):
    """
    Store the active modules path in-process and keep the environment in sync.
    """
    global MODULES_PATH
    MODULES_PATH = path
    if path is None:
        os.environ.pop("MODULES_PATH", None)
    else:
        os.environ["MODULES_PATH"] = path


def get_modules_path():
    """
    Retrieve the active modules path, falling back to the environment or CWD.
    """
    # An application created with create_app() owns its module root. The
    # context-local value takes priority without changing legacy globals.
    from .configuration import get_active_config

    active_config = get_active_config()
    if active_config is not None:
        return active_config.modules_path
    if MODULES_PATH is not None:
        return MODULES_PATH
    return os.environ.get("MODULES_PATH") or os.getcwd()


def _normalize_default_application(value):
    candidate = str(value).strip().strip("/")
    if (
        not candidate
        or candidate in (".", "..")
        or not all(char.isalnum() or char in "._-" for char in candidate)
    ):
        raise ValueError(
            "default_application must be a single application name without a path"
        )
    return candidate


def _normalize_favicon_folder(value, modules_folder):
    candidate = os.fsdecode(os.fspath(value)).strip()
    if not candidate:
        raise ValueError("favicon_folder must not be empty")

    candidate = os.path.expanduser(candidate)
    if not os.path.isabs(candidate):
        candidate = os.path.join(modules_folder, candidate)
    return os.path.abspath(candidate)


def _environment_flag(name):
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _development_email_login_enabled():
    return _environment_flag("ENABLE_USER_LOGIN") and _environment_flag(
        "ENABLE_DEV_EMAIL_LOGIN"
    )


def _development_auth_origin_enabled():
    return _environment_flag("PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN")


def _loopback_bind_host(host=None):
    """Resolve and validate the listener used by passwordless development login."""

    resolved = str(host).strip() if host is not None else ""
    development_only = (
        _development_email_login_enabled() or _development_auth_origin_enabled()
    )
    if not resolved:
        resolved = "127.0.0.1" if development_only else "0.0.0.0"
    if development_only:
        try:
            loopback = ipaddress.ip_address(resolved).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise RuntimeError(
                "Development authentication requires a literal loopback bind host "
                "such as 127.0.0.1 or ::1"
            )
    return resolved


class _PathOnlyAccessFilter(logging.Filter):
    """Remove query strings before Uvicorn formats an optional access record."""

    def filter(self, record):
        arguments = record.args
        if isinstance(arguments, tuple) and len(arguments) >= 3:
            sanitized = list(arguments)
            sanitized[2] = str(sanitized[2]).split("?", 1)[0][:2048]
            record.args = tuple(sanitized)
        return True


def _sanitized_uvicorn_log_config():
    config = deepcopy(uvicorn.config.LOGGING_CONFIG)
    config.setdefault("filters", {})["pytincture_path_only"] = {
        "()": _PathOnlyAccessFilter,
    }
    access_handler = config.get("handlers", {}).get("access", {})
    access_handler["filters"] = ["pytincture_path_only"]
    return config


def main(port, ssl_keyfile=None, ssl_certfile=None, modules_folder=None, host=None):
    if modules_folder is not None:
        set_modules_path(os.fspath(modules_folder))

    bind_host = _loopback_bind_host(host)
    config = PytinctureConfig.from_env()
    run_kwargs = dict(
        host=bind_host,
        port=port,
        log_level=config.log_level.lower(),
        access_log=config.uvicorn_access_log,
        reload=False,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
    if config.uvicorn_access_log:
        run_kwargs["log_config"] = _sanitized_uvicorn_log_config()

    if uvloop is not None:
        try:
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            run_kwargs["loop"] = "uvloop"
        except Exception:  # pragma: no cover - uvloop unsupported platform
            run_kwargs["loop"] = "asyncio"

    uvicorn.run(create_app(config), **run_kwargs)


def launch_service(
    modules_folder=os.getcwd(), 
    port=8070, 
    ssl_keyfile=None, 
    ssl_certfile=None, 
    env_vars: dict | None = None,
    bff_docs_path: str = "/bff-docs",
    bff_docs_title: str = "pyTincture BFF API",
    default_application=None,
    favicon_folder=None,
    host=None,
):
    modules_folder = os.fspath(modules_folder)
    set_modules_path(modules_folder)
    
    # Add BFF configuration to environment variables
    os.environ["BFF_DOCS_PATH"] = bff_docs_path.lstrip('/')  # Remove leading slash if present
    os.environ["BFF_DOCS_TITLE"] = bff_docs_title
        
    for akey, value in (env_vars or {}).items():
        if akey == "MODULES_PATH":
            continue
        os.environ[akey] = value

    if default_application is not None:
        os.environ["PYTINCTURE_DEFAULT_APPLICATION"] = (
            _normalize_default_application(default_application)
        )

    if favicon_folder is not None:
        os.environ["PYTINCTURE_FAVICON_FOLDER"] = _normalize_favicon_folder(
            favicon_folder,
            modules_folder,
        )

    # Validate the exact environment before a child process or network listener
    # is created. The child constructs the ASGI app through the same typed path.
    PytinctureConfig.from_env()

    bind_host = _loopback_bind_host(host)
    main_application = Process(
        target=main,
        args=(port, ssl_keyfile, ssl_certfile, modules_folder, bind_host),
    )
    # launch data and main applications
    main_application.start()
    
    def terminate_all(*args):
        main_application.terminate()

    signal.signal(signal.SIGINT, terminate_all)
    signal.signal(signal.SIGTERM, terminate_all)

    # wait for main application death
    main_application.join()


# Imported last so the backend can continue importing launcher helpers while
# create_app() loads an isolated copy of the backend module.
from .configuration import PytinctureConfig
from .factory import create_app
