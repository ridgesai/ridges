import streamlit as st
import logging
import sys
import time
from datetime import datetime
import os
from pathlib import Path

# Create logs directory if it doesn't exist
LOGS_DIR = Path('logs')
LOGS_DIR.mkdir(exist_ok=True)

# Configure logging to write to a file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / 'validator.log', mode='a'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Create a custom handler for Streamlit
class StreamlitHandler(logging.Handler):
    def __init__(self, container):
        super().__init__()
        self.container = container
        self.logs = []

    def emit(self, record):
        msg = self.format(record)
        self.logs.append(msg)
        # Display all logs
        self.container.text("\n".join(self.logs))

def read_logs():
    try:
        with open(LOGS_DIR / 'validator.log', 'r') as f:
            logs = f.readlines()
            # Delete contents after reading
            with open(LOGS_DIR / 'validator.log', 'w') as f:
                f.truncate(0)
            return logs
    except FileNotFoundError:
        return []

# Initialize Streamlit page
st.set_page_config(page_title="Local Subtensor Logging", layout="wide")
st.title("Local Subtensor Logging 🔧")
st.subheader("View the logs of a local Subtensor repository")

st.markdown("This is a tool to view all the local logs within a subtensor repository. Currently it seperates each iteration of `forward()` with a divider.")
st.markdown("Ensure this streamlit app is running before running your neurons.")
st.divider()

while True:
    try:
        # Read new logs
        new_logs = read_logs()
        
        # Add only new logs
        for log in new_logs:
            print(log)
            if "Loop number" in log:
                st.divider()
                st.markdown(
                    f'<span style="color: #ADD8E6; font-family: monospace; font-style: italic;">Query number {log.split("Loop number ")[1].split(" ")[0]}</span>',
                    unsafe_allow_html=True
                )
            else:
                st.markdown(log, unsafe_allow_html=True)
        
        # Add a small delay
        time.sleep(0.1)
        
    except Exception as e:
        st.error(f"Error reading logs: {str(e)}")
        time.sleep(1)  # Longer delay on error
    
