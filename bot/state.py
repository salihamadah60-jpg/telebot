"""
Global runtime state shared between main.py and sorter.py.
Controls pause / resume / stop of long-running operations.
"""

# "running" | "paused" | "stopped"
sorter_state: str = "running"

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
