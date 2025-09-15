"""
Send deflate logs returns "Request accepted for processing (always 202 empty JSON)." response
"""

import logging
import asyncio
import os
import time
from collections import deque
from threading import Lock
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

source = "ec2" if env == "prod" else "local"

BATCH_SIZE = 50
BATCH_TIMEOUT = 5.0

_top_folder_cache = {}

def _get_top_folder(pathname):
    if pathname in _top_folder_cache:
        return _top_folder_cache[pathname]
    
    try:
        top_folder = pathname.split('ridges/')[1].split('/')[0]
    except:
        top_folder = "unknown"
    
    _top_folder_cache[pathname] = top_folder
    return top_folder

class DatadogLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self._log_queue = deque()
        self._lock = Lock()
        self._last_send_time = time.time()
        self._batch_task = None
        self._api_client = None
        self._api_instance = None
        self._flush_task = None
        
    def _start_flush_task(self):
        if self._flush_task is None:
            try:
                loop = asyncio.get_running_loop()
                self._flush_task = loop.create_task(self._periodic_flush())
            except RuntimeError:
                pass
                
    async def _periodic_flush(self):
        """Periodically flush logs to ensure they're sent"""
        while True:
            await asyncio.sleep(BATCH_TIMEOUT)
            await self._send_batch_async()

    def _get_api_client(self):
        """Get or create API client"""
        if self._api_client is None:
            self._api_client = ApiClient(configuration)
            self._api_instance = LogsApi(self._api_client)
        return self._api_instance

    def emit(self, record):
        try:
            loop = asyncio.get_running_loop()
            self._start_flush_task()
            asyncio.create_task(self._async_emit(record))
        except RuntimeError:
            self._sync_emit(record)

    def _sync_emit(self, record):
        self._add_to_batch(record)
        self._send_batch_sync()

    def emit_async(self, record):
        asyncio.create_task(self._async_emit(record))

    def _add_to_batch(self, record):
        with self._lock:
            self._log_queue.append(record)
            
    def _send_batch_sync(self):
        with self._lock:
            if not self._log_queue:
                return
                
            batch = list(self._log_queue)
            self._log_queue.clear()
            
        if batch:
            self._send_logs_sync(batch)

    def _send_logs_sync(self, records):
        log_items = []
        
        for record in records:
            provided_process_type = getattr(record, 'process_type', None)
            provided_process_id = getattr(record, 'process_id', None)
            
            top_folder = _get_top_folder(record.pathname)
            
            log_items.append(
                HTTPLogItem(
                    ddsource=source,
                    process_type=provided_process_type,
                    process_id=provided_process_id,
                    ddtags=f"env:{env}",
                    hostname=hostname,
                    level=record.levelname,
                    location=f"{record.pathname}:{record.lineno}",
                    function=f"{record.funcName}()",
                    message=record.msg,
                    service=service,
                    resource=top_folder,
                    pathname=record.pathname,
                    date=record.timestamp,
                )
            )
        
        body = HTTPLog(log_items)
        
        try:
            api_instance = self._get_api_client()
            api_instance.submit_log(content_encoding=ContentEncoding.DEFLATE, body=body)
        except Exception as e:
            print(f"Failed to send batch of {len(records)} logs to Datadog: {e}")
            self._api_client = None
            self._api_instance = None

    async def _async_emit(self, record):
        self._add_to_batch(record)
        
        should_send = False
        with self._lock:
            if len(self._log_queue) >= BATCH_SIZE:
                should_send = True
            elif time.time() - self._last_send_time >= BATCH_TIMEOUT:
                should_send = True
                
        if should_send:
            await self._send_batch_async()
            
    async def _send_batch_async(self):
        with self._lock:
            if not self._log_queue:
                return
                
            batch = list(self._log_queue)
            self._log_queue.clear()
            self._last_send_time = time.time()
            
        if batch:
            await asyncio.to_thread(self._send_logs_sync, batch)
            
    def close(self):
        """Close the handler and flush any remaining logs"""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            
        self._send_batch_sync()
        
        if self._api_client:
            self._api_client.close()
            self._api_client = None
            self._api_instance = None
            
        super().close()
