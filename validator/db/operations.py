import json
import sqlite3
from typing import Dict, List, Optional, Tuple, Any, TYPE_CHECKING
from datetime import datetime
from pathlib import Path

from shared.logging_utils import get_logger

from validator.config import VALIDATION_DELAY
from .schema import check_db_initialized, init_db

if TYPE_CHECKING:
    from validator.challenge.base import BaseChallenge

logger = get_logger(__name__)


class DatabaseManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

        # Initialize database if needed
        if not check_db_initialized(str(db_path)):
            logger.info(f"Initializing new database at {db_path}")
            init_db(str(db_path))
        
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        logger.info(f"Connected to database at {db_path}")

    def close(self):
        if self.conn:
            self.conn.close()

    def get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)
    
    # =============================================================================
    # GENERIC CHALLENGE OPERATIONS
    # =============================================================================
    
    def store_challenge(self, challenge_id: str, challenge_type: str, challenge_data: Dict[str, Any]) -> None:
        """Store a challenge in the database"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # First insert into the parent challenges table
            cursor.execute("""
                INSERT OR IGNORE INTO challenges (
                    challenge_id, challenge_type, created_at
                )
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (challenge_id, challenge_type))

            # Then insert type-specific data
            if challenge_type == 'codegen':
                cursor.execute("""
                    INSERT OR IGNORE INTO codegen_challenges (
                        challenge_id, problem_statement, dynamic_checklist, repository_url, commit_hash, context_file_paths
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    challenge_id,
                    challenge_data['problem_statement'],
                    json.dumps(challenge_data['dynamic_checklist']),
                    challenge_data['repository_url'],
                    challenge_data['commit_hash'],
                    json.dumps(challenge_data['context_file_paths'])
                ))
            elif challenge_type == 'regression':
                cursor.execute("""
                    INSERT OR IGNORE INTO regression_challenges (
                        challenge_id, problem_statement, repository_url, commit_hash, context_file_paths
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    challenge_id,
                    challenge_data['problem_statement'],
                    challenge_data['repository_url'],
                    challenge_data['commit_hash'],
                    json.dumps(challenge_data['context_file_paths'])
                ))

            if cursor.rowcount == 0:
                logger.debug(f"Challenge {challenge_id} already exists in database")
            else:
                logger.info(f"Stored new {challenge_type} challenge {challenge_id} in database")

            conn.commit()
        except Exception as e:
            logger.error(f"Error storing {challenge_id} in database: {e}")
        finally:
            conn.close()

    def get_challenge_data(self, challenge_id: str, challenge_type: str) -> Optional[Dict[str, Any]]:
        """Get challenge data from the database"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            if challenge_type == 'codegen':
                cursor.execute("""
                    SELECT c.challenge_id, c.created_at,
                           cc.problem_statement, cc.dynamic_checklist, cc.repository_url, cc.commit_hash, cc.context_file_paths
                    FROM challenges c
                    JOIN codegen_challenges cc ON c.challenge_id = cc.challenge_id
                    WHERE c.challenge_id = ? AND c.challenge_type = 'codegen'
                """, (challenge_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return {
                    'challenge_id': row[0],
                    'created_at': row[1],
                    'problem_statement': row[2],
                    'dynamic_checklist': json.loads(row[3]),
                    'repository_url': row[4],
                    'commit_hash': row[5],
                    'context_file_paths': json.loads(row[6])
                }
            
            elif challenge_type == 'regression':
                cursor.execute("""
                    SELECT c.challenge_id, c.created_at,
                           rc.problem_statement, rc.repository_url, rc.commit_hash, rc.context_file_paths
                    FROM challenges c
                    JOIN regression_challenges rc ON c.challenge_id = rc.challenge_id
                    WHERE c.challenge_id = ? AND c.challenge_type = 'regression'
                """, (challenge_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return {
                    'challenge_id': row[0],
                    'created_at': row[1],
                    'problem_statement': row[2],
                    'repository_url': row[3],
                    'commit_hash': row[4],
                    'context_file_paths': json.loads(row[5])
                }
            
            return None

        finally:
            conn.close()
        
    def assign_challenge(self, challenge_id: str, miner_hotkey: str, node_id: int) -> None:
        """Assign a challenge to a miner"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO challenge_assignments (
                    challenge_id,
                    miner_hotkey,
                    node_id,
                    status
                )
                VALUES (?, ?, ?, 'assigned')
            """, (
                challenge_id,
                miner_hotkey,
                node_id
            ))

            conn.commit()

        finally:
            conn.close()

    def find_challenge_ready_for_evaluation(self):
        """Finds a challenge where all responses are pending, and ready to be evaluated"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                        SELECT DISTINCT c.challenge_id, c.challenge_type,
                                COUNT(r.response_id) as pending_count,
                                MIN(r.received_at) as earliest_received
                        FROM responses r
                        JOIN challenges c ON r.challenge_id = c.challenge_id
                        WHERE r.evaluated = FALSE
                            AND datetime(r.received_at) <= datetime('now', '-' || ? || ' minutes')
                        GROUP BY c.challenge_id
                        LIMIT 1
                    """, (VALIDATION_DELAY.total_seconds() / 60,))
                    
            row = cursor.fetchone()

            return row if row and row[0] else None
        finally:
            conn.close()
        
    def mark_challenge_sent(self, challenge_id: str, miner_hotkey: str) -> None:
        """Mark a challenge as sent to a miner"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE challenge_assignments
                SET status = 'sent', sent_at = CURRENT_TIMESTAMP
                WHERE challenge_id = ? AND miner_hotkey = ?
            """, (challenge_id, miner_hotkey))

            conn.commit()

        finally:
            conn.close()

    def mark_challenge_failed(self, challenge_id: str, miner_hotkey: str) -> None:
        """Mark a challenge as failed for a miner"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE challenge_assignments
                SET status = 'failed'
                WHERE challenge_id = ? AND miner_hotkey = ?
            """, (challenge_id, miner_hotkey))

            conn.commit()

        finally:
            conn.close()
    
    # =============================================================================
    # GENERIC RESPONSE OPERATIONS
    # =============================================================================
    
    def store_response(
        self,
        challenge_id: str,
        miner_hotkey: str,
        node_id: int,
        response_patch: Optional[str],
        received_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        evaluated: bool = False,
        score: Optional[float] = None
    ) -> int:
        """Store a miner's response to a challenge"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.utcnow()

            # Store response
            cursor.execute("""
                INSERT INTO responses (
                    challenge_id,
                    miner_hotkey,
                    node_id,
                    response_patch,
                    received_at,
                    completed_at,
                    evaluated,
                    score,
                    evaluated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                challenge_id,
                miner_hotkey,
                node_id,
                response_patch,
                received_at,
                completed_at,
                evaluated,
                score
            ))

            response_id = cursor.lastrowid

            # Mark challenge as completed in challenge_assignments
            cursor.execute("""
                UPDATE challenge_assignments
                SET status = 'completed',
                    completed_at = ?
                WHERE challenge_id = ? AND miner_hotkey = ?
            """, (now, challenge_id, miner_hotkey))

            conn.commit()
            return response_id

        finally:
            conn.close()

    def get_pending_responses(self, challenge_id: str):
        """Get all pending responses for a challenge, returning appropriate response objects."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Get responses and challenge type in a single query
            cursor.execute("""
                SELECT 
                    r.response_id,
                    r.challenge_id,
                    r.miner_hotkey,
                    r.node_id,
                    r.response_patch,
                    r.received_at,
                    r.completed_at,
                    c.challenge_type
                FROM responses r
                JOIN challenges c ON r.challenge_id = c.challenge_id
                WHERE r.challenge_id = ?
                  AND r.evaluated = FALSE
                  AND r.response_patch IS NOT NULL
            """, (str(challenge_id),))
            
            rows = cursor.fetchall()
            if not rows:
                logger.info(f"No pending responses found for challenge {challenge_id}")
                return []
            
            challenge_type = rows[0]["challenge_type"]
            responses = []
            
            # Create appropriate response objects based on challenge type
            if challenge_type == "codegen":
                from validator.challenge.codegen.response import CodegenResponse
                for row in rows:
                    try:
                        response = CodegenResponse.from_dict(dict(row))
                        responses.append(response)
                    except Exception as e:
                        logger.error(f"Error processing codegen response {row['response_id']}: {str(e)}")
                        continue
                        
            elif challenge_type == "regression":
                from validator.challenge.regression.response import RegressionResponse
                for row in rows:
                    try:
                        response = RegressionResponse.from_dict(dict(row))
                        responses.append(response)
                    except Exception as e:
                        logger.error(f"Error processing regression response {row['response_id']}: {str(e)}")
                        continue
            else:
                logger.error(f"Unknown challenge type: {challenge_type}")
                return []
            
            logger.info(f"Found {len(responses)} pending responses for challenge {challenge_id}")
            return responses

        finally:
            cursor.close()
            conn.close()

    def update_response(
        self,
        response_id: int,
        score: float,
        evaluated: bool,
        evaluated_at: datetime
    ) -> None:
        """Update a response with evaluation results"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE responses
                SET score = ?, evaluated = ?, evaluated_at = ?
                WHERE response_id = ?
            """, (score, evaluated, evaluated_at, response_id))

            conn.commit()
            logger.info(f"Updated response {response_id} with score {score}")

        except Exception as e:
            logger.error(f"Error updating response {response_id}: {str(e)}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    def mark_responses_failed(self, challenge_id):
        """Mark all responses for a challenge as evaluated if the response patch is missing."""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE responses
                SET evaluated = TRUE, evaluated_at = ?
                WHERE challenge_id = ?
            """, (datetime.utcnow(), challenge_id))

            conn.commit()
            logger.info(f"All responses for challenge {challenge_id} marked as evaluated (skipped due to no patch).")

        except Exception as e:
            conn.rollback()
            logger.error(f"Error updating responses for challenge {challenge_id}: {str(e)}")

        finally:
            cursor.close()
            conn.close()

    def mark_response_failed(self, response_id: str) -> None:
        """Mark a single response as evaluated (used when evaluation failed)."""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE responses
                SET evaluated = TRUE, evaluated_at = ?
                WHERE response_id = ?
            """, (datetime.utcnow(), response_id))

            conn.commit()
            logger.info(f"Response {response_id} marked as evaluated.")

        except Exception as e:
            conn.rollback()
            logger.error(f"Error marking response {response_id} as failed: {str(e)}")

        finally:
            cursor.close()
            conn.close()

    # =============================================================================
    # UTILITY OPERATIONS
    # =============================================================================

    def log_availability_check(
        self,
        node_id: int,
        hotkey: str,
        is_available: bool,
        response_time_ms: float,
        error: Optional[str] = None
    ) -> None:
        """Log an availability check for a miner with enhanced error handling."""
        try:
            query = """
            INSERT INTO availability_checks
            (node_id, hotkey, checked_at, is_available, response_time_ms, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """
            with self.conn:
                self.conn.execute(
                    query,
                    (node_id, hotkey, datetime.utcnow(), is_available, response_time_ms, error)
                )

            # Log the result
            status = "available" if is_available else "unavailable"
            error_msg = f" (Error: {error})" if error else ""
            logger.info(f"Node {node_id} ({hotkey}) is {status} - Response time: {response_time_ms:.2f}ms{error_msg}")

        except Exception as e:
            logger.error(f"Failed to log availability check for node {node_id}: {str(e)}")
            # Don't raise the exception - we don't want availability logging to break the main flow

    def get_challenge_assignment_sent_at(self, challenge_id: str, miner_hotkey: str) -> Optional[datetime]:
        """Get the sent_at timestamp for a challenge assignment"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT sent_at
                FROM challenge_assignments
                WHERE challenge_id = ? AND miner_hotkey = ?
                AND status IN ('sent', 'completed')
            """, (str(challenge_id), miner_hotkey))
            row = cursor.fetchone()
            return datetime.fromisoformat(row[0]) if row and row[0] else None
        finally:
            cursor.close()
            conn.close()

    def get_global_miner_scores(self, hours: int = 24, challenge_type: Optional[str] = None) -> Tuple[float, int]:
        """Gets the average score for all miners and average number of responses for each miner over the last n hours"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            if challenge_type:
                cursor.execute("""
                    SELECT 
                        AVG(r.score) as global_avg_score,
                        COUNT(*) / COUNT(DISTINCT r.miner_hotkey) as avg_responses_per_miner
                    FROM responses r
                    JOIN challenges c ON r.challenge_id = c.challenge_id
                    WHERE r.evaluated = TRUE 
                    AND c.challenge_type = ?
                    AND r.evaluated_at > datetime('now',  '-' || ? || ' hours')
                """, (challenge_type, hours))
            else:
                cursor.execute("""
                    SELECT 
                        AVG(score) as global_avg_score,
                        COUNT(*) / COUNT(DISTINCT miner_hotkey) as avg_responses_per_miner
                    FROM responses 
                    WHERE evaluated = TRUE 
                    AND evaluated_at > datetime('now',  '-' || ? || ' hours')
                """, (hours,))

            global_average, average_count = cursor.fetchone()
            return global_average, average_count

        finally:
            cursor.close()
            conn.close()
        
    def get_bayesian_miner_score(
        self,
        global_average: float,
        average_count: int,
        hours: int = 24,
        challenge_type: Optional[str] = None
    ): 
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            if challenge_type:
                cursor.execute("""
                    SELECT 
                        r.miner_hotkey,
                        COUNT(*) as response_count,
                        AVG(r.score) as avg_score,
                        (COUNT(*) * AVG(r.score) + ? * ?) / (COUNT(*) + ?) as bayesian_avg
                    FROM responses r
                    JOIN challenges c ON r.challenge_id = c.challenge_id
                    WHERE r.evaluated = TRUE 
                    AND c.challenge_type = ?
                    AND r.evaluated_at > datetime('now', '-' || ? || ' hours')
                    GROUP BY r.miner_hotkey       
                """, (average_count, global_average, average_count, challenge_type, hours))
            else:
                cursor.execute("""
                    SELECT 
                        miner_hotkey,
                        COUNT(*) as response_count,
                        AVG(score) as avg_score,
                        (COUNT(*) * AVG(score) + ? * ?) / (COUNT(*) + ?) as bayesian_avg
                    FROM responses
                    WHERE evaluated = TRUE 
                    AND evaluated_at > datetime('now', '-' || ? || ' hours')
                    GROUP BY miner_hotkey       
                """, (average_count, global_average, average_count, hours))

            results = cursor.fetchall()
            return results
        finally:
            cursor.close()
            conn.close()
        
    def cleanup_old_data(self, days: int = 7) -> None:
        """
        Remove data older than the specified number of days from various tables.
        Updated to work with unified schema.

        Args:
            days: Number of days to keep data for. Default is 7.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Define tables and their timestamp columns for unified schema
            tables_to_clean = [
                ("responses", "received_at"),
                ("challenge_assignments", "completed_at"),
                ("availability_checks", "checked_at"),
            ]

            for table, timestamp_column in tables_to_clean:
                query = f"""
                DELETE FROM {table}
                WHERE {timestamp_column} < datetime('now', '-{days} days')
                """
                cursor.execute(query)
                deleted_rows = cursor.rowcount
                logger.info(f"Deleted {deleted_rows} rows from {table} older than {days} days")

            # Clean up codegen challenges that are no longer referenced
            cursor.execute(f"""
                DELETE FROM codegen_challenges
                WHERE challenge_id NOT IN (
                    SELECT DISTINCT challenge_id FROM responses
                    UNION
                    SELECT DISTINCT challenge_id FROM challenge_assignments
                )
                AND challenge_id IN (
                    SELECT challenge_id FROM challenges 
                    WHERE created_at < datetime('now', '-{days} days')
                )
            """)
            deleted_codegen_challenges = cursor.rowcount
            logger.info(f"Deleted {deleted_codegen_challenges} orphaned codegen challenges older than {days} days")

            # Clean up regression challenges that are no longer referenced
            cursor.execute(f"""
                DELETE FROM regression_challenges
                WHERE challenge_id NOT IN (
                    SELECT DISTINCT challenge_id FROM responses
                    UNION
                    SELECT DISTINCT challenge_id FROM challenge_assignments
                )
                AND challenge_id IN (
                    SELECT challenge_id FROM challenges 
                    WHERE created_at < datetime('now', '-{days} days')
                )
            """)
            deleted_regression_challenges = cursor.rowcount
            logger.info(f"Deleted {deleted_regression_challenges} orphaned regression challenges older than {days} days")

            # Clean up parent challenges that are no longer referenced
            cursor.execute(f"""
                DELETE FROM challenges
                WHERE challenge_id NOT IN (
                    SELECT DISTINCT challenge_id FROM responses
                    UNION
                    SELECT DISTINCT challenge_id FROM challenge_assignments
                )
                AND created_at < datetime('now', '-{days} days')
            """)
            deleted_parent_challenges = cursor.rowcount
            logger.info(f"Deleted {deleted_parent_challenges} orphaned parent challenges older than {days} days")

            conn.commit()
            logger.info(f"Database cleanup completed for data older than {days} days")

        except Exception as e:
            conn.rollback()
            logger.error(f"Error during database cleanup: {str(e)}")
        finally:
            cursor.close()
            conn.close()

    def get_challenge(self, challenge_id: str) -> Optional['BaseChallenge']:
        """Get a challenge object by ID, determining the appropriate type."""
        # First determine the challenge type
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT challenge_type 
                FROM challenges 
                WHERE challenge_id = ?
            """, (challenge_id,))
            
            row = cursor.fetchone()
            if not row:
                logger.error(f"Challenge {challenge_id} not found")
                return None
            
            challenge_type = row[0]
            
        finally:
            cursor.close()
            conn.close()
        
        # Get the appropriate challenge object using lazy imports
        if challenge_type == "codegen":
            from validator.challenge.codegen.challenge import CodegenChallenge
            return CodegenChallenge.get_from_database(self, challenge_id)
        elif challenge_type == "regression":
            from validator.challenge.regression.challenge import RegressionChallenge
            return RegressionChallenge.get_from_database(self, challenge_id)
        else:
            logger.error(f"Unknown challenge type: {challenge_type}")
            return None