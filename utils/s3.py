from typing import Optional

import aioboto3
from botocore.config import Config

import utils.logger as logger

S3_CLIENT_CONFIG = Config(signature_version="s3v4")


async def initialize_s3(
    *,
    _endpoint_url: Optional[str] = None,
    _bucket: str,
    region: str,
    access_key_id: str,
    secret_access_key: str,
):
    """Initialize the S3 session.

    Parameters
    ----------
    _bucket : str
        Name of the S3 bucket to use for storage.
    region : str
        AWS region where the S3 bucket is located.
    access_key_id : str
        AWS access key ID for authentication.
    secret_access_key : str
        AWS secret access key for authentication.
    endpoint_url : Optional[str], optional
        The endpoint URL for the S3 service, by default None. This can be used to connect to S3-compatible services or for testing with local S3 emulators.
    """
    logger.info(f"Initializing S3 client for bucket {_bucket} in region {region}...")

    global session, bucket, endpoint_url
    session = aioboto3.Session(
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region,
    )
    bucket = _bucket
    endpoint_url = _endpoint_url

    logger.info(f"S3 client initialized for bucket {_bucket}.")


async def deinitialize_s3():
    logger.info("Deinitializing S3 client...")

    global session, bucket, endpoint_url
    session = None
    bucket = None
    endpoint_url = None

    logger.info("S3 client deinitialized.")


def _s3_client():
    """Generate an S3 client using the previously created session."""

    client_kwargs = {
        "service_name": "s3",
        "config": S3_CLIENT_CONFIG,
    }
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url
    return session.client(
        **client_kwargs,
    )


async def upload_text_file_to_s3(path: str, text: str):
    async with _s3_client() as s3_client:
        logger.info(f"Uploading text file to s3://{bucket}/{path}")
        await s3_client.put_object(Bucket=bucket, Key=path, Body=text.encode("utf-8"))
        logger.info(f"Successfully uploaded text file to s3://{bucket}/{path}")


async def download_text_file_from_s3(path: str) -> str:
    async with _s3_client() as s3_client:
        logger.info(f"Downloading text file from s3://{bucket}/{path}")
        response = await s3_client.get_object(Bucket=bucket, Key=path)
        body = await response["Body"].read()
        content = body.decode("utf-8")
        logger.info(f"Successfully downloaded text file from s3://{bucket}/{path}")
        return content


async def generate_presigned_url(key: str, *, ttl_seconds: int = 300) -> str:
    async with _s3_client() as s3_client:
        url = await s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )
        logger.info(f"Generated presigned URL for s3://{bucket}/{key} (ttl={ttl_seconds}s)")
        return url


async def generate_presigned_upload_url(key: str, *, ttl_seconds: int = 7200) -> str:
    async with _s3_client() as s3_client:
        url = await s3_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )
        logger.info(f"Generated presigned upload URL for s3://{bucket}/{key} (ttl={ttl_seconds}s)")
        return url
