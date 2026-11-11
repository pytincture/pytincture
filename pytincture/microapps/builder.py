"""Publish an environment browser bundle before any web backend starts."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import math
from pathlib import Path
import tempfile

from pytincture import PytinctureConfig
from .environment import EnvironmentMicroappManager
from .object_store import DirectoryObjectStore, S3ObjectStore


async def build_environment_bundle(objects, *, environment, manifest_key="environment.json",
                                   cache_directory=None, timeout=300, poll_seconds=1,
                                   lease_seconds=60):
    """Build once or verify/reuse the matching S3 bundle, without BFF processes.

    The existing manifest selects approved wheels. This function does not create
    environments, approve apps, import their code, or run their health checks.
    Use the same framework/Python/dependency environment as the web deployment.
    Web replicas verify and start their own BFF workers before becoming ready.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Build timeout must be a positive finite number")

    async def build(directory):
        manager = EnvironmentMicroappManager(directory, PytinctureConfig(),
            objects=objects, environment=environment, manifest_key=manifest_key,
            poll_seconds=poll_seconds, lease_seconds=lease_seconds)
        # The cache is private to this controller, just like a web replica's
        # runtime store. Never race another process using the same directory.
        with (manager.store / "manager.lock").open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("This microapp store already has a running controller") from None
            async with asyncio.timeout(timeout):
                active = await manager._reconcile(build_only=True)
        return {"state": "ready", "environment": environment,
                **{key: active[key] for key in ("selection", "revision", "key", "sha256", "bytes")}}

    if cache_directory is not None:
        return await build(Path(cache_directory))
    with tempfile.TemporaryDirectory(prefix="pytincture-environment-build-") as directory:
        return await build(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bucket", help="Private S3 artifact bucket")
    source.add_argument("--objects-directory", type=Path, help="Local object store for development")
    parser.add_argument("--prefix", default="", help="S3 environment prefix")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--manifest-key", default="environment.json")
    parser.add_argument("--cache-directory", type=Path)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--poll-seconds", type=float, default=1)
    parser.add_argument("--lease-seconds", type=float, default=60)
    args = parser.parse_args()
    if args.objects_directory is not None and args.prefix:
        parser.error("--prefix is only supported with --bucket")
    objects = S3ObjectStore(args.bucket, args.prefix) if args.bucket else DirectoryObjectStore(args.objects_directory)
    result = asyncio.run(build_environment_bundle(objects, environment=args.environment,
        manifest_key=args.manifest_key, cache_directory=args.cache_directory, timeout=args.timeout,
        poll_seconds=args.poll_seconds, lease_seconds=args.lease_seconds))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
