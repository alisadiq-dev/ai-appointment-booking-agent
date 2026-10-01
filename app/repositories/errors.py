class SlotTakenError(Exception):
    """The database rejected a write because it overlaps another confirmed booking (23P01)."""


class ActiveBookingLimitError(Exception):
    """The user already holds the maximum number of active (confirmed, future) bookings."""
