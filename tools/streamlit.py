import streamlit as st
import time
import streamlit.components.v1 as components
from pathlib import Path
import random
from typing import List

# Create logs directory if it doesn't exist
LOGS_DIR = Path('logs')
LOGS_DIR.mkdir(exist_ok=True)

class Log:
    def __init__(self, timestamp: str, pathname: str, messages: list[str]):
        self.timestamp = timestamp
        self.pathname = pathname
        self.messages = messages

def read_logs():
    try:
        all_logs: List[Log] = []

        # Get all .log files in the logs directory and sort by creation time
        log_files = sorted(LOGS_DIR.glob('*.log'), key=lambda x: x.stat().st_ctime)
        
        for log_file in log_files:
            with open(log_file, 'r') as f:
                log_message = f.readlines()
            log_data = log_file.stem.split('__')

            log_date_time = log_data[0].split('_')
            log_date = log_date_time[0].replace('-', '/')
            log_time = log_date_time[1].replace('-', ':')[:-4]
            log_timestamp = f"{log_date} {log_time}"
            
            log_path = log_data[1].replace('_', '/')

            all_logs.append(Log(log_timestamp, log_path, log_message))

            # Delete the file after reading
            log_file.unlink()
        
        return all_logs
    except FileNotFoundError:
        return []

def output_log(log):
    st.markdown(
        f"<span style='color:orange'>**{log.timestamp}**</span> `{log.pathname}`", 
        unsafe_allow_html=True
    )
    if log.messages[0].startswith("{"):
        st.json(log.messages[0])
    else:
        for line in log.messages:
            st.text(line)
    st.text("\n")
    st.text("\n")

# Initialize Streamlit page
st.set_page_config(page_title="Local Subtensor Logging", layout="wide")
st.title("Local Subtensor Logging 🔧")
st.subheader("View the logs of a local subtensor repository")

st.markdown("This is a tool to view all the local logs within a subtensor repository. Currently it seperates each iteration of your validator's `forward()` with a divider.")
st.markdown("Ensure this streamlit app is running before running your neurons.")
with st.expander("Documentation"):
    st.markdown("""
    To ensure your logs appear in this dashboard you need to:
    - Ensure you are running a validator
    - Ensure you are running a miner
    - Ensure that logs throughout the codebase are logged using the `logger.info()` method
    - <span style="color:red">**IMPORTANT**</span> DO NOT add any other characters to logs that contain JSON, they must be logged alone
    """,
    unsafe_allow_html=True
    )
st.divider()

while True:
    # Read new logs
    new_logs = read_logs()
    
    # Add only new logs
    for log in new_logs:
        try:
            if "Loop number" in log.messages[0]:
                st.divider()
                st.markdown(
                    f'<span style="color: #ADD8E6; font-family: monospace; font-style: italic;">Query number {log.messages[0].split("Loop number ")[1].split(" ")[0]}</span>',
                    unsafe_allow_html=True
                )
            else:
                output_log(log)
        except Exception as e:
            st.markdown(f"<span style='color:red'>**There was an error outputting a log. Check the console for details.**</span>", unsafe_allow_html=True)
            print("--------------------------------")
            print("Error outputting the log:")
            print("\n".join(log.messages))
            print(e)
            print("--------------------------------")

    # Add a small delay
    time.sleep(1)
    