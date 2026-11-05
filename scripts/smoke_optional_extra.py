#!/usr/bin/env python3
"""Clean-install smoke checks for the base package and each supported extra."""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path


OPTIONAL_DISTRIBUTIONS = {
    "httpx",
    "boto3",
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
    elif feature == "microapps":
        import asyncio
        import httpx
        from pytincture import PytinctureConfig, create_app
        from pytincture.microapps import LiveMicroappManager, create_microapp_host

        async def exercise():
            with tempfile.TemporaryDirectory() as store:
                config = PytinctureConfig()
                manager = LiveMicroappManager(store, config)
                app = create_microapp_host(create_app(config), manager,
                                           authorize=lambda scope, app_id: True)
                await manager.start()
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url="http://127.0.0.1") as client:
                        response = await client.get("/_microapps/catalog")
                        assert response.status_code == 200
                        assert response.json() == {"schema": 1, "revision": 0, "apps": []}
                finally:
                    await manager.close()
        asyncio.run(exercise())
    elif feature == "microapps-s3":
        import asyncio
        import boto3
        from botocore.stub import Stubber
        from pytincture.microapps import S3ObjectStore, EnvironmentMicroappManager, empty_manifest
        client = boto3.client("s3", region_name="us-east-1",
                              aws_access_key_id="testing", aws_secret_access_key="testing")
        with Stubber(client) as stub:
            stub.add_response("put_object", {"ETag": '"created"'}, {
                "Bucket": "smoke-test", "Key": "dev/environment.json", "Body": b"{}", "IfNoneMatch": "*"})
            result = asyncio.run(S3ObjectStore("smoke-test", "dev", client=client).put("environment.json", b"{}"))
            assert result == '"created"'
            stub.assert_no_pending_responses()
        assert empty_manifest("dev-smoke")["active"] is None
        assert EnvironmentMicroappManager is not None
    elif feature == "oauth":
        os.environ["ENABLE_GOOGLE_AUTH"] = "true"
        os.environ["GOOGLE_CLIENT_ID"] = "clean-install-client"
        os.environ["GOOGLE_CLIENT_SECRET"] = "clean-install-client-secret"
        os.environ["SAML_SECRET_KEY"] = "clean-install-oauth-secret-0123456789abcdef"
        os.environ["PYTINCTURE_ALLOWED_HOSTS"] = "auth.example.test"
        os.environ["PYTINCTURE_CANONICAL_ORIGIN"] = "https://auth.example.test"
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
        os.environ["SAML_IDP_ENTITY_ID"] = "https://idp.example.test/entity"
        os.environ["SAML_IDP_SSO_URL"] = "https://idp.example.test/sso"
        os.environ["SAML_IDP_X509_CERT"] = "clean-install-certificate"
        os.environ["SAML_SECRET_KEY"] = "clean-install-saml-secret-0123456789abcdef"
        os.environ["PYTINCTURE_ALLOWED_HOSTS"] = "auth.example.test"
        os.environ["PYTINCTURE_CANONICAL_ORIGIN"] = "https://auth.example.test"
        import pytincture.backend.app as backend

        assert backend.OneLogin_Saml2_Auth is not None
    elif feature == "mcp":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        modules = tempfile.TemporaryDirectory()
        modules_path = Path(modules.name)
        (modules_path / "demoapp.py").write_text("from service import Service\n")
        (modules_path / "service.py").write_text(
            "from pytincture.dataclass import backend_for_frontend\n"
            "@backend_for_frontend\n"
            "class Service:\n"
            "    def status(self): return {'ready': True}\n"
        )
        public_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        ).public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        os.environ["MODULES_PATH"] = str(modules_path)
        os.environ["ENABLE_MCP"] = "true"
        os.environ["MCP_TOOLS"] = json.dumps([{
            "name": "status", "application": "demoapp", "module": "service.py",
            "class": "Service", "method": "status", "scopes": ["status:read"],
        }])
        os.environ["MCP_ALLOWED_HOSTS"] = '["mcp.example.test"]'
        os.environ["MCP_ALLOWED_ORIGINS"] = '["https://mcp.example.test"]'
        os.environ["MCP_JWT_PUBLIC_KEY"] = public_key
        os.environ["MCP_JWT_ISSUER"] = "https://issuer.example.test"
        os.environ["MCP_JWT_AUDIENCE"] = "pytincture-smoke"
        import pytincture.backend.app as backend

        assert backend.mcp_http_app is not None
    else:
        raise SystemExit(f"unknown feature: {feature}")

    print(f"pytincture clean-install smoke passed: {feature}")


if __name__ == "__main__":
    main()
