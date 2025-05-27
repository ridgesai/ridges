import streamlit as st
from datetime import datetime
from typing import Optional, List
import sqlite3
import pandas as pd

st.set_page_config(layout="wide")

class ChallengeAssignment:
    def __init__(
        self,
        assignment_id: Optional[int] = None,  # Optional because it's auto-incrementing
        challenge_id: str = None,
        miner_hotkey: str = None,
        node_id: int = None,
        assigned_at: datetime = None,
        sent_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        status: str = 'assigned'  # Default status
    ):
        self.assignment_id = assignment_id
        self.challenge_id = challenge_id
        self.miner_hotkey = miner_hotkey
        self.node_id = node_id
        self.assigned_at = assigned_at or datetime.now()
        self.sent_at = sent_at
        self.completed_at = completed_at
        self.status = status

    def to_dict(self) -> dict:
        """Convert the object to a dictionary for database operations"""
        return {
            'assignment_id': self.assignment_id,
            'challenge_id': self.challenge_id,
            'miner_hotkey': self.miner_hotkey,
            'node_id': self.node_id,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'status': self.status
        }

    @classmethod
    def from_db_row(cls, row: tuple) -> 'ChallengeAssignment':
        """Create a ChallengeAssignment instance from a database row"""
        return cls(
            assignment_id=row[0],
            challenge_id=row[1],
            miner_hotkey=row[2],
            node_id=row[3],
            assigned_at=datetime.fromisoformat(row[4]) if row[4] else None,
            sent_at=datetime.fromisoformat(row[5]) if row[5] else None,
            completed_at=datetime.fromisoformat(row[6]) if row[6] else None,
            status=row[7]
        )

def get_all_challenge_assignments(db_path: str = "../validator.db") -> List[ChallengeAssignment]:
    """
    Read all challenge assignments from the database and return them as a list of ChallengeAssignment objects.
    
    Args:
        db_path (str): Path to the SQLite database file
        
    Returns:
        List[ChallengeAssignment]: List of ChallengeAssignment objects
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM challenge_assignments")
            return [ChallengeAssignment.from_db_row(row) for row in cursor.fetchall()]
    except Exception as e:
        st.error(f"Error reading challenge assignments: {e}")
        return []

# Get all challenge assignments
assignments = get_all_challenge_assignments()
assignments_dict = [assignment.to_dict() for assignment in assignments]

st.subheader('Challenge assignments table')
st.dataframe(
    assignments_dict,
    column_order=['assignment_id', 'challenge_id', 'miner_hotkey', 'node_id', 'assigned_at', 'sent_at', 'completed_at', 'status'],
    hide_index=True
)

# Calculate average completion time per node
completion_times = []
for assignment in assignments:
    if assignment.completed_at and assignment.sent_at:
        completion_time = (assignment.completed_at - assignment.sent_at).total_seconds()
        completion_times.append({
            'NodeID': assignment.node_id,
            'Average completion time (s)': completion_time
        })

completion_times_df = pd.DataFrame(completion_times)

if not completion_times_df.empty:
    avg_completion_times = completion_times_df.groupby('NodeID')['Average completion time (s)'].mean().reset_index()

    # Display average completion time per node in a bar chart
    st.subheader('Average challenge completion time per node')
    st.bar_chart(
        data=avg_completion_times,
        x='NodeID',
        y='Average completion time (s)'
    )
else:
    st.info('No completed challenges found to calculate completion times')

