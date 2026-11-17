import asyncio
import io
import boto3
from botocore.stub import Stubber
from botocore.response import StreamingBody
import pytest
from pytincture.microapps.object_store import S3ObjectStore, ObjectConflict


def client():
    # Synthetic credentials only: Stubber intercepts every request.
    return boto3.client('s3', region_name='us-east-1', aws_access_key_id='testing', aws_secret_access_key='testing')


def test_s3_conditional_writes_and_bounded_reads():
    sdk = client()
    store = S3ObjectStore('example-bucket', 'dev-123', client=sdk)
    with Stubber(sdk) as stub:
        stub.add_response('put_object', {'ETag': '"one"'}, {'Bucket': 'example-bucket', 'Key': 'dev-123/environment.json', 'Body': b'{}', 'IfNoneMatch': '*'})
        stub.add_response('put_object', {'ETag': '"two"'}, {'Bucket': 'example-bucket', 'Key': 'dev-123/environment.json', 'Body': b'{"revision":1}', 'IfMatch': '"one"'})
        stub.add_response('get_object', {'ETag': '"two"', 'ContentLength': 2, 'Body': StreamingBody(io.BytesIO(b'{}'), 2)}, {'Bucket': 'example-bucket', 'Key': 'dev-123/environment.json'})
        async def exercise():
            assert await store.put('environment.json', b'{}') == '"one"'
            assert await store.put('environment.json', b'{"revision":1}', expected='"one"') == '"two"'
            assert await store.get('environment.json') == (b'{}', '"two"')
        asyncio.run(exercise())
        stub.assert_no_pending_responses()


def test_s3_conflicts_are_not_overwritten():
    sdk = client(); store = S3ObjectStore('example-bucket', client=sdk)
    with Stubber(sdk) as stub:
        stub.add_client_error('put_object', service_error_code='PreconditionFailed', http_status_code=412,
            expected_params={'Bucket': 'example-bucket', 'Key': 'environment.json', 'Body': b'{}', 'IfMatch': '"stale"'})
        with pytest.raises(ObjectConflict): asyncio.run(store.put('environment.json', b'{}', expected='"stale"'))


def test_s3_missing_object_and_oversized_body():
    sdk = client(); store = S3ObjectStore('example-bucket', client=sdk)
    with Stubber(sdk) as stub:
        stub.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404,
                              expected_params={'Bucket': 'example-bucket', 'Key': 'missing'})
        with pytest.raises(FileNotFoundError): asyncio.run(store.get('missing'))
        stub.add_response('get_object', {'ETag': '"one"', 'ContentLength': 10, 'Body': StreamingBody(io.BytesIO(b'1234567890'), 10)},
            {'Bucket': 'example-bucket', 'Key': 'large'})
        with pytest.raises(ValueError, match='limit'): asyncio.run(store.get('large', limit=2))
