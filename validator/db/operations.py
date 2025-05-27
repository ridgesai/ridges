import json
import sqlite3
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

from validator.utils.logging_utils import get_logger

from validator.challenge.challenge_types import HydratedGeneratedCodegenProblem, CodegenResponse
from .schema import check_db_initialized, init_db

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

    def store_codegen_challenge(self, challenge: HydratedGeneratedCodegenProblem) -> None:
        """Store a new challenge in the database"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO codegen_challenges (
                    challenge_id, created_at, question_text, 
                           relevant_filepair_1_name, relevant_filepair_2_name,
                           dynamic_checklist
                )
                VALUES (?, CURRENT_TIMESTAMP, ?, 
                           ?, ?,
                           ?)
            """, (
                challenge.challenge_id,
                challenge.problem_statement,
                str(challenge.context_files[0].path),
                str(challenge.context_files[1].path),
                json.dumps(challenge.dynamic_checklist)
            ))

            if cursor.rowcount == 0:
                logger.debug(f"Challenge {challenge.challenge_id} already exists in database")
            else:
                logger.info(f"Stored new challenge {challenge.challenge_id} in database")

            conn.commit()

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
    
    def store_response(
        self,
        challenge_id: str,
        miner_hotkey: str,
        response: CodegenResponse,
        node_id: int,
        received_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None
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
                    evaluated_at,
                    response_patch
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            """, (
                challenge_id,
                miner_hotkey,
                node_id,
                received_at,
                completed_at,
                response.evaluated,
                response.score,
                response.response_patch
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
    
    def get_challenge(self, challenge_id: str) -> Optional[Dict]:
        """Get a challenge from the database by ID"""
        conn = self.get_connection()
        with conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.*, ca.sent_at
                FROM codegen_challenges c
                LEFT JOIN challenge_assignments ca ON c.challenge_id = ca.challenge_id
                WHERE c.challenge_id = ?
            """, (challenge_id,))
            row = cursor.fetchone()

            if row:
                return dict(row)
            return None

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
        
    def cleanup_old_data(self, days: int = 7) -> None:
        """
        Remove data older than the specified number of days from various tables.

        Args:
            days: Number of days to keep data for. Default is 7.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Define tables and their timestamp columns
            tables_to_clean = [
                ("responses", "received_at"),
                ("challenge_assignments", "completed_at"),
                ("availability_checks", "checked_at")
            ]

            for table, timestamp_column in tables_to_clean:
                query = f"""
                DELETE FROM {table}
                WHERE {timestamp_column} < datetime('now', '-{days} days')
                """
                cursor.execute(query)
                deleted_rows = cursor.rowcount
                logger.info(f"Deleted {deleted_rows} rows from {table} older than {days} days")

            # Clean up challenges that are no longer referenced
            cursor.execute("""
                DELETE FROM challenges
                WHERE challenge_id NOT IN (
                    SELECT DISTINCT challenge_id FROM responses
                    UNION
                    SELECT DISTINCT challenge_id FROM challenge_assignments
                )
                AND created_at < datetime('now', '-{days} days')
            """)
            deleted_challenges = cursor.rowcount
            logger.info(f"Deleted {deleted_challenges} orphaned challenges older than {days} days")

            conn.commit()
            logger.info(f"Database cleanup completed for data older than {days} days")

        except Exception as e:
            conn.rollback()
            logger.error(f"Error during database cleanup: {str(e)}")
        finally:
            conn.close()
        
    def mark_response_as_evaluated(self, response_id: int) -> None:
        """Mark a response as evaluated"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE responses
                SET evaluated = TRUE, evaluated_at = ?
                WHERE response_id = ?
            """, (datetime.utcnow(), response_id))

            conn.commit()

        finally:
            conn.close()
    
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
            conn.close()
    
    async def get_pending_responses(self, challenge_id: str) -> List[CodegenResponse]:
        """
        Get all unevaluated responses for a challenge.

        Args:
            challenge_id: The challenge ID to get responses for

        Returns:
            List of CodegenResponse objects
        """
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    response_id,
                    challenge_id,
                    miner_hotkey,
                    node_id,
                    response_patch,
                    received_at,
                    completed_at
                FROM responses
                WHERE challenge_id = ?
                  AND evaluated = FALSE
                  AND response_data IS NOT NULL
            """, (challenge_id,))

            rows = cursor.fetchall()
            responses = []

            for row in rows:
                try:
                    response = CodegenResponse(
                        challenge_id=row["challenge_id"],
                        miner_hotkey=row["miner_hotkey"],
                        response_id=row["response_id"],
                        node_id=row["node_id"],
                        response_patch=row["response_patch"]
                    )
                    responses.append(response)
                except Exception as e:
                    logger.error(f"Error processing response {row['response_id']}: {str(e)}")
                    continue

            logger.info(f"Found {len(responses)} pending responses for challenge {challenge_id}")
            return responses

        finally:
            cursor.close()
            conn.close()

    def mark_responses_failed(self, challenge_id):
        """Mark all responses for a codegen challenge as evaluated if the response patch is missing."""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE responses
                SET evaluated = TRUE, evaluated_at = ?
                WHERE challenge_id = ?
            """, (datetime.utcnow(), challenge_id))

            conn.commit()
            logger.info(f" All responses for challenge {challenge_id} marked as evaluated (skipped due to no patch).")

        except Exception as e:
            conn.rollback()  #
            logger.error(f" Error updating responses for challenge {challenge_id}: {str(e)}")

        finally:
            cursor.close()
            conn.close()


    def mark_response_failed(self, response_id: int) -> None:
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
