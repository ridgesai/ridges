import streamlit as st
import json
from pathlib import Path

# Returns the path to the logs.json file
def get_logs_file():
    return Path(__file__).parents[1] / "logs.json"

# Returns all logs from logs.json as a list of dictionaries
def get_logs():
    log_file = get_logs_file()
    try:
        if log_file.exists():
            with open(log_file, 'r') as f:
                return json.load(f)
    except Exception as e:
        st.error(f"Error reading logs: {e}")
    return []

# Clears all logs by resetting logs.json to an empty array
def clear_logs():
    """Clear all logs by resetting logs.json to an empty array."""
    log_file = get_logs_file()
    try:
        with open(log_file, 'w') as f:
            json.dump([], f)
    except Exception as e:
        print(f"Error clearing log file: {e}")

# Returns the levelname of a log (without the ANSI color codes)
def get_log_levelname(log):
    return log['levelname'][9:-4]

# Returns the desired color of a log levelname
def get_log_color(log_levelname):
    if log_levelname == 'DEBUG':
        log_color = 'cyan'
    elif log_levelname == 'INFO':
        log_color = 'green'
    elif log_levelname == 'WARNING':
        log_color = 'yellow'
    elif log_levelname == 'ERROR' or log_levelname == 'CRITICAL':
        log_color = 'red'
    else:
        log_color = 'white'
    return log_color

# Outputs a log to the dashboard, displays JSON nicely if possible
def output_log(log):
    log_levelname = get_log_levelname(log)
    log_color = get_log_color(log_levelname)
    st.markdown(f"<span style='color: gray; font-style: italic;'>{log['timestamp']}</span> — <span style='color: {log_color};'>**{log_levelname}**</span> from `{log['pathname'] + ':' + str(log['lineno'])}`", unsafe_allow_html=True)
    
    # Check if message is valid JSON, and output accordingly
    try:
        json_obj = json.loads(log['message'])
        st.json(json_obj)
    except (json.JSONDecodeError, TypeError):
        st.text(log['message'])
    
    # Spacing between logs
    st.text('\n')
    st.text('\n')

# Wide view
st.set_page_config(layout="wide")

# Create a container to store logs
log_container = st.empty()

# Initialize session state variables
if "logs" not in st.session_state:
    st.session_state.logs = []
if "files" not in st.session_state:
    st.session_state.files = []
if "file_selection" not in st.session_state:
    st.session_state.file_selection = None
if "levels" not in st.session_state:
    st.session_state.levels = []
if "level_selection" not in st.session_state:
    st.session_state.level_selection = None

# Display logs with log container
with log_container.container():
    # Get logs from logs.json
    st.session_state.logs = get_logs()

    # Get a list of all unique files and levels from the logs
    st.session_state.files = set([log['filename'] for log in st.session_state.logs])
    st.session_state.levels = set([get_log_levelname(log) for log in st.session_state.logs])

    # Sidebar for filters and clearing logs
    with st.sidebar:
        st.session_state.file_selection = st.selectbox("Filter by file", st.session_state.files, index=None)
        st.session_state.level_selection = st.selectbox("Filter by level", st.session_state.levels, index=None)
        if st.button("Clear existing logs", type="primary"):
            clear_logs()
            st.rerun()

    # Title and refresh text
    st.subheader("Validator Logs")
    st.text("Press R to refresh to see latest logs (working on a fix for this)")

    # Display the selected filters
    if st.session_state.file_selection is not None:
        st.markdown(f"Displaying logs from `{st.session_state.file_selection}`")
    if st.session_state.level_selection is not None:
        log_color = get_log_color(st.session_state.level_selection)
        st.markdown(f"Displaying logs with level <span style='color: {log_color};'>**{st.session_state.level_selection}**</span>", unsafe_allow_html=True)
    st.divider()

    # Display the logs that match the selected filters
    num_logs_ouputted = 0
    for log in reversed(st.session_state.logs):
        if (st.session_state.file_selection is None or log['filename'] in st.session_state.file_selection) and (st.session_state.level_selection is None or get_log_levelname(log) in st.session_state.level_selection):
            output_log(log)
            num_logs_ouputted += 1
    
    # Output the number of logs that match the selected filters
    st.divider()
    st.text(f"Displayed {num_logs_ouputted} logs that match the selected filters")
