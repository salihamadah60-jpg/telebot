"""
Global runtime state shared between main.py and sorter.py.
Controls pause / resume / stop of long-running operations.
"""

# "running" | "paused" | "stopped"
sorter_state: str = "running"

# Guard: prevents running harvest and sort simultaneously
is_harvesting: bool = False

# Persistent progress message — set by main.py, read by sorter.py
progress_msg_id: int | None = None
progress_chat_id: int | None = None

# Harvest stop flag
harvest_stop: bool = False


def pause():
    global sorter_state
    sorter_state = "paused"

def resume():
    global sorter_state
    sorter_state = "running"

def stop():
    global sorter_state
    sorter_state = "stopped"

def reset():
    global sorter_state
    sorter_state = "running"

def is_paused() -> bool:
    return sorter_state == "paused"

def is_stopped() -> bool:
    return sorter_state == "stopped"

def is_running() -> bool:
    return sorter_state == "running"

def start_harvest():
    global is_harvesting, harvest_stop
    is_harvesting = True
    harvest_stop = False

def end_harvest():
    global is_harvesting
    is_harvesting = False

def stop_harvest():
    global harvest_stop
    harvest_stop = True

def set_progress_msg(msg_id: int, chat_id: int):
    global progress_msg_id, progress_chat_id
    progress_msg_id = msg_id
    progress_chat_id = chat_id

def clear_progress_msg():
    global progress_msg_id, progress_chat_id
    progress_msg_id = None
    progress_chat_id = None
