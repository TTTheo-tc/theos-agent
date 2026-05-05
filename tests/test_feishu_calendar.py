"""Tests for Feishu Calendar API, FeishuClient calendar methods, and FeishuCalendarTool."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent.tools.feishu import FeishuCalendarTool, _resolve_natural_date
from src.feishu import api_calendar

# ---------------------------------------------------------------------------
# Helpers: build mock lark client with calendar.v4 stubs
# ---------------------------------------------------------------------------


def _ok_response(data_obj):
    """Build a mock SDK response that passes _check()."""
    resp = MagicMock()
    resp.success.return_value = True
    resp.code = 0
    resp.msg = "ok"
    resp.data = data_obj
    return resp


def _mock_lark_client():
    """Return a mock lark.Client with calendar.v4 sub-services wired up."""
    client = MagicMock()
    # Ensure the nested attribute chain exists
    client.calendar.v4.calendar.list = MagicMock()
    client.calendar.v4.calendar_event.list = MagicMock()
    client.calendar.v4.calendar_event.get = MagicMock()
    client.calendar.v4.calendar_event.create = MagicMock()
    client.calendar.v4.calendar_event.delete = MagicMock()
    client.calendar.v4.freebusy.list = MagicMock()
    client.calendar.v4.freebusy.batch = MagicMock()
    return client


# ---------------------------------------------------------------------------
# api_calendar unit tests
# ---------------------------------------------------------------------------


class TestListCalendars:
    def test_list_calendars(self):
        client = _mock_lark_client()
        data = SimpleNamespace(
            calendar_list=[{"calendar_id": "cal_1", "summary": "My Calendar"}],
            has_more=False,
            page_token=None,
        )
        client.calendar.v4.calendar.list.return_value = _ok_response(data)

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            result = api_calendar.list_calendars(client)

        assert len(result) == 1
        assert result[0]["calendar_id"] == "cal_1"

    def test_list_calendars_passes_request_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(calendar_list=[], has_more=False, page_token=None)
        client.calendar.v4.calendar.list.return_value = _ok_response(data)
        option = object()

        with patch("src.feishu.api_calendar._request_option", return_value=option):
            api_calendar.list_calendars(client)

        assert client.calendar.v4.calendar.list.call_args.args[1] is option

    def test_list_calendars_paginated(self):
        client = _mock_lark_client()
        page1 = SimpleNamespace(
            calendar_list=[{"calendar_id": "cal_1"}],
            has_more=True,
            page_token="page2",
        )
        page2 = SimpleNamespace(
            calendar_list=[{"calendar_id": "cal_2"}],
            has_more=False,
            page_token=None,
        )
        client.calendar.v4.calendar.list.side_effect = [
            _ok_response(page1),
            _ok_response(page2),
        ]

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            result = api_calendar.list_calendars(client)

        assert len(result) == 2


class TestListEvents:
    def test_list_events_with_date_range(self):
        client = _mock_lark_client()
        data = SimpleNamespace(
            items=[
                {"event_id": "ev_1", "summary": "Standup"},
                {"event_id": "ev_2", "summary": "Lunch"},
            ],
            has_more=False,
            page_token=None,
        )
        client.calendar.v4.calendar_event.list.return_value = _ok_response(data)

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            result = api_calendar.list_events(
                client,
                calendar_id="primary",
                start_time="2026-03-25T00:00:00+08:00",
                end_time="2026-03-25T23:59:59+08:00",
            )

        assert len(result) == 2
        assert result[0]["event_id"] == "ev_1"

    def test_list_events_uses_one_arg_when_no_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(items=[], has_more=False, page_token=None)
        client.calendar.v4.calendar_event.list.return_value = _ok_response(data)

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            api_calendar.list_events(client)

        assert len(client.calendar.v4.calendar_event.list.call_args.args) == 1

    def test_list_events_empty(self):
        client = _mock_lark_client()
        data = SimpleNamespace(items=None, has_more=False, page_token=None)
        client.calendar.v4.calendar_event.list.return_value = _ok_response(data)

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            result = api_calendar.list_events(client)

        assert result == []


class TestCreateEvent:
    def test_create_event(self):
        client = _mock_lark_client()
        created = SimpleNamespace(event={"event_id": "ev_new", "summary": "Team Sync"})
        client.calendar.v4.calendar_event.create.return_value = _ok_response(created)

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            result = api_calendar.create_event(
                client,
                summary="Team Sync",
                start_time="2026-03-25T10:00:00+08:00",
                end_time="2026-03-25T11:00:00+08:00",
                attendees=[{"type": "user", "user_id": "ou_abc123"}],
                location="Meeting Room A",
            )

        assert result["event_id"] == "ev_new"
        assert result["summary"] == "Team Sync"

    def test_create_all_day_event(self):
        client = _mock_lark_client()
        created = SimpleNamespace(event={"event_id": "ev_allday", "summary": "Holiday"})
        client.calendar.v4.calendar_event.create.return_value = _ok_response(created)

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            result = api_calendar.create_event(
                client,
                summary="Holiday",
                start_time="2026-03-25",
                end_time="2026-03-26",
                is_all_day=True,
            )

        assert result["event_id"] == "ev_allday"


class TestDeleteEvent:
    def test_delete_event(self):
        client = _mock_lark_client()
        # delete returns empty data on success
        client.calendar.v4.calendar_event.delete.return_value = _ok_response(SimpleNamespace())

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            result = api_calendar.delete_event(client, "primary", "ev_123")

        assert result is True


class TestFreebusyQuery:
    def test_freebusy_single_user(self):
        client = _mock_lark_client()
        fb_data = SimpleNamespace(freebusy_list=[{"start_time": "...", "end_time": "..."}])
        client.calendar.v4.freebusy.list.return_value = _ok_response(fb_data)

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            api_calendar.freebusy_query(
                client,
                user_ids=["ou_user1"],
                start_time="2026-03-25T00:00:00+08:00",
                end_time="2026-03-25T23:59:59+08:00",
            )

        # Called freebusy.list (not batch) for single user
        client.calendar.v4.freebusy.list.assert_called_once()
        client.calendar.v4.freebusy.batch.assert_not_called()

    def test_freebusy_single_user_passes_request_option(self):
        client = _mock_lark_client()
        fb_data = SimpleNamespace(freebusy_list=[])
        client.calendar.v4.freebusy.list.return_value = _ok_response(fb_data)
        option = object()

        with patch("src.feishu.api_calendar._request_option", return_value=option):
            api_calendar.freebusy_query(
                client,
                user_ids=["ou_user1"],
                start_time="2026-03-25T00:00:00+08:00",
                end_time="2026-03-25T23:59:59+08:00",
            )

        assert client.calendar.v4.freebusy.list.call_args.args[1] is option

    def test_freebusy_multiple_users(self):
        client = _mock_lark_client()
        fb_data = SimpleNamespace(freebusy_list={})
        client.calendar.v4.freebusy.batch.return_value = _ok_response(fb_data)

        with patch("src.feishu.api_calendar._request_option", return_value=None):
            api_calendar.freebusy_query(
                client,
                user_ids=["ou_user1", "ou_user2"],
                start_time="2026-03-25T00:00:00+08:00",
                end_time="2026-03-25T23:59:59+08:00",
            )

        client.calendar.v4.freebusy.batch.assert_called_once()
        client.calendar.v4.freebusy.list.assert_not_called()


# ---------------------------------------------------------------------------
# Natural date parsing tests
# ---------------------------------------------------------------------------


class TestNaturalDateParsing:
    def test_today_start(self):
        result = _resolve_natural_date("today", is_end=False)
        dt = datetime.fromisoformat(result)
        now = datetime.now().astimezone()
        assert dt.date() == now.date()
        assert dt.hour == 0
        assert dt.minute == 0

    def test_today_end(self):
        result = _resolve_natural_date("today", is_end=True)
        dt = datetime.fromisoformat(result)
        now = datetime.now().astimezone()
        assert dt.date() == now.date()
        assert dt.hour == 23
        assert dt.minute == 59

    def test_tomorrow_start(self):
        result = _resolve_natural_date("tomorrow", is_end=False)
        dt = datetime.fromisoformat(result)
        expected = (datetime.now().astimezone() + timedelta(days=1)).date()
        assert dt.date() == expected
        assert dt.hour == 0

    def test_tomorrow_end(self):
        result = _resolve_natural_date("Tomorrow", is_end=True)
        dt = datetime.fromisoformat(result)
        expected = (datetime.now().astimezone() + timedelta(days=1)).date()
        assert dt.date() == expected
        assert dt.hour == 23

    def test_this_week_start(self):
        result = _resolve_natural_date("this week", is_end=False)
        dt = datetime.fromisoformat(result)
        # Should be Monday
        assert dt.weekday() == 0
        assert dt.hour == 0

    def test_this_week_end(self):
        result = _resolve_natural_date("this week", is_end=True)
        dt = datetime.fromisoformat(result)
        # Should be Sunday
        assert dt.weekday() == 6
        assert dt.hour == 23

    def test_passthrough_rfc3339(self):
        rfc = "2026-03-25T10:00:00+08:00"
        assert _resolve_natural_date(rfc) == rfc

    def test_case_insensitive(self):
        result = _resolve_natural_date("TODAY", is_end=False)
        dt = datetime.fromisoformat(result)
        assert dt.hour == 0


# ---------------------------------------------------------------------------
# Tool schema + execute tests
# ---------------------------------------------------------------------------


class TestFeishuCalendarToolSchema:
    def test_tool_schema(self):
        tool = FeishuCalendarTool(client=MagicMock())
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "feishu_calendar"
        params = schema["function"]["parameters"]
        assert "action" in params["properties"]
        assert params["properties"]["action"]["enum"] == [
            "list",
            "events",
            "create",
            "delete",
            "freebusy",
        ]
        assert "action" in params["required"]

    def test_risk_level(self):
        tool = FeishuCalendarTool(client=MagicMock())
        assert tool.risk_level == "medium"


class TestFeishuCalendarToolExecute:
    def _make_tool(self):
        mock_client = MagicMock()
        mock_client.calendar_list.return_value = [{"calendar_id": "cal_1"}]
        mock_client.calendar_events.return_value = [{"event_id": "ev_1"}]
        mock_client.calendar_create_event.return_value = {"event_id": "ev_new"}
        mock_client.calendar_delete_event.return_value = True
        mock_client.calendar_freebusy.return_value = {"freebusy_list": []}
        return FeishuCalendarTool(client=mock_client), mock_client

    def test_execute_list(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="list"))
        assert "cal_1" in result
        mock_client.calendar_list.assert_called_once()

    def test_execute_events(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="events", start_time="today", end_time="today"))
        assert "ev_1" in result
        mock_client.calendar_events.assert_called_once()

    def test_execute_create(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(
            tool.execute(
                action="create",
                summary="Standup",
                start_time="2026-03-25T10:00:00+08:00",
                end_time="2026-03-25T10:30:00+08:00",
                attendees=["ou_abc"],
            )
        )
        assert "ev_new" in result
        mock_client.calendar_create_event.assert_called_once()
        # Verify attendees were converted to dicts
        call_kwargs = mock_client.calendar_create_event.call_args
        att = call_kwargs.kwargs.get("attendees") or call_kwargs[1].get("attendees")
        assert att == [{"type": "user", "user_id": "ou_abc"}]

    def test_execute_create_missing_summary(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="create", start_time="today", end_time="today"))
        assert "Error" in result

    def test_execute_delete(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="delete", event_id="ev_123"))
        assert "true" in result.lower() or "True" in result
        mock_client.calendar_delete_event.assert_called_once()

    def test_execute_delete_missing_event_id(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="delete"))
        assert "Error" in result

    def test_execute_freebusy(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(
            tool.execute(
                action="freebusy",
                user_ids=["ou_1", "ou_2"],
                start_time="today",
                end_time="today",
            )
        )
        assert "freebusy_list" in result
        mock_client.calendar_freebusy.assert_called_once()

    def test_execute_freebusy_missing_user_ids(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="freebusy", start_time="today", end_time="today"))
        assert "Error" in result

    def test_execute_unknown_action(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="unknown_action"))
        assert "Error" in result
        assert "unknown_action" in result
