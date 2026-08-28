#!/usr/bin/env python3
"""Clean-install smoke checks for the base package and each supported extra."""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys


OPTIONAL_DISTRIBUTIONS = {
    "authlib",
    "fastmcp",
    "python3-saml",
    "upstash-redis",
}


def installed(name: str) -> bool:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def main() -> None:
    feature = sys.argv[1] if len(sys.argv) == 2 else "base"
    os.environ.setdefault("MODULES_PATH", os.getcwd())

    if feature == "base":
        unexpected = sorted(name for name in OPTIONAL_DISTRIBUTIONS if installed(name))
        if unexpected:
            raise SystemExit(f"base install contains optional distributions: {unexpected}")
        import pytincture.backend.app as backend

        assert backend.mcp_http_app is None
    elif feature == "oauth":
        os.environ["ENABLE_GOOGLE_AUTH"] = "true"
        os.environ["SAML_SECRET_KEY"] = "clean-install-oauth-secret-0123456789abcdef"
        import pytincture.backend.app as backend

        assert backend.OAuth is not None
    elif feature == "password":
        from argon2 import PasswordHasher
        from pytincture.backend.auth import verify_password

        encoded = PasswordHasher().hash("release-smoke")
        hashes = json.dumps({"release@example.com": encoded})
        assert verify_password("release@example.com", "release-smoke", hashes)
    elif feature == "redis":
        import upstash_redis

        from pytincture.backend.storage import RedisDict

        assert upstash_redis.Redis is not None
        assert RedisDict is not None
    elif feature == "saml":
        os.environ["ENABLE_SAML_AUTH"] = "true"
        os.environ["SAML_SECRET_KEY"] = "clean-install-saml-secret-0123456789abcdef"
        import pytincture.backend.app as backend

        assert backend.OneLogin_Saml2_Auth is not None
    elif feature == "mcp":
        os.environ["ENABLE_MCP"] = "true"
        os.environ["MCP_EXPOSED_OPERATIONS"] = "[]"
        import pytincture.backend.app as backend

        assert backend.mcp_http_app is not None
    else:
        raise SystemExit(f"unknown feature: {feature}")

    print(f"pytincture clean-install smoke passed: {feature}")


if __name__ == "__main__":
    main()
