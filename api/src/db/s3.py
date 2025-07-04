import aioboto3
import os
from typing import BinaryIO
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
            return (await response['Body'].read()).decode('utf-8')
    
    async def get_file_object(self, key: str) -> BinaryIO:
        async with self.session.client('s3') as s3:
            response = await s3.get_object(Bucket=s3_bucket_name, Key=key)
            return response['Body']
