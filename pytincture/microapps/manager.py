"""Stage approved wheels, run versioned BFF workers, then publish a catalog.

Installation is a trusted Python API, never a browser upload/install endpoint.
Only pure-Python wheels and explicitly supplied dependency wheels are accepted.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import sys
import tempfile
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from pytincture import PytinctureConfig
from pytincture.packaged_apps import _relative, _digest


@dataclass(frozen=True)
class WheelArtifact:
    path: str | Path
    sha256: str


@dataclass(frozen=True)
class BrowserArtifact:
    wheel: WheelArtifact
    entrypoint: str  # package.module:mount; optional unmount(context) in same module
    runtime_version: str = "0.29.3"


def _checked_copy(artifact, destination):
    source = Path(artifact.path)
    if not re.fullmatch(r"[a-f0-9]{64}", artifact.sha256):
        raise ValueError("An approved SHA-256 is required")
    if not source.is_file() or source.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("Artifact is missing or exceeds 128 MiB")
    shutil.copyfile(source, destination)
    if _digest(destination) != artifact.sha256:
        raise ValueError("Artifact does not match approved SHA-256")


def _unpack_wheel(wheel, destination):
    _, _, _, tags = parse_wheel_filename(wheel.name)
    if not all(t.abi == "none" and t.platform == "any" for t in tags):
        raise ValueError("Live installation currently supports only pure-Python wheels")
    total = 0
    with zipfile.ZipFile(wheel) as archive:
        infos = archive.infolist()
        if len(infos) > 10000:
            raise ValueError("Wheel contains too many files")
        for entry in infos:
            name = entry.filename.rstrip("/")
            path = _relative(name)
            if any(p.endswith(".data") for p in path.parts):
                raise ValueError("Wheel .data installation schemes are not supported")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Wheel symlinks are forbidden")
            total += entry.file_size
            if total > 256 * 1024 * 1024:
                raise ValueError("Unpacked wheel exceeds 256 MiB")
            target = destination.joinpath(*path.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                if target.exists():
                    raise ValueError(f"Overlapping wheel files: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def _seal_site(site):
    for path in site.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    site.chmod(0o555)


def _remove_tree(path, *, ignore_errors=False):
    # The controller owns these directories, even when worker source is read-only.
    try:
        if path.exists():
            path.chmod(0o700)
            for directory in path.rglob("*"):
                if directory.is_dir() and not directory.is_symlink():
                    directory.chmod(0o700)
        shutil.rmtree(path)
    except OSError:
        if not ignore_errors:
            raise


def _verify_dependencies(site):
    # The process has the host framework's installed dependencies plus this
    # release's explicit wheels. There is no network dependency resolution.
    installed = {canonicalize_name(d.metadata["Name"]): d.version
                 for d in metadata.distributions()}
    local = list(metadata.distributions(path=[str(site)]))
    for dist in local:
        if canonicalize_name(dist.metadata["Name"]) == "pytincture":
            raise ValueError("Microapp dependencies cannot replace the host framework")
        installed[canonicalize_name(dist.metadata["Name"])] = dist.version
    from pytincture import __version__
    installed["pytincture"] = __version__
    for dist in local:
        for text in dist.requires or ():
            req = Requirement(text)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            if req.url or req.extras:
                raise ValueError("Direct-URL and extra dependencies are not supported by the live worker")
            version = installed.get(canonicalize_name(req.name))
            if version is None or not req.specifier.contains(version):
                raise ValueError(f"Unsatisfied backend dependency: {req.name}")


def _stage_release(stage, app_id, backend, browser, dependencies, approval_id):
    site = stage / "site"; site.mkdir()
    wheels = stage / "wheels"; wheels.mkdir()
    for artifact in (backend, *dependencies):
        path = wheels / Path(artifact.path).name
        _checked_copy(artifact, path)
        _unpack_wheel(path, site)
    _verify_dependencies(site)
    backend_name, backend_version, _, _ = parse_wheel_filename(Path(backend.path).name)
    matches = [metadata.Distribution.at(p) for p in site.glob("*.dist-info")
               if canonicalize_name(metadata.Distribution.at(p).metadata["Name"]) == canonicalize_name(backend_name)]
    if len(matches) != 1:
        raise ValueError("Backend wheel must contain its matching distribution metadata")
    dist = matches[0]
    if Version(dist.version) != backend_version:
        raise ValueError("Backend wheel version metadata mismatch")
    # Validate the existing packaged-app contract without mutating
    # the running host's sys.path or installed distribution table.
    from pytincture.packaged_apps import _load_distribution
    packaged = _load_distribution(dist, expected_version=dist.version)
    if not packaged.application.isascii():
        raise ValueError("Live browser applications require ASCII module names")
    public = stage / "browser"; public.mkdir()
    browser_path = public / Path(browser.wheel.path).name
    _checked_copy(browser.wheel, browser_path)
    check = stage / "browser-check"; check.mkdir()
    _unpack_wheel(browser_path, check)
    if any(d.requires for d in metadata.distributions(path=[str(check)])):
        raise ValueError("Browser wheel dependencies must be resolved into its unique namespace at build time")
    # Browser wheels cannot carry the server-side app contract.
    if list(check.rglob("pytincture-app.json")):
        raise ValueError("Browser artifact contains a backend app manifest")
    module = browser.entrypoint.split(":")[0]
    if not ((check / (module.replace(".", "/") + ".py")).is_file()
            or (check / module.replace(".", "/") / "__init__.py").is_file()):
        raise ValueError("Browser entrypoint is missing from wheel")
    public_names = [p for p in check.iterdir() if not p.name.endswith(".dist-info")]
    if any(not p.stem.isascii() or not p.stem.isidentifier() or p.suffix not in {"", ".py"} for p in public_names):
        raise ValueError("Browser wheel must contain importable Python packages")
    top_modules = sorted(p.stem if p.is_file() else p.name for p in public_names)
    shutil.rmtree(check)
    identity = hashlib.sha256(json.dumps({"backend": backend.sha256,
        "browser": browser.wheel.sha256, "entrypoint": browser.entrypoint,
        "dependencies": [d.sha256 for d in dependencies]}, sort_keys=True).encode()).hexdigest()
    key = f"{app_id}/{identity}"
    release = {"id": app_id, "release": identity, "distribution": dist.metadata["Name"],
               "version": dist.version, "application": packaged.application,
               "approval_id": approval_id, "browser": {"wheel": browser_path.name,
               "sha256": browser.wheel.sha256, "bytes": browser_path.stat().st_size,
               "entrypoint": browser.entrypoint, "runtime": "pyodide",
               "runtime_version": browser.runtime_version, "top_modules": top_modules}}
    _seal_site(site)
    return key, release


class LiveMicroappManager:
    """Single deployment-controller owner of one environment's app catalog.

    Share traffic through this host or an explicit gateway. Independent managers
    across ECS tasks are not a distributed control plane. The store must be
    private, writable only by the service account, and retained across restarts.
    """
    def __init__(self, store, config: PytinctureConfig, *, prefix="/_microapps", readiness_timeout=30, max_releases=32):
        if not re.fullmatch(r"/[A-Za-z0-9_-]+", prefix):
            raise ValueError("prefix must be a single URL path segment")
        if not isinstance(max_releases, int) or not 1 <= max_releases <= 256:
            raise ValueError("max_releases must be between 1 and 256")
        if any((config.enable_user_login, config.enable_google_auth,
                config.enable_microsoft_auth, config.enable_saml_auth)) and not config.session_secret:
            raise ValueError("Live authenticated workers require an explicit shared session_secret")
        self.max_releases = max_releases
        self.store = Path(store).resolve()
        self.store.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config = config
        self.prefix = prefix
        self.readiness_timeout = readiness_timeout
        self._lock = asyncio.Lock()
        self._workers = {}
        self._releases = {}
        self._active = {}
        self._revision = 0
        self._started = False
        self._lease = None

    def _save(self):
        state = {"schema": 1, "revision": self._revision,
                 "active": self._active, "releases": self._releases}
        fd, name = tempfile.mkstemp(dir=self.store, prefix="catalog-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.store / "catalog.json")
        finally:
            Path(name).unlink(missing_ok=True)

    async def start(self):
        async with self._lock:
            if self._started:
                return
            import fcntl
            lease = (self.store / "manager.lock").open("a")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lease.close()
                raise RuntimeError("This microapp store already has a running controller") from None
            self._lease = lease
            try:
                state_path = self.store / "catalog.json"
                if state_path.exists():
                    state = json.loads(state_path.read_text())
                    if state["schema"] != 1:
                        raise ValueError("Invalid live microapp store")
                    self._releases = state["releases"]
                    self._active = state["active"]
                    self._revision = state["revision"]
                    for key, release in self._releases.items():
                        if key != f'{release["id"]}/{release["release"]}' or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}/[a-f0-9]{64}", key):
                            raise ValueError("Invalid stored microapp release")
                        await self._start_worker(key, release)
                self._started = True
            except BaseException:
                await self._stop_workers()
                self._lease.close()
                self._lease = None
                raise

    async def close(self):
        async with self._lock:
            await self._stop_workers()
            self._started = False
            if self._lease:
                self._lease.close()
                self._lease = None

    async def _stop_workers(self):
        for key in list(self._workers):
            await self._stop_worker(key)

    async def _stop_worker(self, key):
        worker = self._workers.pop(key, None)
        if worker:
            process = worker["process"]
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            worker["log"].close()

    async def _start_worker(self, key, release):
        import httpx
        directory = self.store / key
        token = secrets.token_hex(32)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.set_inheritable(True)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        log = (directory / "worker.log").open("ab")
        framework_root = str(Path(__file__).resolve().parents[2])
        # Include the exact host framework environment; per-release paths are
        # added in worker.run(), and cannot replace the framework bootstrap.
        paths = [p for p in sys.path if p and Path(p).is_dir()]
        bootstrap = f"import sys; sys.path[:0] = {([framework_root] + paths)!r}; from pytincture.microapps.worker import run; run()"
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", bootstrap,
                stdin=asyncio.subprocess.PIPE, stdout=log, stderr=log,
                pass_fds=(listener.fileno(),))
            payload = {"site": str(directory / "site"), "config": self.config.to_environ(),
                       "distribution": release["distribution"], "version": release["version"],
                       "token": token, "socket": listener.fileno()}
            process.stdin.write(json.dumps(payload).encode())
            await process.stdin.drain()
            process.stdin.close()
            self._workers[key] = {"process": process, "url": url, "token": token, "log": log}
        except BaseException:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            log.close()
            raise
        finally:
            listener.close()
        try:
            async with asyncio.timeout(self.readiness_timeout):
                async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                    while process.returncode is None:
                        try:
                            headers = {"x-pytincture-worker": token}
                            if self.config.canonical_origin:
                                from urllib.parse import urlsplit
                                origin = urlsplit(self.config.canonical_origin)
                                headers["host"] = origin.netloc
                                headers["x-pytincture-scheme"] = origin.scheme
                            elif self.config.allowed_hosts and self.config.allowed_hosts[0] != "*":
                                headers["host"] = self.config.allowed_hosts[0].replace("*.", "healthcheck.", 1)
                            response = await client.get(url + "/healthz", headers=headers)
                            if response.status_code == 200:
                                return
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.05)
                    raise RuntimeError(f"Microapp worker exited; inspect {directory / 'worker.log'}")
        except BaseException:
            await self._stop_worker(key)
            raise

    async def install(self, app_id, backend: WheelArtifact, browser: BrowserArtifact, *, approval_id,
                      dependencies=()):
        """Install an approved release; call only after your platform authorizes it.

        Sources must already have been downloaded by trusted provisioning (e.g.
        using an ECS task role). There is intentionally no arbitrary URL fetch.
        """
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", app_id):
            raise ValueError("Invalid microapp id")
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("An external approval record is required")
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", browser.entrypoint, flags=re.ASCII):
            raise ValueError("Browser entrypoint must be module:function")
        if browser.runtime_version != "0.29.3":
            raise ValueError("Unsupported browser runtime")
        if backend.sha256 == browser.wheel.sha256:
            raise ValueError("Browser wheel must be separate from the private backend wheel")
        dependencies = tuple(dependencies)
        async with self._lock:
            if not self._started:
                raise RuntimeError("Start the live microapp manager before installation")
            stage = Path(tempfile.mkdtemp(prefix="stage-", dir=self.store))
            key = None
            try:
                staging = asyncio.create_task(asyncio.to_thread(
                    _stage_release, stage, app_id, backend, browser, dependencies, approval_id))
                try:
                    key, release = await asyncio.shield(staging)
                except asyncio.CancelledError:
                    # Let the bounded file operation finish before removing its directory.
                    await staging
                    raise
                if key in self._releases:
                    worker = self._workers.get(key)
                    if not worker or worker["process"].returncode is not None:
                        await self._stop_worker(key)
                        await self._start_worker(key, self._releases[key])
                    self._activate(key)
                    return self._public_release(self._releases[key])
                if len(self._releases) >= self.max_releases:
                    raise ValueError("Microapp release capacity reached; retained versions count toward the limit")
                destination = self.store / key
                destination.parent.mkdir(exist_ok=True)
                if destination.exists():
                    _remove_tree(destination)  # orphan from a failed unpublished installation
                stage.rename(destination)
                await self._start_worker(key, release)
                self._releases[key] = release
                old = self._active.get(app_id)
                self._active[app_id] = key
                self._revision += 1
                try:
                    self._save()
                except BaseException:
                    self._revision -= 1
                    self._releases.pop(key)
                    if old is None:
                        self._active.pop(app_id)
                    else:
                        self._active[app_id] = old
                    await self._stop_worker(key)
                    raise
                return self._public_release(release)
            finally:
                _remove_tree(stage, ignore_errors=True)

    def _activate(self, key):
        app_id = self._releases[key]["id"]
        if self._active.get(app_id) == key:
            return
        old = self._active.get(app_id)
        self._active[app_id] = key
        self._revision += 1
        try:
            self._save()
        except BaseException:
            self._revision -= 1
            if old is None:
                self._active.pop(app_id)
            else:
                self._active[app_id] = old
            raise

    async def activate(self, app_id, release_id):
        """Roll back/select an already installed version without killing old clients."""
        async with self._lock:
            key = f"{app_id}/{release_id}"
            if key not in self._releases or self._public_release(self._releases[key]) is None:
                raise ValueError("Release is not installed and ready")
            self._activate(key)
            return self._public_release(self._releases[key])

    def _public_release(self, release):
        key = f'{release["id"]}/{release["release"]}'
        worker = self._workers.get(key)
        if not worker or worker["process"].returncode is not None:
            return None
        base = f"{self.prefix}/releases/{key}"
        return {"id": release["id"], "release": release["release"],
                "version": release["version"], "application": release["application"],
                "bff_base": base + "/backend/" + release["application"],
                "browser": {**release["browser"], "url": base + "/browser/" + release["browser"]["wheel"]}}

    def catalog(self):
        return {"schema": 1, "revision": self._revision,
                "apps": [public for key in sorted(self._active.values())
                         if (public := self._public_release(self._releases[key])) is not None]}
