import streamlit as st
from datetime import datetime
from typing import Optional, List
import sqlite3
import pandas as pd

st.set_page_config(layout="wide")

class AvailabilityCheck:
    def __init__(
        self,
        id: Optional[int] = None,  # Optional because it's auto-incrementing
        node_id: int = None,
        hotkey: str = None,
        checked_at: datetime = None,
        is_available: bool = None,
        response_time_ms: float = None,
        error: Optional[str] = None  # Optional because it can be NULL
    ):
        self.id = id
        self.node_id = node_id
        self.hotkey = hotkey
        self.checked_at = checked_at
        self.is_available = is_available
        self.response_time_ms = response_time_ms
        self.error = error

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'node_id': self.node_id,
            'hotkey': self.hotkey,
            'checked_at': self.checked_at.isoformat() if self.checked_at else None,
            'is_available': self.is_available,
            'response_time_ms': self.response_time_ms,
            'error': self.error
        }

    @classmethod
    def from_db_row(cls, row: tuple) -> 'AvailabilityCheck':
        return cls(
            id=row[0],
            node_id=row[1],
            hotkey=row[2],
            checked_at=datetime.fromisoformat(row[3]) if row[3] else None,
            is_available=bool(row[4]),
            response_time_ms=float(row[5]),
            error=row[6]
        )

def get_all_availability_checks(db_path: str = "validator.db") -> List[AvailabilityCheck]:
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM availability_checks")
            return [AvailabilityCheck.from_db_row(row) for row in cursor.fetchall()]
    except Exception as e:
        st.error(f"Error reading availability checks: {e}")
        return []

# Get and process availability checks
availability_checks = get_all_availability_checks()
availability_checks_dict = [check.to_dict() for check in availability_checks]

# Display availability checks table
st.subheader('Availability checks table')
st.dataframe(
    availability_checks_dict,
    column_order=['id', 'node_id', 'hotkey', 'checked_at', 'is_available', 'response_time_ms', 'error'],
    hide_index=True
)

# Calculate average response times
response_times_df = pd.DataFrame([
    {'NodeID': check.node_id, 'Response time (ms)': check.response_time_ms}
    for check in availability_checks
])
avg_response_times = response_times_df.groupby('NodeID')['Response time (ms)'].mean().reset_index()

# Display average response time per node in a bar chart
st.subheader('Average response time per node')
st.bar_chart(
    data=avg_response_times,
    x='NodeID',
    y='Response time (ms)'
)
