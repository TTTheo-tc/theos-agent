"""Feishu/Lark Contacts API — users, departments.

Provides department listing, user lookup by email/phone, and department
member listing using lark-oapi SDK typed bindings (contact.v3).

Existing user operations (get_user, search_users) live in ``api.py`` and are
re-exported here for convenience.
"""

from __future__ import annotations

import lark_oapi as lark
from lark_oapi.api.contact.v3 import (
    BatchGetIdUserRequest,
    BatchGetIdUserRequestBody,
    ChildrenDepartmentRequest,
    GetDepartmentRequest,
    ListUserRequest,
)

from src.feishu.api import (
    _check,
    _request_option,
    _unmarshal,
    search_users,  # noqa: F401 — re-export
)
from src.feishu.api import info_user as get_user  # noqa: F401 — re-export

# ---------------------------------------------------------------------------
# Department operations
# ---------------------------------------------------------------------------


def list_departments(
    client: lark.Client,
    parent_id: str = "0",
    page_size: int = 50,
) -> list[dict]:
    """List child departments of *parent_id*.

    Uses ``GET /open-apis/contact/v3/departments/:parent_id/children``
    (SDK: ``contact.v3.department.children``).

    Args:
        client: lark-oapi client.
        parent_id: Parent department ID (``"0"`` for root).
        page_size: Max results per page.

    Returns:
        List of department dicts.
    """
    option = _request_option()
    departments: list[dict] = []
    page_token: str | None = None

    while True:
        builder = ChildrenDepartmentRequest.builder().department_id(parent_id).page_size(page_size)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.contact.v3.department.children(request, option)
            if option
            else client.contact.v3.department.children(request)
        )
        _check(response, "list_departments")

        data = response.data
        if data.items:
            departments.extend(_unmarshal(data.items))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return departments


def get_department(client: lark.Client, department_id: str) -> dict:
    """Get department info.

    Uses ``GET /open-apis/contact/v3/departments/:department_id``
    (SDK: ``contact.v3.department.get``).

    Args:
        client: lark-oapi client.
        department_id: The department ID.

    Returns:
        Department info dict.
    """
    option = _request_option()
    request = GetDepartmentRequest.builder().department_id(department_id).build()

    response = (
        client.contact.v3.department.get(request, option)
        if option
        else client.contact.v3.department.get(request)
    )
    _check(response, "get_department")
    return _unmarshal(response.data.department)


def list_department_users(
    client: lark.Client,
    department_id: str,
    page_size: int = 50,
) -> list[dict]:
    """List users belonging to *department_id*.

    Uses ``GET /open-apis/contact/v3/users?department_id=<id>``
    (SDK: ``contact.v3.user.list``).

    Args:
        client: lark-oapi client.
        department_id: The department ID.
        page_size: Max results per page.

    Returns:
        List of user dicts.
    """
    option = _request_option()
    users: list[dict] = []
    page_token: str | None = None

    while True:
        builder = ListUserRequest.builder().department_id(department_id).page_size(page_size)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.contact.v3.user.list(request, option)
            if option
            else client.contact.v3.user.list(request)
        )
        _check(response, "list_department_users")

        data = response.data
        if data.items:
            users.extend(_unmarshal(data.items))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return users


# ---------------------------------------------------------------------------
# User lookup by email / phone
# ---------------------------------------------------------------------------


def get_user_by_email(client: lark.Client, email: str) -> dict | None:
    """Find a user by email address.

    Uses ``POST /open-apis/contact/v3/users/batch_get_id`` with ``emails=[email]``
    (SDK: ``contact.v3.user.batch_get_id``).

    Args:
        client: lark-oapi client.
        email: Email address to look up.

    Returns:
        Dict with ``user_id`` and ``email``, or ``None`` if not found.
    """
    option = _request_option()
    body = BatchGetIdUserRequestBody.builder().emails([email]).build()
    request = BatchGetIdUserRequest.builder().request_body(body).build()

    response = (
        client.contact.v3.user.batch_get_id(request, option)
        if option
        else client.contact.v3.user.batch_get_id(request)
    )
    _check(response, "get_user_by_email")

    data = response.data
    user_list = data.user_list if hasattr(data, "user_list") else None
    if not user_list:
        return None

    items = _unmarshal(user_list) if not isinstance(user_list, list) else user_list
    # batch_get_id returns list of {user_id, ...} dicts; find the one matching email
    for item in items:
        if isinstance(item, dict) and item.get("user_id"):
            return item
    return None


def get_user_by_phone(client: lark.Client, phone: str) -> dict | None:
    """Find a user by mobile phone number.

    Uses ``POST /open-apis/contact/v3/users/batch_get_id`` with ``mobiles=[phone]``
    (SDK: ``contact.v3.user.batch_get_id``).

    Args:
        client: lark-oapi client.
        phone: Phone number to look up.

    Returns:
        Dict with ``user_id`` and ``mobile``, or ``None`` if not found.
    """
    option = _request_option()
    body = BatchGetIdUserRequestBody.builder().mobiles([phone]).build()
    request = BatchGetIdUserRequest.builder().request_body(body).build()

    response = (
        client.contact.v3.user.batch_get_id(request, option)
        if option
        else client.contact.v3.user.batch_get_id(request)
    )
    _check(response, "get_user_by_phone")

    data = response.data
    user_list = data.user_list if hasattr(data, "user_list") else None
    if not user_list:
        return None

    items = _unmarshal(user_list) if not isinstance(user_list, list) else user_list
    for item in items:
        if isinstance(item, dict) and item.get("user_id"):
            return item
    return None
