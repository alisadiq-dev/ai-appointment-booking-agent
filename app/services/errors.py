from app.core.errors import AppError


class ServiceNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__("service_not_found", "Service not found.", 404)


class BookingNotFoundError(AppError):
    """Also used when the booking exists but belongs to someone else (never reveal that)."""

    def __init__(self) -> None:
        super().__init__("booking_not_found", "Booking not found.", 404)


class SlotUnavailableError(AppError):
    def __init__(self) -> None:
        super().__init__("slot_unavailable", "That time slot is no longer available.", 409)


class BookingNotActiveError(AppError):
    def __init__(self) -> None:
        super().__init__("booking_not_active", "This booking is no longer active.", 409)


class BookingLimitReachedError(AppError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            "booking_limit_reached",
            f"You already have the maximum of {limit} upcoming bookings. "
            "Cancel or move one before booking another.",
            409,
        )


class BookingInPastError(AppError):
    def __init__(self, message: str = "Start time must be in the future.") -> None:
        super().__init__("booking_in_past", message, 422)


class OutsideBusinessHoursError(AppError):
    def __init__(self) -> None:
        super().__init__("outside_business_hours", "That time is outside our business hours.", 422)


class InvalidSlotTimeError(AppError):
    def __init__(self, interval_minutes: int) -> None:
        super().__init__(
            "invalid_slot_time",
            f"Start time must fall on a {interval_minutes}-minute slot boundary.",
            422,
        )


class ForbiddenError(AppError):
    def __init__(self) -> None:
        super().__init__("forbidden", "You do not have permission to do that.", 403)


class CalendarUnavailableError(AppError):
    """The business calendar cannot be reached or refused the request. Fails closed."""

    def __init__(self) -> None:
        super().__init__("calendar_unavailable", "The calendar is temporarily unavailable.", 503)


class CalendarEventMissingError(AppError):
    """The booking's calendar entry no longer exists, so it cannot be moved."""

    def __init__(
        self,
        message: str = (
            "This booking's calendar entry no longer exists. Please cancel it and book again."
        ),
    ) -> None:
        super().__init__("calendar_event_missing", message, 409)


class CalendarEventNotFoundError(Exception):
    """The calendar has no (live) event with that id, e.g. it was deleted by hand."""


class TurnInProgressError(AppError):
    """Another chat message from the same user is still being processed."""

    def __init__(self) -> None:
        super().__init__(
            "turn_in_progress",
            "Your previous message is still being processed. Please wait a moment and try again.",
            409,
        )
