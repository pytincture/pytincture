#!/usr/bin/env python3
"""Audit a live Pytincture TLS edge and emit redacted qualification evidence."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shlex
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


SCHEMA_ID = (
    "https://github.com/pytincture/pytincture/contracts/"
    "production-edge-evidence-v1.schema.json"
)
MAX_RESPONSE_BYTES = 1024 * 1024
COMMIT = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^1\.0\.0(?:rc[1-9]\d*)?$")
HOSTILE_FORWARD_HOST = "edge-probe.invalid"
HOSTILE_FORWARDED_FOR = "198.51.100.77"


@dataclass(frozen=True)
class ProbeResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass
class _NginxBlock:
    header: tuple[str, ...]
    directives: list[tuple[str, ...]]
    children: list["_NginxBlock"]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _read_bounded(response) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("edge response exceeds the 1 MiB evidence limit")
    return body


def _fetch(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    tls_context: ssl.SSLContext,
) -> ProbeResponse:
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=tls_context))
    request = Request(
        url,
        method="GET",
        headers={"User-Agent": "pytincture-production-edge-audit/1", **headers},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return ProbeResponse(
                status=response.status,
                headers={key.lower(): value for key, value in response.headers.items()},
                body=_read_bounded(response),
            )
    except HTTPError as response:
        return ProbeResponse(
            status=response.code,
            headers={key.lower(): value for key, value in response.headers.items()},
            body=_read_bounded(response),
        )
    except (OSError, URLError) as exc:
        raise ValueError(f"edge request failed for {url}: {exc}") from exc


def _origin(value: str, expected_scheme: str, field: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != expected_scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field} must be an exact {expected_scheme.upper()} origin")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} contains an invalid port") from exc
    return value.rstrip("/")


def _probe_path(value: str, field: str) -> str:
    parsed = urlsplit(value)
    if not value.startswith("/") or parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError(f"{field} must be an origin-relative path")
    return value


def _absolute_url(value: str, field: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{field} must be an absolute credential-free HTTP(S) URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} contains an invalid port") from exc
    return value


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("tested_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("tested_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _expanded_text(response: ProbeResponse) -> str:
    text = response.headers.get("location", "") + "\n" + response.body.decode(
        "utf-8", errors="replace"
    )
    text = html.unescape(text)
    for _ in range(3):
        expanded = unquote(text)
        if expanded == text:
            break
        text = expanded
    return text


def _hsts_max_age(value: str) -> int | None:
    for directive in value.split(";"):
        name, separator, raw_value = directive.strip().partition("=")
        if name.lower() != "max-age" or not separator:
            continue
        if not raw_value.strip().isdigit():
            return None
        return int(raw_value.strip())
    return None


def _parse_nginx(config: str) -> _NginxBlock:
    lexer = shlex.shlex(config, posix=True, punctuation_chars="{};")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    tokens = list(lexer)
    index = 0

    def parse_scope(header: tuple[str, ...] = ()) -> _NginxBlock:
        nonlocal index
        block = _NginxBlock(header, [], [])
        statement: list[str] = []
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == ";":
                if statement:
                    block.directives.append(tuple(statement))
                    statement = []
                continue
            if token == "{":
                if not statement:
                    raise ValueError("nginx config contains a block without a header")
                block.children.append(parse_scope(tuple(statement)))
                statement = []
                continue
            if token == "}":
                if statement:
                    raise ValueError("nginx config contains an unterminated directive")
                return block
            statement.append(token)
        if header:
            raise ValueError("nginx config contains an unterminated block")
        if statement:
            raise ValueError("nginx config contains an unterminated directive")
        return block

    root = parse_scope()
    if index != len(tokens):
        raise ValueError("nginx config contains unexpected trailing tokens")
    return root


def _descendant_blocks(block: _NginxBlock, name: str) -> list[_NginxBlock]:
    matches = []
    for child in block.children:
        if child.header and child.header[0].casefold() == name.casefold():
            matches.append(child)
        matches.extend(_descendant_blocks(child, name))
    return matches


def _directive_values(block: _NginxBlock, name: str) -> list[tuple[str, ...]]:
    return [
        directive[1:]
        for directive in block.directives
        if directive and directive[0].casefold() == name.casefold()
    ]


def _listen_matches(values: tuple[str, ...], expected_port: int) -> bool:
    if not values:
        return False
    address = values[0].casefold()
    return address == str(expected_port) or address.endswith(f":{expected_port}")


def _matching_location(
    server: _NginxBlock,
    request_path: str,
) -> _NginxBlock | None:
    ranked: list[tuple[int, _NginxBlock]] = []
    for location in server.children:
        header = location.header
        if not header or header[0].casefold() != "location" or len(header) < 2:
            continue
        modifier = ""
        pattern_index = 1
        if header[1] in {"=", "^~", "~", "~*"}:
            modifier = header[1]
            pattern_index = 2
        if len(header) <= pattern_index or modifier in {"~", "~*"}:
            continue
        pattern = header[pattern_index]
        if pattern.startswith("@"):
            continue
        if modifier == "=":
            if request_path == pattern:
                ranked.append((1_000_000 + len(pattern), location))
        elif request_path.startswith(pattern):
            ranked.append((len(pattern), location))
    if not ranked:
        return None
    best_score = max(score for score, _ in ranked)
    best = [block for score, block in ranked if score == best_score]
    return best[0] if len(best) == 1 else None


def _nginx_proxy_checks(
    config: str,
    expected_host: str,
    request_path: str = "/",
    expected_port: int = 443,
) -> dict[str, bool]:
    root = _parse_nginx(config)
    servers = []
    for server in _descendant_blocks(root, "server"):
        names = {
            value.casefold()
            for values in _directive_values(server, "server_name")
            for value in values
        }
        listens = _directive_values(server, "listen")
        if expected_host.casefold() in names and any(
            _listen_matches(values, expected_port) for values in listens
        ):
            servers.append(server)
    server = servers[0] if len(servers) == 1 else None
    location = _matching_location(server, request_path) if server else None

    effective_headers: dict[str, str] = {}
    if server and location:
        local_headers = _directive_values(location, "proxy_set_header")
        source_headers = local_headers or _directive_values(server, "proxy_set_header")
        for values in source_headers:
            if len(values) >= 2:
                effective_headers[values[0].casefold()] = " ".join(values[1:])
    proxy_targeted = bool(location and _directive_values(location, "proxy_pass"))
    host_value = effective_headers.get("host", "")
    forwarded_host = effective_headers.get("x-forwarded-host", "")
    forwarded_proto = effective_headers.get("x-forwarded-proto", "")
    forwarded_for = effective_headers.get("x-forwarded-for", "")
    unsafe_forward = re.compile(
        r"\$(?:http_)?(?:x_)?forwarded_|\$proxy_add_x_forwarded_for",
        re.I,
    )
    return {
        "target_vhost": server is not None,
        "target_location": proxy_targeted,
        "server_name": server is not None,
        "upstream_host_replaced": host_value == "$host",
        "forwarded_host_replaced": forwarded_host == "$host",
        "forwarded_proto_replaced": forwarded_proto == "$scheme",
        "forwarded_for_replaced": forwarded_for == "$remote_addr",
        "caller_forwarded_values_rejected": bool(effective_headers)
        and not any(unsafe_forward.search(value) for value in effective_headers.values()),
    }


def _body_sha256(response: ProbeResponse) -> str:
    return hashlib.sha256(response.body).hexdigest()


def build_evidence(
    args: argparse.Namespace,
    *,
    fetcher: Callable[..., ProbeResponse] = _fetch,
) -> dict:
    https_origin = _origin(args.https_origin, "https", "https_origin")
    http_origin = _origin(args.http_origin, "http", "http_origin")
    health_path = _probe_path(args.health_path, "health_path")
    canonical_probe_path = _probe_path(
        args.canonical_probe_path, "canonical_probe_path"
    )
    if not VERSION.fullmatch(args.version):
        raise ValueError("version must be 1.0.0rcN or 1.0.0")
    if not COMMIT.fullmatch(args.commit_sha):
        raise ValueError("commit_sha must be a full lowercase Git SHA")
    evidence_url = _absolute_url(str(args.evidence_url), "evidence_url")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("timeout must be finite and greater than zero")
    if args.hsts_min_age <= 0:
        raise ValueError("hsts_min_age must be greater than zero")

    tls_context = ssl.create_default_context(cafile=args.ca_file)
    health_url = urljoin(f"{https_origin}/", health_path.lstrip("/"))
    redirect_url = urljoin(f"{http_origin}/", health_path.lstrip("/"))
    canonical_url = urljoin(
        f"{https_origin}/", canonical_probe_path.lstrip("/")
    )
    client_probe_url = urljoin(f"{https_origin}/", "_pytincture/edge-client")
    expected_redirect = health_url
    hostile_headers = {
        "Forwarded": f"for=192.0.2.10;host={HOSTILE_FORWARD_HOST};proto=http",
        "X-Forwarded-Host": HOSTILE_FORWARD_HOST,
        "X-Forwarded-For": HOSTILE_FORWARDED_FOR,
        "X-Forwarded-Port": "80",
        "X-Forwarded-Proto": "http",
    }

    health = fetcher(
        health_url, headers={}, timeout=args.timeout, tls_context=tls_context
    )
    redirect = fetcher(
        redirect_url, headers={}, timeout=args.timeout, tls_context=tls_context
    )
    canonical = fetcher(
        canonical_url, headers={}, timeout=args.timeout, tls_context=tls_context
    )
    hostile = fetcher(
        canonical_url,
        headers=hostile_headers,
        timeout=args.timeout,
        tls_context=tls_context,
    )
    client_probe = fetcher(
        client_probe_url,
        headers=hostile_headers,
        timeout=args.timeout,
        tls_context=tls_context,
    )

    health_payload = {}
    try:
        health_payload = json.loads(health.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    observed_version = str(
        health_payload.get("distribution_version")
        or health_payload.get("version")
        or ""
    )
    hsts = health.headers.get("strict-transport-security", "")
    hsts_max_age = _hsts_max_age(hsts)
    redirect_location = urljoin(redirect_url, redirect.headers.get("location", ""))
    canonical_text = _expanded_text(canonical)
    hostile_text = _expanded_text(hostile)
    observed_client_host = ""
    try:
        client_probe_payload = json.loads(client_probe.body)
        if isinstance(client_probe_payload, dict):
            observed_client_host = str(client_probe_payload.get("client_host") or "")
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    client_ip_replaced = (
        client_probe.status == 200
        and bool(observed_client_host)
        and observed_client_host != HOSTILE_FORWARDED_FOR
    )

    proxy_config = Path(args.proxy_config).read_bytes()
    if len(proxy_config) > MAX_RESPONSE_BYTES:
        raise ValueError("proxy_config exceeds the 1 MiB evidence limit")
    proxy_checks = _nginx_proxy_checks(
        proxy_config.decode("utf-8"),
        urlsplit(https_origin).hostname or "",
        canonical_probe_path,
        urlsplit(https_origin).port or 443,
    )
    canonical_status_ok = 200 <= canonical.status < 400 and 200 <= hostile.status < 400
    canonical_origin_ok = (
        canonical_status_ok
        and https_origin in canonical_text
        and https_origin in hostile_text
        and HOSTILE_FORWARD_HOST not in hostile_text
    )
    checks = {
        "https_redirect": redirect.status in {301, 302, 307, 308}
        and redirect_location == expected_redirect,
        "hsts": hsts_max_age is not None and hsts_max_age >= args.hsts_min_age,
        "canonical_origin": canonical_origin_ok,
        "trusted_proxy_headers": (
            canonical_origin_ok and client_ip_replaced and all(proxy_checks.values())
        ),
        "tls_certificate_valid": health.status == 200,
        "version": observed_version == args.version,
    }
    findings = [name for name, passed in checks.items() if not passed]
    tested_at = _timestamp(args.tested_at)
    return {
        "$schema": SCHEMA_ID,
        "schema_version": 1,
        "status": "passed" if not findings else "failed",
        "tested_at": tested_at,
        "version": args.version,
        "commit_sha": args.commit_sha,
        "evidence_url": evidence_url,
        "https_origin": https_origin,
        "checks": checks,
        "observations": {
            "canonical_probe_body_sha256": _body_sha256(canonical),
            "canonical_probe_hostile_body_sha256": _body_sha256(hostile),
            "canonical_probe_path": canonical_probe_path,
            "canonical_probe_status": canonical.status,
            "canonical_probe_hostile_status": hostile.status,
            "client_ip_probe_body_sha256": _body_sha256(client_probe),
            "client_ip_probe_status": client_probe.status,
            "client_ip_forgery_rejected": client_ip_replaced,
            "health_path": health_path,
            "health_status": health.status,
            "health_version": observed_version,
            "hsts": hsts,
            "hsts_max_age": hsts_max_age,
            "http_redirect_location": redirect_location,
            "http_redirect_status": redirect.status,
            "proxy_config_sha256": hashlib.sha256(proxy_config).hexdigest(),
            "proxy_kind": "nginx",
            "proxy_replacement_checks": proxy_checks,
        },
        "findings": findings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--https-origin", required=True)
    parser.add_argument("--http-origin", required=True)
    parser.add_argument("--health-path", default="/healthz")
    parser.add_argument("--canonical-probe-path", required=True)
    parser.add_argument("--proxy-config", type=Path, required=True)
    parser.add_argument("--ca-file")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--hsts-min-age", type=int, default=31536000)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--evidence-url", required=True)
    parser.add_argument("--tested-at")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        evidence = build_evidence(args)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SystemExit(f"production-edge audit failed: {exc}") from exc
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"Pytincture production-edge audit: {evidence['status'].upper()}")
    print(f"- evidence: {args.output}")
    if evidence["findings"]:
        print(f"- failed checks: {', '.join(evidence['findings'])}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
