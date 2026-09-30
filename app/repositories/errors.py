class SlotTakenError(Exception):
    """The database rejected a write because it overlaps another confirmed booking (23P01)."""
