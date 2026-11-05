"""Manifest-driven startup, durable asynchronous installs and atomic bundle swaps."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import secrets
import time
import zipfile
from .environment_manifest import decode, encode, empty_manifest, selection, selection_id, validate_manifest
from .manager import LiveMicroappManager, WheelArtifact, BrowserArtifact
from .object_store import ObjectConflict
from .snapshot import build_snapshot, verify_wheel, INVENTORY, MAX_BUNDLE


class SupersededBuild(RuntimeError):
    pass


class EnvironmentMicroappManager(LiveMicroappManager):
    """One private local runtime per replica; one S3-fenced builder per environment.

    A single conditional manifest write contains desired state, active pointer and
    lease. Losing or expiring a lease prevents an old builder from activating.
    """
    def __init__(self, store, config, *, objects, environment, manifest_key='environment.json',
                 poll_seconds=5, lease_seconds=60, startup_timeout=300,
                 browser_delivery='host', asset_origins=(), **kwargs):
        super().__init__(store, config, **kwargs)
        empty_manifest(environment)
        if browser_delivery not in {'host', 's3'}: raise ValueError('Invalid browser delivery')
        if poll_seconds <= 0 or lease_seconds < 3: raise ValueError('Invalid reconciliation timing')
        self.objects, self.environment, self.manifest_key = objects, environment, manifest_key
        self.poll_seconds, self.lease_seconds, self.startup_timeout = poll_seconds, lease_seconds, startup_timeout
        from urllib.parse import urlsplit
        for origin in asset_origins:
            parsed = urlsplit(origin)
            if parsed.scheme != 'https' or parsed.netloc != parsed.hostname or parsed.path or parsed.query or parsed.fragment:
                raise ValueError('S3 asset origins must be exact HTTPS host origins')
        if browser_delivery == 's3' and (not asset_origins or not hasattr(objects, 'signed_url')):
            raise ValueError('Direct S3 delivery requires explicit approved asset origins')
        self.asset_origins = tuple(asset_origins)
        self.browser_delivery = browser_delivery
        self._reconcile_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._watcher = None
        self._bundle = None
        self._bundle_file = None
        self._document = None
        self._last_error = None
        self._cache = self.store / 'artifacts'; self._cache.mkdir(exist_ok=True)

    async def read_manifest(self):
        data, etag = await self.objects.get(self.manifest_key)
        return decode(data, self.environment), etag

    async def initialize(self, document):
        """Provision a new environment lockfile, never overwrite an existing one."""
        validate_manifest(document, self.environment)
        await self.objects.put(self.manifest_key, encode(document), expected=None)

    async def start(self):
        async with self._start_lock:
            if self._watcher is not None: return
            await super().start()
            try:
                async with asyncio.timeout(self.startup_timeout):
                    await self.reconcile()
            except BaseException:
                await super().close()
                raise
            self._watcher = asyncio.create_task(self._watch(), name=f'microapps-{self.environment}')

    async def close(self):
        watcher, self._watcher = self._watcher, None
        if watcher:
            watcher.cancel()
            try: await watcher
            except asyncio.CancelledError: pass
        await super().close()

    async def _watch(self):
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
            except TimeoutError: pass
            self._wake.clear()
            try:
                await self.reconcile()
                self._last_error = None
            except asyncio.CancelledError: raise
            except Exception as error:
                self._last_error = type(error).__name__
                # Desired state remains durable in S3 and is retried on the next
                # interval or after a restart. The current bundle is untouched.

    async def _cached(self, artifact, *, bundle=False, browser=False):
        path = self._cache / artifact['sha256'] / Path(artifact['key']).name
        if path.is_file() and await asyncio.to_thread(lambda: hashlib.sha256(path.read_bytes()).hexdigest()) == artifact['sha256']:
            if not bundle: await asyncio.to_thread(verify_wheel, path, artifact, python_version='3.13.2' if browser else None)
            return path
        data, _ = await self.objects.get(artifact['key'], limit=MAX_BUNDLE if bundle else 128 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != artifact['sha256']: raise ValueError('S3 artifact SHA-256 mismatch')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
        await asyncio.to_thread(temporary.write_bytes, data)
        temporary.replace(path)
        if not bundle: await asyncio.to_thread(verify_wheel, path, artifact, python_version='3.13.2' if browser else None)
        return path

    async def _prepare(self, lock):
        releases, browser_paths = {}, {}
        for app_id, app in sorted(lock['apps'].items()):
            backend = await self._cached(app['backend'])
            browser = await self._cached(app['browser']['artifact'], browser=True)
            dependencies = [WheelArtifact(await self._cached(item), item['sha256']) for item in app.get('dependencies', [])]
            release = await self.install(app_id, WheelArtifact(backend, app['backend']['sha256']),
                BrowserArtifact(WheelArtifact(browser, app['browser']['artifact']['sha256']), app['browser']['entrypoint']),
                approval_id=app['approval_id'], dependencies=dependencies, activate=False)
            releases[app_id], browser_paths[app_id] = release, browser
        base = await self._cached(lock['base']['artifact'], browser=True) if lock['base'] else None
        if base:
            application = lock['base']['application']
            with zipfile.ZipFile(base) as archive:
                names = archive.namelist()
            if application + '.py' not in names and application + '/__init__.py' not in names:
                raise ValueError('Base application module is missing from browser wheel')
        return releases, browser_paths, base

    async def _lease_heartbeat(self, token, done):
        while not done.is_set():
            try: await asyncio.wait_for(done.wait(), self.lease_seconds / 3)
            except TimeoutError: pass
            if done.is_set(): return
            for _ in range(20):
                doc, etag = await self.read_manifest()
                if not doc['build'] or doc['build']['token'] != token: return
                if doc['build']['expires_at'] <= time.time(): return
                doc['build']['expires_at'] = time.time() + self.lease_seconds
                try:
                    await self.objects.put(self.manifest_key, encode(doc), expected=etag)
                    break
                except ObjectConflict: continue

    async def _release_lease(self, token, error=None):
        for _ in range(20):
            doc, etag = await self.read_manifest()
            if not doc['build'] or doc['build']['token'] != token: return
            doc['build'] = None
            doc['error'] = {'selection': selection_id(doc), 'type': type(error).__name__} if error else None
            try:
                await self.objects.put(self.manifest_key, encode(doc), expected=etag)
                return
            except ObjectConflict: continue

    async def _publish(self, token, active):
        for _ in range(30):
            doc, etag = await self.read_manifest()
            lease = doc['build']
            if not lease or lease['token'] != token or lease['expires_at'] <= time.time():
                raise SupersededBuild('Builder lost its lease')
            if selection_id(doc) != active['selection']:
                raise SupersededBuild('Installed selection changed during build')
            if doc['active'] and doc['active']['selection'] != active['selection']:
                doc['history'] = ([doc['active']] + doc['history'])[:8]
            doc['active'], doc['build'], doc['error'] = active, None, None
            validate_manifest(doc, self.environment)
            try:
                await self.objects.put(self.manifest_key, encode(doc), expected=etag)
                self._document = doc
                return
            except ObjectConflict: continue
        raise ObjectConflict('Manifest remains busy')

    async def _apply(self, active, prepared=None):
        if self._bundle and self._bundle['selection'] == active['selection']:
            return
        lock = active['inventory']['lock']
        releases, _, _ = prepared or await self._prepare(lock)
        if {key: item['release'] for key, item in releases.items()} != active['inventory']['releases']:
            raise ValueError('Active bundle release inventory disagrees with wheel contents')
        path = await self._cached(active, bundle=True)
        with zipfile.ZipFile(path) as archive:
            inventory = json.loads(archive.read(INVENTORY))
        if inventory['selection'] != active['selection'] or {key: item['release'] for key, item in inventory['apps'].items()} != active['inventory']['releases']:
            raise ValueError('Embedded browser inventory does not match active selection')
        def commit():
            self._bundle_file = path
            self._bundle = copy.deepcopy(active)
        await self.activate_set(active['inventory']['releases'], revision=active['revision'], _commit=commit)

    async def reconcile(self):
        async with self._reconcile_lock:
            while True:
                doc, etag = await self.read_manifest()
                self._document = doc
                wanted = selection_id(doc)
                if doc['active'] and doc['active']['selection'] == wanted:
                    try:
                        await self._apply(doc['active'])
                        return
                    except FileNotFoundError:
                        # Recover an absent aggregate object from immutable wheels.
                        # Hash corruption is never silently replaced.
                        pass
                if doc['build'] and doc['build']['expires_at'] > time.time():
                    await asyncio.sleep(min(self.poll_seconds, 0.5))
                    continue
                token = secrets.token_hex(16)
                doc['build_epoch'] += 1
                doc['build'] = {'token': token, 'selection': wanted, 'epoch': doc['build_epoch'],
                                'expires_at': time.time() + self.lease_seconds}
                try: await self.objects.put(self.manifest_key, encode(doc), expected=etag)
                except ObjectConflict: continue
                done = asyncio.Event()
                heartbeat = asyncio.create_task(self._lease_heartbeat(token, done))
                try:
                    lock = selection(doc)
                    prepared = await self._prepare(lock)
                    snapshot = self._cache / (wanted + '.zip')
                    await asyncio.to_thread(build_snapshot, snapshot, {**lock, 'selection': wanted}, *prepared[:2], base_path=prepared[2])
                    data = await asyncio.to_thread(snapshot.read_bytes)
                    digest = hashlib.sha256(data).hexdigest()
                    key = f'bundles/{self.environment}/{wanted}/{digest}/environment.zip'
                    try: await self.objects.put(key, data, expected=None)
                    except ObjectConflict:
                        existing, _ = await self.objects.get(key, limit=MAX_BUNDLE)
                        if existing != data: raise ValueError('Immutable bundle object changed')
                    active = {'selection': wanted, 'key': key, 'sha256': digest, 'bytes': len(data),
                              'revision': doc['revision'], 'inventory': {'lock': lock,
                              'releases': {key: item['release'] for key, item in prepared[0].items()}}}
                    await self._publish(token, active)
                    await self._apply(active, prepared)
                    return
                except SupersededBuild:
                    await self._release_lease(token)
                except BaseException as error:
                    await self._release_lease(token, error)
                    raise
                finally:
                    done.set(); heartbeat.cancel()
                    try: await heartbeat
                    except (asyncio.CancelledError, Exception): pass

    async def request_install(self, app_id, specification, *, artifacts=None, expected_revision=None):
        """Persist a desired change before acknowledging; watcher performs the build.

        specification must come from your trusted approval callback. Optional
        artifact paths are trusted server inputs, never client-selected paths.
        """
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise ValueError('Expected revision must be a nonnegative integer')
        for _ in range(30):
            doc, etag = await self.read_manifest()
            if expected_revision is not None and doc['revision'] != expected_revision:
                raise ObjectConflict('Environment revision changed')
            changed = copy.deepcopy(doc)
            changed['apps'][app_id] = copy.deepcopy(specification)
            validate_manifest(changed, self.environment)
            if artifacts:
                approved = []
                for item in (specification['backend'], specification['browser']['artifact'], *specification.get('dependencies', [])):
                    approved.append(item)
                by_key = {item['key']: item for item in approved}
                for key, path in artifacts.items():
                    if key not in by_key: raise ValueError('Upload is not in the approved app record')
                    is_browser = key == specification['browser']['artifact']['key']
                    await asyncio.to_thread(verify_wheel, Path(path), by_key[key], python_version='3.13.2' if is_browser else None)
                    data = await asyncio.to_thread(Path(path).read_bytes)
                    try: await self.objects.put(key, data, expected=None)
                    except ObjectConflict:
                        old, _ = await self.objects.get(key, limit=128 * 1024 * 1024)
                        if old != data: raise ValueError('An immutable wheel key already contains different bytes')
            if changed['apps'] == doc['apps']:
                return {'revision': doc['revision'], 'selection': selection_id(doc), 'state': 'accepted'}
            changed['revision'] += 1
            changed['error'] = None
            try: await self.objects.put(self.manifest_key, encode(changed), expected=etag)
            except ObjectConflict: continue
            self._wake.set()
            return {'revision': changed['revision'], 'selection': selection_id(changed), 'state': 'accepted'}
        raise ObjectConflict('Environment manifest remains busy')

    async def ensure_release(self, app_id, release_id):
        """Bring an approved retained BFF version to a newly started replica."""
        if app_id not in self._active:
            return
        document, _ = await self.read_manifest()
        for active in ([document['active']] if document['active'] else []) + document['history']:
            if active['inventory']['releases'].get(app_id) != release_id:
                continue
            lock = copy.deepcopy(active['inventory']['lock'])
            lock['apps'] = {app_id: lock['apps'][app_id]}
            lock['base'] = None
            await self._prepare(lock)
            return

    async def browser_bundle(self, selection_hash):
        """Resolve an immutable, authorized bundle across a concurrent soft swap."""
        candidates = ([self._bundle] if self._bundle else [])
        if self._document:
            candidates += self._document['history']
        for active in candidates:
            if active['selection'] == selection_hash:
                # An old startup catalog may race a swap. Keep serving its
                # immutable bytes while every included app is still installed.
                if not set(active['inventory']['releases']).issubset(self._active):
                    return None
                return active, await self._cached(active, bundle=True)
        return None

    def _asset_url(self, key):
        from urllib.parse import urlsplit
        value = self.objects.signed_url(key)
        parsed = urlsplit(value)
        origin = parsed.scheme + '://' + parsed.netloc
        if origin not in self.asset_origins or parsed.username or parsed.password:
            raise ValueError('Signer returned an unapproved S3 origin')
        return value

    def catalog(self):
        doc = super().catalog()
        doc['environment'] = self.environment
        doc['desired_revision'] = self._document['revision'] if self._document else None
        doc['build_error'] = self._last_error
        if self._bundle:
            active = self._bundle
            url = f'{self.prefix}/bundles/{active["selection"]}/environment.zip'
            if self.browser_delivery == 's3': url = self._asset_url(active['key'])
            doc['snapshot'] = {key: active[key] for key in ('selection', 'sha256', 'bytes')}
            doc['snapshot'].update({'url': url, 'runtime': {'engine': 'pyodide', 'version': '0.29.3'},
                'base': active['inventory']['lock']['base'] and {key: active['inventory']['lock']['base'][key] for key in ('application', 'entrypoint')}})
            if self.browser_delivery == 's3':
                for app in doc['apps']:
                    record = active['inventory']['lock']['apps'][app['id']]['browser']['artifact']
                    app['browser']['url'] = self._asset_url(record['key'])
        return doc
