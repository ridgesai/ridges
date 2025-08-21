import asyncpg
from datetime import datetime

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import ProviderStatistics

@db_operation
async def get_inference_provider_statistics(conn: asyncpg.Connection, start_time: datetime, end_time: datetime) -> list[ProviderStatistics]:
    provider_stats = await conn.fetch(f"""
        SELECT 
            filtered.provider,
            AVG(filtered.time_taken) AS avg_time_taken,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY filtered.time_taken) AS median_time_taken,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY filtered.time_taken) AS p95_time_taken,
            MAX(filtered.time_taken) AS max_time_taken,
            MIN(filtered.time_taken) AS min_time_taken,
            COUNT(*) AS total_requests,
            COUNT(*) FILTER (WHERE filtered.status_code = 200) AS successful_requests,
            COUNT(*) FILTER (WHERE filtered.status_code != 200) AS failed_requests,
            CASE 
                WHEN COUNT(*) > 0 THEN 
                    (COUNT(*) FILTER (WHERE filtered.status_code != 200))::float / COUNT(*)::float * 100
                ELSE 0 
            END AS error_rate,
            SUM(filtered.total_tokens) AS total_tokens
        FROM (
            SELECT 
                provider,
                EXTRACT(EPOCH FROM (finished_at - created_at)) AS time_taken,
                status_code,
                total_tokens
            FROM inferences 
            WHERE created_at >= $1 
            AND created_at <= $2 
            AND finished_at IS NOT NULL
            AND provider IS NOT NULL
        ) AS filtered
        GROUP BY filtered.provider
        ORDER BY filtered.provider
    """, start_time, end_time)
    
    return [ProviderStatistics(**stat) for stat in provider_stats]
