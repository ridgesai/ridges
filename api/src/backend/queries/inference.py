import asyncpg
from datetime import datetime

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import ProviderStatistics

@db_operation
async def get_inference_provider_statistics(conn: asyncpg.Connection, start_time: datetime, end_time: datetime) -> list[ProviderStatistics]:
    provider_stats = await conn.fetch(f"""
        SELECT 
            provider,
            AVG(EXTRACT(EPOCH FROM (finished_at - created_at))) as avg_time_taken,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finished_at - created_at))) as median_time_taken,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finished_at - created_at))) as p95_time_taken,
            MAX(EXTRACT(EPOCH FROM (finished_at - created_at))) as max_time_taken,
            MIN(EXTRACT(EPOCH FROM (finished_at - created_at))) as min_time_taken,
            COUNT(*) as total_requests,
            COUNT(CASE WHEN status_code = 200 THEN 1 END) as successful_requests,
            COUNT(CASE WHEN status_code != 200 THEN 1 END) as failed_requests,
            CASE 
                WHEN COUNT(*) > 0 THEN 
                    (COUNT(CASE WHEN status_code != 200 THEN 1 END)::float / COUNT(*)::float) * 100
                ELSE 0 
            END as error_rate,
            SUM(total_tokens) as total_tokens
        FROM inferences 
        WHERE created_at >= $1 
        AND created_at <= $2 
        AND finished_at IS NOT NULL
        AND provider IS NOT NULL
        GROUP BY provider
        ORDER BY provider
    """, start_time, end_time)
    
    return [ProviderStatistics(**stat) for stat in provider_stats]
