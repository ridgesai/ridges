"""
Send deflate logs returns "Request accepted for processing (always 202 empty JSON)." response
"""

import logging
import asyncio
import os
from dotenv import load_dotenv

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.model.content_encoding import ContentEncoding
from datadog_api_client.v2.model.http_log import HTTPLog
from datadog_api_client.v2.model.http_log_item import HTTPLogItem

load_dotenv()

configuration = Configuration()

hostname = os.getenv("DD_HOSTNAME")
service = os.getenv("DD_SERVICE")
env = os.getenv("DD_ENV")

class DatadogLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()

    def emit(self, record):
        try:
            # Check if there's a running event loop
            loop = asyncio.get_running_loop()
            # If we get here, there's a running loop, so we can create a task
            asyncio.create_task(self._async_emit(record))
        except RuntimeError:
            # No running event loop, use synchronous version
            self._sync_emit(record)

    def _sync_emit(self, record):
        provided_process_type = getattr(record, 'process_type', None)
        provided_process_id = getattr(record, 'process_id', None)
        
        # Extract the top-level folder from pathname
        try:
            top_folder = record.pathname.split('ridges/')[1].split('/')[0]
        except:
            top_folder = "unknown"
        
        body = HTTPLog(
            [
                HTTPLogItem(
                    ddsource="ec2",
                    process_type=provided_process_type,
                    process_id=provided_process_id,
                    ddtags=f"pathname:{record.pathname}",
                    hostname=hostname,
                    level=record.levelname,
                    location=f"{record.pathname}:{record.lineno}",
                    function=f"{record.funcName}()",
                    message=record.msg,
                    service=top_folder,
                    date=record.timestamp,
                    env=env
                ),
            ]
        )

        with ApiClient(configuration) as api_client:
            api_instance = LogsApi(api_client)
            try:
                api_instance.submit_log(content_encoding=ContentEncoding.DEFLATE, body=body)
            except Exception as e:
                print(f"Failed to send log to Datadog: {e}")
                print(f"Original log: {body}")

    def emit_async(self, record):
        asyncio.create_task(self._async_emit(record))

    async def _async_emit(self, record):
        provided_process_type = getattr(record, 'process_type', None)
        provided_process_id = getattr(record, 'process_id', None)
        
        # Extract the top-level folder from pathname
        try:
            top_folder = record.pathname.split('ridges/')[1].split('/')[0]
        except:
            top_folder = "unknown"
        
        body = HTTPLog(
            [
                HTTPLogItem(
                    ddsource="ec2",
                    process_type=provided_process_type,
                    process_id=provided_process_id,
                    ddtags=f"pathname:{record.pathname}",
                    hostname=hostname,
                    level=record.levelname,
                    location=f"{record.pathname}:{record.lineno}",
                    function=f"{record.funcName}()",
                    message=record.msg,
                    service=top_folder,
                    date=record.timestamp,
                    env=env
                ),
            ]
        )

        def _send_log():
            with ApiClient(configuration) as api_client:
                api_instance = LogsApi(api_client)
                try:
                    api_instance.submit_log(content_encoding=ContentEncoding.DEFLATE, body=body)
                except Exception as e:
                    print(f"Failed to send log to Datadog: {e}")
                    print(f"Original log: {body}")

        await asyncio.to_thread(_send_log)
