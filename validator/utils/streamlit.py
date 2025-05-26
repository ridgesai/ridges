import streamlit as st
import json
from pathlib import Path

def get_logs_file():
    return Path(__file__).parent / "logs.json"

def get_logs():
    log_file = get_logs_file()
    try:
        if log_file.exists():
            with open(log_file, 'r') as f:
                return json.load(f)
    except Exception as e:
        st.error(f"Error reading logs: {e}")
    return []

def get_log_levelname(log):
    return log['levelname'][9:-4]

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

def output_log(log):
    log_levelname = get_log_levelname(log)
    log_color = get_log_color(log_levelname)
    st.markdown(f"<span style='color: gray; font-style: italic;'>{log['timestamp']}</span> — <span style='color: {log_color};'>**{log_levelname}**</span> from `{log['pathname'] + ':' + str(log['lineno'])}`", unsafe_allow_html=True)
    
    # Check if message is valid JSON
    try:
        json_obj = json.loads(log['message'])
        st.json(json_obj)
    except (json.JSONDecodeError, TypeError):
        st.text(log['message'])
    
    st.text('\n')
    st.text('\n')

st.set_page_config(layout="wide")

# Create a container for logs
log_container = st.empty()

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

# Display logs in a more readable format
with log_container.container():
    st.session_state.logs = get_logs()
    st.session_state.files = set([log['pathname'] for log in st.session_state.logs])
    st.session_state.levels = set([get_log_levelname(log) for log in st.session_state.logs])

    with st.sidebar:
        st.text("Filter by file")
        st.session_state.file_selection = st.selectbox("Files", st.session_state.files, index=None)
        st.text("Filter by level")
        st.session_state.level_selection = st.selectbox("Levels", st.session_state.levels, index=None)
    
    st.title("Validator dashboard")

    if st.session_state.file_selection is not None:
        st.markdown(f"Displaying logs from `{st.session_state.file_selection}`")
    if st.session_state.level_selection is not None:
        log_color = get_log_color(st.session_state.level_selection)
        st.markdown(f"Displaying logs with level <span style='color: {log_color};'>**{st.session_state.level_selection}**</span>", unsafe_allow_html=True)
    st.divider()

    num_logs_ouputted = 0
    for log in st.session_state.logs:
        if (st.session_state.file_selection is None or log['pathname'] in st.session_state.file_selection) and (st.session_state.level_selection is None or get_log_levelname(log) in st.session_state.level_selection):
            output_log(log)
            num_logs_ouputted += 1
