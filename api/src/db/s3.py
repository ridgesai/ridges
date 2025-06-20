import boto3
import os
from typing import BinaryIO
from dotenv import load_dotenv

load_dotenv()

s3_bucket_name = os.getenv('AWS_S3_BUCKET_NAME')

class S3Manager:
    def __init__(self):
        self.s3 = boto3.client('s3')
    
    def upload_file_object(self, file_object: BinaryIO, key: str):
        self.s3.upload_fileobj(file_object, s3_bucket_name, key)

    def get_file_text(self, key: str) -> str:
        response = self.s3.get_object(Bucket=s3_bucket_name, Key=key)
        return response['Body'].read().decode('utf-8')