"""
Send deflate logs returns "Request accepted for processing (always 202 empty JSON)." response
"""

import logging
import asyncio
from datetime import datetime

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.model.content_encoding import ContentEncoding
from datadog_api_client.v2.model.http_log import HTTPLog
from datadog_api_client.v2.model.http_log_item import HTTPLogItem
from datadog_api_client.v2.model.metric_payload import MetricPayload
from datadog_api_client.v2.model.metric_series import MetricSeries
from datadog_api_client.v2.model.metric_point import MetricPoint
from datadog_api_client.v2.api.metrics_api import MetricsApi

from dotenv import load_dotenv

load_dotenv()

configuration = Configuration()

class DatadogLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()

    def emit(self, record):
        asyncio.create_task(self._async_emit(record))

    async def _async_emit(self, record):
        body = HTTPLog(
            [
                HTTPLogItem(
                    ddsource="ec2",
                    ddtags=f"pathname:{record.pathname}",
                    hostname="localhost",
                    level=record.levelname,
                    location=f"{record.pathname}:{record.lineno}",
                    function=f"{record.funcName}()",
                    message=record.msg,
                    service="ridges-platform"
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

async def dd_update_connected_validators(count: int, validator_hotkeys: list[str]):
    try:
        body = MetricPayload(
            series=[
                MetricSeries(
                    metric="connected_validators",
                    points=[
                        MetricPoint(
                            timestamp=int(datetime.now().timestamp()),
                            value=count,
                        ),
                    ],
                ),
            ],
        )
        
        def _send_metric():
            with ApiClient(configuration) as api_client:
                api_instance = MetricsApi(api_client)
                api_instance.submit_metrics(body=body)
        
        await asyncio.to_thread(_send_metric)
    except Exception as e:
        print(f"Failed to send metric to Datadog: {e}")
        print(f"Original metric: {body}")

if __name__ == "__main__":
    asyncio.run(dd_update_connected_validators(2, ["0x1234567890"]))