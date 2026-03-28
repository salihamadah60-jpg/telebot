"""
Global runtime state shared between main.py and sorter.py.
Controls pause / resume / stop of long-running operations.
"""

# "running" | "paused" | "stopped"
sorter_state: str = "running"

# Guard: prevents running harvest and sort simultaneously (avoids SQLite session lock)
is_harvesting: bool = False


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
    global is_harvesting
    is_harvesting = True

def end_harvest():
    global is_harvesting
    is_harvesting = False
