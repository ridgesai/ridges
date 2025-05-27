import streamlit as st
from datetime import datetime
from typing import Optional, List
import sqlite3
import pandas as pd

st.set_page_config(layout="wide")

class Response:
    def __init__(
        self,
        response_id: Optional[int] = None,  # Optional because it's auto-incrementing
        challenge_id: str = None,
        miner_hotkey: str = None,
        node_id: Optional[int] = None,
        processing_time: Optional[float] = None,
        received_at: datetime = None,
        completed_at: Optional[datetime] = None,
        evaluated: bool = False,
        score: Optional[float] = None,
        evaluated_at: Optional[datetime] = None,
        response_patch: Optional[str] = None
    ):
        self.response_id = response_id
        self.challenge_id = challenge_id
        self.miner_hotkey = miner_hotkey
        self.node_id = node_id
        self.processing_time = processing_time
        self.received_at = received_at or datetime.now()
        self.completed_at = completed_at
        self.evaluated = evaluated
        self.score = score
        self.evaluated_at = evaluated_at
        self.response_patch = response_patch

    def to_dict(self) -> dict:
        """Convert the object to a dictionary for database operations"""
        return {
            'response_id': self.response_id,
            'challenge_id': self.challenge_id,
            'miner_hotkey': self.miner_hotkey,
            'node_id': self.node_id,
            'processing_time': self.processing_time,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'evaluated': self.evaluated,
            'score': self.score,
            'evaluated_at': self.evaluated_at.isoformat() if self.evaluated_at else None,
            'response_patch': self.response_patch
        }

    @classmethod
    def from_db_row(cls, row: tuple) -> 'Response':
        """Create a Response instance from a database row"""
        return cls(
            response_id=row[0],
            challenge_id=row[1],
            miner_hotkey=row[2],
            node_id=row[3],
            processing_time=float(row[4]) if row[4] is not None else None,
            received_at=datetime.fromisoformat(row[5]) if row[5] else None,
            completed_at=datetime.fromisoformat(row[6]) if row[6] else None,
            evaluated=bool(row[7]),
            score=float(row[8]) if row[8] is not None else None,
            evaluated_at=datetime.fromisoformat(row[9]) if row[9] else None,
            response_patch=row[10]
        )

def get_all_responses(db_path: str = "validator.db") -> List[Response]:
    """
    Read all responses from the database and return them as a list of Response objects.
    
    Args:
        db_path (str): Path to the SQLite database file
        
    Returns:
        List[Response]: List of Response objects
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM responses")
            return [Response.from_db_row(row) for row in cursor.fetchall()]
    except Exception as e:
        st.error(f"Error reading responses: {e}")
        return []

# Get and process responses
responses = get_all_responses()
responses_dict = [response.to_dict() for response in responses]

# Display responses table
st.subheader('Responses table')
responses_df = st.dataframe(
    responses_dict,
    column_order=['response_id', 'challenge_id', 'miner_hotkey', 'node_id', 'processing_time', 
                 'received_at', 'completed_at', 'evaluated', 'score', 'evaluated_at', 'response_patch'],
    on_select="rerun",
    selection_mode="single-row",
    hide_index=True
)

try:
    row_num = responses_df['selection']['rows'][0]
    selected_response = responses_dict[row_num]
    st.code(selected_response['response_patch'], language='diff')
except:
    st.write("Select a response to see the patch")
    pass
