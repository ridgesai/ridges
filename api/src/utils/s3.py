import aioboto3
import os
from typing import BinaryIO, List, Dict
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

s3_bucket_name = os.getenv('AWS_S3_BUCKET_NAME')


class S3Manager:
    def __init__(self):
        self.session = aioboto3.Session()

    async def upload_file_object(self, file_object: BinaryIO, key: str):
        async with self.session.client('s3') as s3:
            await s3.upload_fileobj(file_object, s3_bucket_name, key)

    async def get_file_text(self, key: str) -> str:
        async with self.session.client('s3') as s3:
            response = await s3.get_object(Bucket=s3_bucket_name, Key=key)
            body = await response['Body'].read()
            return body.decode('utf-8')

    async def get_file_object(self, key: str) -> BytesIO:
        async with self.session.client('s3') as s3:
            response = await s3.get_object(Bucket=s3_bucket_name, Key=key)
            body = await response['Body'].read()
            return BytesIO(body)

    async def list_objects(self, prefix: str = "", max_keys: int = 100) -> List[Dict]:
        """List objects in the S3 bucket with optional prefix filter"""
        async with self.session.client('s3') as s3:
            response = await s3.list_objects_v2(
                Bucket=s3_bucket_name, 
                Prefix=prefix, 
                MaxKeys=max_keys
            )
            
            if 'Contents' not in response:
                return []
            
            return [
                {
                    'Key': obj['Key'],
                    'Size': obj['Size'], 
                    'LastModified': obj['LastModified']
                }
                for obj in response['Contents']
            ]

    async def check_object_exists(self, key: str) -> bool:
        """Check if an object exists in S3"""
        async with self.session.client('s3') as s3:
            try:
                await s3.head_object(Bucket=s3_bucket_name, Key=key)
                return True
            except s3.exceptions.NoSuchKey:
                return False
            except Exception:
                return False
