# state.py
# Shared in-memory state to avoid circular imports

_pending_outbound: set = set()