import aioboto3
from botocore.config import Config

import utils.logger as logger

S3_CLIENT_CONFIG = Config(signature_version="s3v4")


async def initialize_s3(*, _bucket: str, region: str, access_key_id: str, secret_access_key: str):
    logger.info(f"Initializing S3 client for bucket {_bucket} in region {region}...")

    global session, bucket
    session = aioboto3.Session(
        aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key, region_name=region
    )
    bucket = _bucket

    logger.info(f"S3 client initialized for bucket {_bucket}.")


async def deinitialize_s3():
    logger.info("Deinitializing S3 client...")

    global session, bucket
    session = None
    bucket = None

    logger.info("S3 client deinitialized.")


async def upload_text_file_to_s3(path: str, text: str):
    global session, bucket

    async with session.client("s3", config=S3_CLIENT_CONFIG) as s3_client:
        logger.info(f"Uploading text file to s3://{bucket}/{path}")
        await s3_client.put_object(Bucket=bucket, Key=path, Body=text.encode("utf-8"))
        logger.info(f"Successfully uploaded text file to s3://{bucket}/{path}")


async def download_text_file_from_s3(path: str) -> str:
    global session, bucket

    async with session.client("s3", config=S3_CLIENT_CONFIG) as s3_client:
        logger.info(f"Downloading text file from s3://{bucket}/{path}")
        response = await s3_client.get_object(Bucket=bucket, Key=path)
        body = await response["Body"].read()
        content = body.decode("utf-8")
        logger.info(f"Successfully downloaded text file from s3://{bucket}/{path}")
        return content


async def generate_presigned_url(key: str, *, ttl_seconds: int = 300) -> str:
    global session, bucket

    async with session.client("s3", config=S3_CLIENT_CONFIG) as s3_client:
        url = await s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )
        logger.info(f"Generated presigned URL for s3://{bucket}/{key} (ttl={ttl_seconds}s)")
        return url


async def generate_presigned_upload_url(key: str, *, ttl_seconds: int = 7200) -> str:
    global session, bucket

    async with session.client("s3", config=S3_CLIENT_CONFIG) as s3_client:
        url = await s3_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )
        logger.info(f"Generated presigned upload URL for s3://{bucket}/{key} (ttl={ttl_seconds}s)")
        return url
