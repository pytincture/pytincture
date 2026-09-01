import argparse
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_production_edge as edge


NGINX_CONFIG = """
server {
    listen 443 ssl;
    server_name app.example.test;
    location / {
        proxy_pass http://127.0.0.1:8070;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
"""


def _args(tmp_path: Path, **overrides):
    proxy_config = tmp_path / "nginx.conf"
    proxy_config.write_text(NGINX_CONFIG)
    values = {
        "https_origin": "https://app.example.test",
        "http_origin": "http://app.example.test",
        "health_path": "/healthz",
        "canonical_probe_path": "/demo/auth/saml/metadata",
        "proxy_config": proxy_config,
        "ca_file": None,
        "timeout": 5.0,
        "hsts_min_age": 31536000,
        "version": "1.0.0rc3",
        "commit_sha": "a" * 40,
        "evidence_url": "https://github.com/pytincture/pytincture/actions/runs/1",
        "tested_at": "2026-09-01T03:00:00Z",
        "output": tmp_path / "edge.json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _passing_fetcher(url, *, headers, timeout, tls_context):
    assert timeout == 5.0
    assert tls_context.verify_mode != 0
    if url == "https://app.example.test/healthz":
        return edge.ProbeResponse(
            200,
            {"strict-transport-security": "max-age=63072000; includeSubDomains"},
            b'{"status":"ok","version":"1.0.0rc3"}',
        )
    if url == "http://app.example.test/healthz":
        return edge.ProbeResponse(
            308,
            {"location": "https://app.example.test/healthz"},
            b"",
        )
    assert url == "https://app.example.test/demo/auth/saml/metadata"
    assert not headers or headers["X-Forwarded-Host"] == edge.HOSTILE_FORWARD_HOST
    return edge.ProbeResponse(
        200,
        {"content-type": "application/xml"},
        (
            b'<EntityDescriptor entityID="https://app.example.test/demo/auth/saml/metadata">'
            b"private-metadata-marker</EntityDescriptor>"
        ),
    )


def test_live_edge_evidence_passes_and_redacts_response_bodies(tmp_path):
    evidence = edge.build_evidence(_args(tmp_path), fetcher=_passing_fetcher)

    assert evidence["status"] == "passed"
    assert all(evidence["checks"].values())
    assert evidence["observations"]["hsts_max_age"] == 63072000
    assert evidence["observations"]["proxy_replacement_checks"] == {
        "server_name": True,
        "upstream_host_replaced": True,
        "forwarded_host_replaced": True,
        "forwarded_proto_replaced": True,
        "caller_forwarded_values_rejected": True,
    }
    assert "private-metadata-marker" not in json.dumps(evidence)
    assert evidence["findings"] == []


def test_live_edge_evidence_fails_closed_on_header_passthrough_and_poisoning(tmp_path):
    args = _args(tmp_path, hsts_min_age=63072000)
    args.proxy_config.write_text(
        NGINX_CONFIG.replace(
            "proxy_set_header X-Forwarded-Host $host;",
            "proxy_set_header X-Forwarded-Host $http_x_forwarded_host;",
        )
    )

    def unsafe_fetcher(url, *, headers, timeout, tls_context):
        response = _passing_fetcher(
            url, headers=headers, timeout=timeout, tls_context=tls_context
        )
        if url.startswith("https://") and url.endswith("/metadata") and headers:
            return edge.ProbeResponse(
                200,
                {},
                b'<EntityDescriptor entityID="http://edge-probe.invalid/metadata"/>',
            )
        if url.endswith("/healthz") and url.startswith("https://"):
            return edge.ProbeResponse(
                200,
                {"strict-transport-security": "max-age=31536000"},
                response.body,
            )
        return response

    evidence = edge.build_evidence(args, fetcher=unsafe_fetcher)

    assert evidence["status"] == "failed"
    assert evidence["checks"]["canonical_origin"] is False
    assert evidence["checks"]["trusted_proxy_headers"] is False
    assert evidence["checks"]["hsts"] is False
    assert set(evidence["findings"]) == {
        "hsts",
        "canonical_origin",
        "trusted_proxy_headers",
    }


@pytest.mark.parametrize(
    ("value", "scheme", "message"),
    [
        ("https://user@app.example", "https", "exact HTTPS origin"),
        ("https://app.example/path", "https", "exact HTTPS origin"),
        ("http://app.example", "https", "exact HTTPS origin"),
        ("http://app.example:bad", "http", "invalid port"),
    ],
)
def test_origin_validation_rejects_ambiguous_values(value, scheme, message):
    with pytest.raises(ValueError, match=message):
        edge._origin(value, scheme, "origin")


def test_edge_evidence_schema_matches_the_auditor_contract():
    schema = json.loads(
        (ROOT / "contracts" / "production-edge-evidence-v1.schema.json").read_text()
    )
    assert schema["$id"] == edge.SCHEMA_ID
    assert set(schema["properties"]["checks"]["required"]) == {
        "https_redirect",
        "hsts",
        "canonical_origin",
        "trusted_proxy_headers",
        "tls_certificate_valid",
        "version",
    }
