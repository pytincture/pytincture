import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app


def _mcp_fixture(tmp_path):
    (tmp_path / "demoapp.py").write_text("from service import Service\n")
    (tmp_path / "service.py").write_text(
        """from pytincture.dataclass import backend_for_frontend

@backend_for_frontend
class Service:
    def greet(self, name, excited=False):
        return {"message": f"Hello, {name}" + ("!" if excited else "")}
"""
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    tools = json.dumps([{
        "name": "greet",
        "application": "demoapp",
        "module": "service.py",
        "class": "Service",
        "method": "greet",
        "scopes": ["demo:greet"],
        "description": "Return a greeting.",
    }])
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        enable_mcp=True,
        mcp_tools=tools,
        mcp_allowed_hosts=("mcp.example.test",),
        mcp_allowed_origins=("https://mcp.example.test",),
        mcp_jwt_public_key=public_pem,
        mcp_jwt_issuer="https://issuer.example.test",
        mcp_jwt_audience="pytincture-mcp",
    )
    return create_app(config), private_pem


def _token(private_key, scope="demo:greet"):
    return jwt.encode(
        {
            "iss": "https://issuer.example.test",
            "aud": "pytincture-mcp",
            "sub": "automation-client",
            "client_id": "automation-client",
            "scope": scope,
        },
        private_key,
        algorithm="RS256",
    )


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Origin": "https://mcp.example.test",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }


def _call_payload(arguments=None):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "greet", "arguments": arguments or {"name": "Ada"}},
    }


def test_mcp_transport_is_authenticated_scoped_and_exactly_routed(tmp_path):
    app, private_key = _mcp_fixture(tmp_path)
    with TestClient(app, base_url="https://mcp.example.test") as client:
        response = client.post(
            "/mcp",
            headers=_headers(_token(private_key)),
            json=_call_payload({"name": "Ada", "excited": True}),
        )
        assert response.status_code == 200
        assert '"message":"Hello, Ada!"' in response.text

        dispatcher_arguments = client.post(
            "/mcp",
            headers=_headers(_token(private_key)),
            json=_call_payload({
                "name": "Ada", "file_path": "other.py", "class_name": "Other",
            }),
        )
        assert dispatcher_arguments.status_code == 200
        assert '"isError":true' in dispatcher_arguments.text

        unauthorized = client.post("/mcp", json=_call_payload())
        assert unauthorized.status_code == 401

        wrong_scope = client.post(
            "/mcp",
            headers=_headers(_token(private_key, "different:scope")),
            json=_call_payload(),
        )
        assert wrong_scope.status_code == 200
        assert "Unknown tool" in wrong_scope.text


def test_mcp_transport_rejects_dns_rebinding_and_untrusted_origins(tmp_path):
    app, private_key = _mcp_fixture(tmp_path)
    headers = _headers(_token(private_key))
    with TestClient(app, base_url="https://mcp.example.test") as client:
        wrong_host = client.post(
            "/mcp", headers={**headers, "Host": "evil.example"}, json=_call_payload()
        )
        assert wrong_host.status_code == 421

        wrong_origin = client.post(
            "/mcp",
            headers={**headers, "Origin": "https://evil.example"},
            json=_call_payload(),
        )
        assert wrong_origin.status_code == 403


def test_mcp_configuration_fails_closed_without_transport_and_jwt_policy(tmp_path):
    with pytest.raises(ValueError, match="allowed hosts and origins"):
        PytinctureConfig(modules_path=str(tmp_path), enable_mcp=True)

    with pytest.raises(ValueError, match="exactly one JWT verification source"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            enable_mcp=True,
            mcp_allowed_hosts=("mcp.example.test",),
            mcp_allowed_origins=("https://mcp.example.test",),
        )
