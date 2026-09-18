"""Operator-managed credentials for BFF integrations.

Run ``python -m pytincture.api_clients --help``. Keep this SQLite registry
outside the application's served modules directory. Secrets are generated here,
never chosen by callers; SHA-256 is appropriate for these 256-bit random keys.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from pytincture.backend.safe_paths import validate_application_name


def normalize_grants(grants: list[dict]) -> list[dict]:
    """Validate exact module/class grants with optional explicit method lists."""
    if not isinstance(grants, list) or not 1 <= len(grants) <= 256:
        raise ValueError("Provide between 1 and 256 grants")
    result = []
    for grant in grants:
        if not isinstance(grant, dict) or set(grant) - {"module", "class", "methods"}:
            raise ValueError("Each grant requires module, class, and optional methods")
        module = grant.get("module", "")
        if isinstance(module, str) and module.endswith(".py"):
            module = module[:-3]
        if not isinstance(module, str) or not module or not all(
            part.isidentifier() and not part.startswith(".") for part in module.split("/")
        ):
            raise ValueError("Grant module must be an exact module path, such as services/catalog")
        cls = grant.get("class", "")
        if not isinstance(cls, str) or not cls.isidentifier() or cls.startswith("_"):
            raise ValueError("Grant class must be a public class identifier")
        normalized = {"module": module, "class": cls}
        if "methods" in grant:
            methods = grant["methods"]
            if not isinstance(methods, list) or not 1 <= len(methods) <= 256 or any(
                not isinstance(method, str) or not method.isidentifier() or method.startswith("_")
                for method in methods
            ):
                raise ValueError("Grant methods must be a nonempty list of public method identifiers")
            normalized["methods"] = sorted(set(methods))
        if normalized not in result:
            result.append(normalized)
    return result


def permits(client: dict, module: str, cls: str, method: str) -> bool:
    module = module.removesuffix(".py")
    return any(
        grant["module"] == module and grant["class"] == cls
        and ("methods" not in grant or method in grant["methods"])
        for grant in client["grants"]
    )


def _connect(path: str, *, write: bool = False):
    uri = Path(path).expanduser().resolve().as_uri() + ("?mode=rw" if write else "?mode=ro")
    connection = sqlite3.connect(uri, uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    return connection


def check_registry(path: str) -> None:
    with closing(_connect(path)) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported API client registry version")
        connection.execute("SELECT client_id, application, secret_hash, revision, enabled, grants FROM clients LIMIT 0")


def get_client(path: str, client_id: str) -> dict | None:
    if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id):
        return None
    with closing(_connect(path)) as connection:
        row = connection.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()
    if row is None:
        return None
    client = dict(row)
    client["grants"] = normalize_grants(json.loads(client["grants"]))
    return client


def authenticate_client(path: str, application: str, client_id: str, secret: str) -> dict | None:
    client = get_client(path, client_id)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    matches = hmac.compare_digest(digest, client["secret_hash"] if client else "0" * 64)
    if not matches or not client or not client["enabled"] or client["application"] != application:
        return None
    return client


def _initialize(path: str) -> None:
    target = Path(path).expanduser().resolve()
    # Exclusive creation prevents accidentally truncating an existing registry.
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        check_registry(str(target))
        return
    os.close(fd)
    with closing(_connect(str(target), write=True)) as connection, connection:
        connection.executescript("""
            CREATE TABLE clients (
                client_id TEXT PRIMARY KEY, application TEXT NOT NULL,
                secret_hash TEXT NOT NULL, revision TEXT NOT NULL,
                enabled INTEGER NOT NULL, grants TEXT NOT NULL
            );
            CREATE TABLE audit (timestamp INTEGER NOT NULL, client_id TEXT NOT NULL, action TEXT NOT NULL);
            PRAGMA user_version = 1;
        """)


def create_client(path: str, application: str, grants: list[dict], *, client_id: str | None = None) -> dict:
    """Register a client; the returned secret is never stored in plaintext."""
    validate_application_name(application)
    grants = normalize_grants(grants)
    client_id = client_id or secrets.token_urlsafe(18)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id):
        raise ValueError("client_id must contain 1–128 letters, digits, underscores or hyphens")
    secret = secrets.token_urlsafe(32)
    _initialize(path)
    with closing(_connect(path, write=True)) as connection, connection:
        connection.execute("INSERT INTO clients VALUES (?, ?, ?, ?, 1, ?)", (
            client_id, application, hashlib.sha256(secret.encode()).hexdigest(),
            secrets.token_urlsafe(18), json.dumps(grants),
        ))
        connection.execute("INSERT INTO audit VALUES (?, ?, 'create')", (int(time.time()), client_id))
    return {"client_id": client_id, "client_secret": secret, "application": application, "grants": grants}


def update_client(path: str, client_id: str, action: str, *, grants: list[dict] | None = None) -> dict:
    """Every administrative change invalidates outstanding client tokens."""
    if action not in {"rotate", "disable", "enable", "set-grants"}:
        raise ValueError("Unknown client action")
    updates = {"revision": secrets.token_urlsafe(18)}
    result = {"client_id": client_id, "action": action}
    if action == "rotate":
        secret = secrets.token_urlsafe(32)
        updates["secret_hash"] = hashlib.sha256(secret.encode()).hexdigest()
        result["client_secret"] = secret
    elif action == "set-grants":
        updates["grants"] = json.dumps(normalize_grants(grants))
    else:
        updates["enabled"] = int(action == "enable")
    with closing(_connect(path, write=True)) as connection, connection:
        changed = connection.execute(
            f"UPDATE clients SET {', '.join(key + ' = ?' for key in updates)} WHERE client_id = ?",
            (*updates.values(), client_id),
        ).rowcount
        if not changed:
            raise ValueError("Unknown API client")
        connection.execute("INSERT INTO audit VALUES (?, ?, ?)", (int(time.time()), client_id, action))
    return result


def _cli_grants(values: list[str]) -> list[dict]:
    # Colon separates the class from nested module folders unambiguously.
    result = []
    for value in values:
        parts = value.split(":")
        if len(parts) not in {2, 3}:
            raise ValueError("Use MODULE:CLASS or MODULE:CLASS:METHOD for --allow")
        grant = {"module": parts[0], "class": parts[1]}
        if len(parts) == 3:
            grant["methods"] = [parts[2]]
        result.append(grant)
    return normalize_grants(result)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, help="Private SQLite file outside served app modules")
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create", help="Print a new client secret once; store it securely")
    create.add_argument("--application", required=True)
    create.add_argument("--client-id")
    create.add_argument("--allow", action="append", required=True, metavar="MODULE:CLASS[:METHOD]")
    for action in ("rotate", "disable", "enable", "set-grants"):
        command = commands.add_parser(action)
        command.add_argument("client_id")
        if action == "set-grants":
            command.add_argument("--allow", action="append", required=True, metavar="MODULE:CLASS[:METHOD]")
    commands.add_parser("list", help="List identities and grants without secret hashes")
    commands.add_parser("audit", help="Show the most recent 100 administrative actions")
    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            result = create_client(args.registry, args.application, _cli_grants(args.allow), client_id=args.client_id)
        elif args.action in {"list", "audit"}:
            with closing(_connect(args.registry)) as connection:
                query = ("SELECT client_id, application, enabled, grants FROM clients ORDER BY client_id"
                         if args.action == "list" else "SELECT * FROM audit ORDER BY rowid DESC LIMIT 100")
                result = [dict(row) for row in connection.execute(query)]
                if args.action == "list":
                    for item in result:
                        item["grants"] = json.loads(item["grants"])
        else:
            result = update_client(args.registry, args.client_id, args.action,
                grants=_cli_grants(args.allow) if args.action == "set-grants" else None)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"API client registry error: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
