"""Feishu Calendar API -- events, attendees, freebusy.

Uses lark-oapi SDK typed bindings (calendar.v4) for all operations.
All functions accept a pre-built ``lark.Client`` so callers own auth configuration.
"""

from __future__ import annotations

import lark_oapi as lark
from lark_oapi.api.calendar.v4 import (
    BatchFreebusyRequest,
    BatchFreebusyRequestBody,
    CalendarEvent,
    CalendarEventAttendee,
    CreateCalendarEventRequest,
    DeleteCalendarEventRequest,
    EventLocation,
    GetCalendarEventRequest,
    ListCalendarEventRequest,
    ListCalendarRequest,
    ListFreebusyRequest,
    ListFreebusyRequestBody,
    TimeInfo,
)

from src.feishu.api import _call_with_option, _check, _request_option, _unmarshal
from src.feishu.retry import with_retry

# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def _extend_items(target: list[dict], items) -> None:
    if items:
        target.extend(_unmarshal(items))


def list_calendars(client: lark.Client) -> list[dict]:
    """List the current user's calendars.

    Uses GET /open-apis/calendar/v4/calendars (paginated).
    """
    option = _request_option()
    calendars: list[dict] = []
    page_token: str | None = None

    while True:
        builder = ListCalendarRequest.builder().page_size(50)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = _call_with_option(client.calendar.v4.calendar.list, request, option)
        _check(response, "list_calendars")

        data = response.data
        _extend_items(calendars, data.calendar_list)
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return calendars


def list_events(
    client: lark.Client,
    calendar_id: str = "primary",
    start_time: str = "",
    end_time: str = "",
    page_size: int = 50,
) -> list[dict]:
    """List events in a calendar within a time range.

    Uses GET /open-apis/calendar/v4/calendars/:id/events (paginated).

    Args:
        calendar_id: Calendar ID or ``"primary"`` for the default calendar.
        start_time: RFC3339 start time, e.g. ``"2026-03-25T00:00:00+08:00"``.
        end_time: RFC3339 end time.
        page_size: Max results per page (max 50).
    """
    option = _request_option()
    events: list[dict] = []
    page_token: str | None = None

    while True:
        builder = (
            ListCalendarEventRequest.builder()
            .calendar_id(calendar_id)
            .page_size(min(page_size, 50))
        )
        if start_time:
            builder = builder.start_time(start_time)
        if end_time:
            builder = builder.end_time(end_time)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = _call_with_option(client.calendar.v4.calendar_event.list, request, option)
        _check(response, "list_events")

        data = response.data
        _extend_items(events, data.items)
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return events


def get_event(client: lark.Client, calendar_id: str, event_id: str) -> dict:
    """Get a single event's detail.

    Uses GET /open-apis/calendar/v4/calendars/:id/events/:event_id.
    """
    option = _request_option()
    request = GetCalendarEventRequest.builder().calendar_id(calendar_id).event_id(event_id).build()
    response = _call_with_option(client.calendar.v4.calendar_event.get, request, option)
    _check(response, "get_event")
    return _unmarshal(response.data.event)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def create_event(
    client: lark.Client,
    calendar_id: str = "primary",
    summary: str = "",
    description: str = "",
    start_time: str = "",
    end_time: str = "",
    attendees: list[dict] | None = None,
    location: str = "",
    is_all_day: bool = False,
) -> dict:
    """Create a calendar event.

    Uses POST /open-apis/calendar/v4/calendars/:id/events.

    Args:
        calendar_id: Calendar ID or ``"primary"``.
        summary: Event title.
        description: Event description.
        start_time: RFC3339 start time, or date string ``"2026-03-25"`` for all-day.
        end_time: RFC3339 end time, or date string for all-day.
        attendees: List of attendee dicts, e.g.
            ``[{"type": "user", "user_id": "xxx"}]``.
        location: Location name string.
        is_all_day: Whether this is an all-day event.
    """
    option = _request_option()

    # Build time info
    start_info = TimeInfo.builder()
    end_info = TimeInfo.builder()
    if is_all_day:
        # All-day events use 'date' field (YYYY-MM-DD)
        start_info = start_info.date(start_time[:10] if start_time else "")
        end_info = end_info.date(end_time[:10] if end_time else "")
    else:
        start_info = start_info.timestamp(start_time)
        end_info = end_info.timestamp(end_time)

    # Build event body
    event_builder = (
        CalendarEvent.builder()
        .summary(summary)
        .description(description)
        .start_time(start_info.build())
        .end_time(end_info.build())
    )

    if location:
        loc = EventLocation.builder().name(location).build()
        event_builder = event_builder.location(loc)

    if attendees:
        att_list = []
        for a in attendees:
            ab = CalendarEventAttendee.builder()
            if a.get("type"):
                ab = ab.type(a["type"])
            if a.get("user_id"):
                ab = ab.user_id(a["user_id"])
            if a.get("chat_id"):
                ab = ab.chat_id(a["chat_id"])
            if a.get("third_party_email"):
                ab = ab.third_party_email(a["third_party_email"])
            if a.get("room_id"):
                ab = ab.room_id(a["room_id"])
            if a.get("is_optional") is not None:
                ab = ab.is_optional(a["is_optional"])
            att_list.append(ab.build())
        event_builder = event_builder.attendees(att_list)

    request = (
        CreateCalendarEventRequest.builder()
        .calendar_id(calendar_id)
        .request_body(event_builder.build())
        .build()
    )
    response = _call_with_option(client.calendar.v4.calendar_event.create, request, option)
    _check(response, "create_event")
    return _unmarshal(response.data.event)


def delete_event(client: lark.Client, calendar_id: str, event_id: str) -> bool:
    """Delete a calendar event.

    Uses DELETE /open-apis/calendar/v4/calendars/:id/events/:event_id.

    Returns:
        ``True`` on success.
    """
    option = _request_option()
    request = (
        DeleteCalendarEventRequest.builder()
        .calendar_id(calendar_id)
        .event_id(event_id)
        .need_notification(True)
        .build()
    )
    response = _call_with_option(client.calendar.v4.calendar_event.delete, request, option)
    _check(response, "delete_event")
    return True


# ---------------------------------------------------------------------------
# Freebusy query
# ---------------------------------------------------------------------------


def freebusy_query(
    client: lark.Client,
    user_ids: list[str],
    start_time: str,
    end_time: str,
) -> dict:
    """Query free/busy status for users.

    Uses POST /open-apis/calendar/v4/freebusy/batch for multiple users,
    or POST /open-apis/calendar/v4/freebusy/list for a single user.

    Args:
        user_ids: List of user IDs to query.
        start_time: RFC3339 start time.
        end_time: RFC3339 end time.

    Returns:
        Freebusy data dict.
    """
    option = _request_option()

    if len(user_ids) == 1:
        # Single user: use list endpoint
        body = (
            ListFreebusyRequestBody.builder()
            .user_id(user_ids[0])
            .time_min(start_time)
            .time_max(end_time)
            .build()
        )
        request = ListFreebusyRequest.builder().request_body(body).build()
        response = _call_with_option(client.calendar.v4.freebusy.list, request, option)
        _check(response, "freebusy_list")
        return _unmarshal(response.data)

    # Multiple users: use batch endpoint
    body = (
        BatchFreebusyRequestBody.builder()
        .user_ids(user_ids)
        .time_min(start_time)
        .time_max(end_time)
        .build()
    )
    request = BatchFreebusyRequest.builder().request_body(body).build()
    response = _call_with_option(client.calendar.v4.freebusy.batch, request, option)
    _check(response, "freebusy_batch")
    return _unmarshal(response.data)


# ---------------------------------------------------------------------------
# Async retry-wrapped variants
# ---------------------------------------------------------------------------


async def list_calendars_with_retry(client: lark.Client, **retry_kwargs) -> list[dict]:
    """list_calendars with automatic retry on transient/rate-limit errors."""
    return await with_retry(list_calendars, client, action="list_calendars", **retry_kwargs)


async def list_events_with_retry(
    client: lark.Client,
    calendar_id: str = "primary",
    start_time: str = "",
    end_time: str = "",
    page_size: int = 50,
    **retry_kwargs,
) -> list[dict]:
    """list_events with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        list_events,
        client,
        calendar_id=calendar_id,
        start_time=start_time,
        end_time=end_time,
        page_size=page_size,
        action="list_events",
        **retry_kwargs,
    )


async def get_event_with_retry(
    client: lark.Client,
    calendar_id: str,
    event_id: str,
    **retry_kwargs,
) -> dict:
    """get_event with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        get_event, client, calendar_id, event_id, action="get_event", **retry_kwargs
    )


async def create_event_with_retry(
    client: lark.Client,
    calendar_id: str = "primary",
    summary: str = "",
    description: str = "",
    start_time: str = "",
    end_time: str = "",
    attendees: list[dict] | None = None,
    location: str = "",
    is_all_day: bool = False,
    **retry_kwargs,
) -> dict:
    """create_event with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        create_event,
        client,
        calendar_id=calendar_id,
        summary=summary,
        description=description,
        start_time=start_time,
        end_time=end_time,
        attendees=attendees,
        location=location,
        is_all_day=is_all_day,
        action="create_event",
        **retry_kwargs,
    )


async def delete_event_with_retry(
    client: lark.Client,
    calendar_id: str,
    event_id: str,
    **retry_kwargs,
) -> bool:
    """delete_event with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        delete_event, client, calendar_id, event_id, action="delete_event", **retry_kwargs
    )


async def freebusy_query_with_retry(
    client: lark.Client,
    user_ids: list[str],
    start_time: str,
    end_time: str,
    **retry_kwargs,
) -> dict:
    """freebusy_query with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        freebusy_query,
        client,
        user_ids,
        start_time,
        end_time,
        action="freebusy_query",
        **retry_kwargs,
    )
