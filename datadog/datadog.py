"""
Send deflate logs returns "Request accepted for processing (always 202 empty JSON)." response
"""

import logging

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.model.content_encoding import ContentEncoding
from datadog_api_client.v2.model.http_log import HTTPLog
from datadog_api_client.v2.model.http_log_item import HTTPLogItem

from dotenv import load_dotenv

load_dotenv()

configuration = Configuration()

class DatadogLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()

    def emit(self, record):
        body = HTTPLog(
            [
                HTTPLogItem(
                    ddsource="ec2",
                    ddtags=f"pathname:{record.pathname}",
                    hostname="ridges.platform.ai",
                    level=record.levelname,
                    location=f"{record.pathname}:{record.lineno}",
                    function=f"{record.funcName}()",
                    message=record.msg,
                    service="ridges-platform"
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
