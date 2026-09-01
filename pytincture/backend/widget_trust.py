"""Deployment-owned trust policy for browser widget distributions."""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


_MAX_POLICY_BYTES = 1024 * 1024
_MAX_WIDGETSETS = 64
_MAX_ASSETS = 128
_SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")


class WidgetTrustPolicyError(ValueError):
    """Raised when a widget trust policy or selection is invalid."""


def _load_policy_document(value: str) -> Any:
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.startswith("{"):
        encoded = candidate.encode("utf-8")
        if len(encoded) > _MAX_POLICY_BYTES:
            raise WidgetTrustPolicyError("widget trust policy exceeds 1 MiB")
        source = candidate
    else:
        path = Path(candidate).expanduser()
        try:
            if not path.is_file():
                raise WidgetTrustPolicyError(
                    f"widget trust policy is not a regular file: {path}"
                )
            encoded = path.read_bytes()
            if len(encoded) > _MAX_POLICY_BYTES:
                raise WidgetTrustPolicyError("widget trust policy exceeds 1 MiB")
            source = encoded.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise WidgetTrustPolicyError(
                f"unable to read widget trust policy: {path}"
            ) from exc
    try:
        return json.loads(source)
    except json.JSONDecodeError as exc:
        raise WidgetTrustPolicyError("widget trust policy must contain valid JSON") from exc


def _validate_asset(raw: Any, *, position: int) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {"path", "type", "sha256"}:
        raise WidgetTrustPolicyError(
            f"widget asset {position} must contain only path, type, and sha256"
        )
    path = raw.get("path")
    asset_type = raw.get("type")
    digest = raw.get("sha256")
    if not all(isinstance(value, str) for value in (path, asset_type, digest)):
        raise WidgetTrustPolicyError(f"widget asset {position} fields must be strings")
    normalized_path = posixpath.normpath(path)
    if (
        not path
        or normalized_path != path
        or path.startswith("/")
        or "\\" in path
        or normalized_path.startswith("../")
    ):
        raise WidgetTrustPolicyError(f"widget asset {position} has an unsafe path")
    if asset_type not in {"javascript", "css"}:
        raise WidgetTrustPolicyError(
            f"widget asset {position} type must be javascript or css"
        )
    expected_suffix = ".js" if asset_type == "javascript" else ".css"
    if not path.lower().endswith(expected_suffix):
        raise WidgetTrustPolicyError(
            f"widget asset {position} type does not match its path"
        )
    digest = digest.lower()
    if not _SHA256_PATTERN.fullmatch(digest):
        raise WidgetTrustPolicyError(
            f"widget asset {position} must contain a SHA-256 digest"
        )
    return {"path": path, "type": asset_type, "sha256": digest}


def canonical_widget_trust_policy(value: str | None) -> str | None:
    """Validate a path/JSON policy and return one canonical JSON document."""

    if value is None or not str(value).strip():
        return None
    raw = _load_policy_document(str(value))
    if not isinstance(raw, dict) or set(raw) != {"schema", "widgetsets"}:
        raise WidgetTrustPolicyError(
            "widget trust policy must contain only schema and widgetsets"
        )
    if raw.get("schema") != 1:
        raise WidgetTrustPolicyError("widget trust policy schema must be 1")
    entries = raw.get("widgetsets")
    if not isinstance(entries, list) or not 1 <= len(entries) <= _MAX_WIDGETSETS:
        raise WidgetTrustPolicyError(
            f"widget trust policy must declare between 1 and {_MAX_WIDGETSETS} widgetsets"
        )

    normalized_entries: list[dict[str, Any]] = []
    seen_specs: set[tuple[str, Version]] = set()
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != {
            "distribution",
            "version",
            "assets",
        }:
            raise WidgetTrustPolicyError(
                f"widgetset {position} must contain only distribution, version, and assets"
            )
        distribution = entry.get("distribution")
        version_text = entry.get("version")
        assets = entry.get("assets")
        if not isinstance(distribution, str) or not distribution.strip():
            raise WidgetTrustPolicyError(
                f"widgetset {position} requires a distribution name"
            )
        try:
            normalized_distribution = canonicalize_name(
                distribution.strip(), validate=True
            )
        except ValueError as exc:
            raise WidgetTrustPolicyError(
                f"widgetset {position} has an invalid distribution name"
            ) from exc
        if not isinstance(version_text, str):
            raise WidgetTrustPolicyError(f"widgetset {position} requires a version")
        try:
            version = Version(version_text.strip())
        except InvalidVersion as exc:
            raise WidgetTrustPolicyError(
                f"widgetset {position} has an invalid version"
            ) from exc
        if not isinstance(assets, list) or not 1 <= len(assets) <= _MAX_ASSETS:
            raise WidgetTrustPolicyError(
                f"widgetset {position} must declare between 1 and {_MAX_ASSETS} assets"
            )
        normalized_assets = [
            _validate_asset(asset, position=asset_position)
            for asset_position, asset in enumerate(assets, start=1)
        ]
        asset_paths = [asset["path"] for asset in normalized_assets]
        if len(set(asset_paths)) != len(asset_paths):
            raise WidgetTrustPolicyError(
                f"widgetset {position} contains duplicate asset paths"
            )
        spec = (normalized_distribution, version)
        if spec in seen_specs:
            raise WidgetTrustPolicyError(
                f"widgetset {position} duplicates {normalized_distribution}=={version}"
            )
        seen_specs.add(spec)
        normalized_entries.append(
            {
                "distribution": normalized_distribution,
                "version": str(version),
                "assets": normalized_assets,
            }
        )

    normalized_entries.sort(
        key=lambda entry: (entry["distribution"], Version(entry["version"]))
    )
    return json.dumps(
        {"schema": 1, "widgetsets": normalized_entries},
        sort_keys=True,
        separators=(",", ":"),
    )


def trusted_widget_manifest(
    canonical_policy: str | None,
    widget_spec: str,
) -> dict[str, Any] | None:
    """Return the administrator-owned manifest or reject an unlisted widget."""

    if canonical_policy is None:
        return None
    distribution, separator, version_text = widget_spec.partition("==")
    if not separator or not distribution.strip() or not version_text.strip():
        raise WidgetTrustPolicyError(
            "widget trust policy requires an exact distribution==version declaration"
        )
    try:
        normalized_distribution = canonicalize_name(
            distribution.strip(), validate=True
        )
        version = Version(version_text.strip())
    except (InvalidVersion, ValueError) as exc:
        raise WidgetTrustPolicyError("application declares an invalid widgetset") from exc

    policy = json.loads(canonical_policy)
    for entry in policy["widgetsets"]:
        if (
            entry["distribution"] == normalized_distribution
            and entry["version"] == str(version)
        ):
            return {
                "schema": 1,
                "package": entry["distribution"],
                "version": entry["version"],
                "assets": [dict(asset) for asset in entry["assets"]],
            }
    raise WidgetTrustPolicyError(
        f"widgetset {normalized_distribution}=={version} is not allowed by deployment policy"
    )
