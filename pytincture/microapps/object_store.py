"""Bounded object access and conditional writes; AWS credentials stay in the SDK."""
from __future__ import annotations
import asyncio
import hashlib
import os
from pathlib import Path
import tempfile
from .manager import _relative


class ObjectConflict(RuntimeError):
    """The object changed, or an immutable key already exists."""


class DirectoryObjectStore:
    """Local equivalent of an S3 prefix, useful for development and acceptance."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks = self.root / '.locks'; self._locks.mkdir(exist_ok=True)

    def _path(self, key):
        path = self.root.joinpath(*_relative(key).parts)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Object keys cannot traverse symlinks')
        return path

    async def get(self, key, *, limit=4 * 1024 * 1024):
        def read():
            with self._path(key).open('rb') as stream:
                data = stream.read(limit + 1)
            if len(data) > limit: raise ValueError('Object exceeds byte limit')
            return data, '"' + hashlib.sha256(data).hexdigest() + '"'
        return await asyncio.to_thread(read)

    async def put(self, key, data, *, expected=None):
        def write():
            import fcntl
            path = self._path(key)
            lock = self._locks / hashlib.sha256(key.encode()).hexdigest()
            with lock.open('a') as lease:
                fcntl.flock(lease, fcntl.LOCK_EX)
                current = '"' + hashlib.sha256(path.read_bytes()).hexdigest() + '"' if path.exists() else None
                if current != expected: raise ObjectConflict(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(dir=path.parent)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(data); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally: Path(temporary).unlink(missing_ok=True)
                return '"' + hashlib.sha256(data).hexdigest() + '"'
        return await asyncio.to_thread(write)


class S3ObjectStore:
    """S3 prefix adapter. Uses task-role/default SDK credentials, never static keys.

    Pass a client for testing or custom SDK configuration. No list-bucket call is
    used: the manifest selects exact keys and uninstalled objects are untouched.
    """
    def __init__(self, bucket, prefix='', *, client=None):
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client('s3', config=Config(signature_version='s3v4',
                connect_timeout=10, read_timeout=30, retries={'max_attempts': 3, 'mode': 'standard'}))
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip('/')
        if self.prefix: _relative(self.prefix)

    def _key(self, key):
        _relative(key)
        return f'{self.prefix}/{key}' if self.prefix else key

    async def get(self, key, *, limit=4 * 1024 * 1024):
        def read():
            try: response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            except Exception as error:
                if getattr(error, 'response', {}).get('Error', {}).get('Code') in {'NoSuchKey', '404'}:
                    raise FileNotFoundError(key) from None
                raise
            body = response['Body']
            try:
                if response.get('ContentLength', 0) > limit: raise ValueError('Object exceeds byte limit')
                data = body.read(limit + 1)
                if len(data) > limit: raise ValueError('Object exceeds byte limit')
                return data, response['ETag']
            finally: body.close()
        return await asyncio.to_thread(read)

    async def put(self, key, data, *, expected=None):
        def write():
            try:
                result = self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data,
                    **({'IfNoneMatch': '*'} if expected is None else {'IfMatch': expected}))
            except Exception as error:
                if getattr(error, 'response', {}).get('Error', {}).get('Code') in {'PreconditionFailed', 'ConditionalRequestConflict', '412', '409'}:
                    raise ObjectConflict(key) from None
                raise
            return result['ETag']
        return await asyncio.to_thread(write)

    def signed_url(self, key, *, expires=300):
        return self.client.generate_presigned_url('get_object',
            Params={'Bucket': self.bucket, 'Key': self._key(key)}, ExpiresIn=expires)
