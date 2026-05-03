"""Feishu Chat/Group management API using lark-oapi SDK.

Provides chat creation, member management, message listing, pinning, and reactions.
All functions accept a pre-built ``lark.Client`` so callers own auth configuration.
"""

from __future__ import annotations

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateChatMembersRequest,
    CreateChatMembersRequestBody,
    CreateChatRequest,
    CreateChatRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreatePinRequest,
    CreatePinRequestBody,
    DeleteChatMembersRequest,
    DeleteChatMembersRequestBody,
    Emoji,
    GetChatMembersRequest,
    GetChatRequest,
    ListMessageRequest,
    UpdateChatRequest,
    UpdateChatRequestBody,
)

from src.feishu.api import _check, _request_option, _unmarshal
from src.feishu.retry import with_retry

# ---------------------------------------------------------------------------
# Chat CRUD
# ---------------------------------------------------------------------------


def create_chat(
    client: lark.Client,
    name: str,
    description: str = "",
    user_ids: list[str] | None = None,
) -> dict:
    """Create a group chat.

    Uses ``POST /open-apis/im/v1/chats``
    (SDK: ``im.v1.chat.create``).

    Args:
        client: lark-oapi client.
        name: Chat name.
        description: Chat description.
        user_ids: Initial member user IDs (open_id).

    Returns:
        Created chat dict (chat_id, name, etc.).
    """
    option = _request_option()

    body_builder = CreateChatRequestBody.builder().name(name)
    if description:
        body_builder = body_builder.description(description)
    if user_ids:
        body_builder = body_builder.user_id_list(user_ids)

    request = (
        CreateChatRequest.builder()
        .user_id_type("open_id")
        .request_body(body_builder.build())
        .build()
    )

    response = (
        client.im.v1.chat.create(request, option) if option else client.im.v1.chat.create(request)
    )
    _check(response, "create_chat")
    return _unmarshal(response.data)


def get_chat(client: lark.Client, chat_id: str) -> dict:
    """Get chat details.

    Uses ``GET /open-apis/im/v1/chats/:chat_id``
    (SDK: ``im.v1.chat.get``).

    Args:
        client: lark-oapi client.
        chat_id: The chat ID.

    Returns:
        Chat detail dict.
    """
    option = _request_option()

    request = GetChatRequest.builder().chat_id(chat_id).build()

    response = client.im.v1.chat.get(request, option) if option else client.im.v1.chat.get(request)
    _check(response, "get_chat")
    return _unmarshal(response.data)


def update_chat(
    client: lark.Client,
    chat_id: str,
    name: str | None = None,
    description: str | None = None,
) -> dict:
    """Update chat properties.

    Uses ``PUT /open-apis/im/v1/chats/:chat_id``
    (SDK: ``im.v1.chat.update``).

    Args:
        client: lark-oapi client.
        chat_id: The chat ID.
        name: New chat name (optional).
        description: New chat description (optional).

    Returns:
        API response data dict.
    """
    option = _request_option()

    body_builder = UpdateChatRequestBody.builder()
    if name is not None:
        body_builder = body_builder.name(name)
    if description is not None:
        body_builder = body_builder.description(description)

    request = (
        UpdateChatRequest.builder().chat_id(chat_id).request_body(body_builder.build()).build()
    )

    response = (
        client.im.v1.chat.update(request, option) if option else client.im.v1.chat.update(request)
    )
    _check(response, "update_chat")
    return {"success": True, "chat_id": chat_id}


# ---------------------------------------------------------------------------
# Member management
# ---------------------------------------------------------------------------


def list_chat_members(client: lark.Client, chat_id: str) -> list[dict]:
    """List members of a chat (paginated).

    Uses ``GET /open-apis/im/v1/chats/:chat_id/members``
    (SDK: ``im.v1.chat_members.get``).

    Args:
        client: lark-oapi client.
        chat_id: The chat ID.

    Returns:
        List of member dicts.
    """
    option = _request_option()
    members: list[dict] = []
    page_token: str | None = None

    while True:
        builder = (
            GetChatMembersRequest.builder()
            .chat_id(chat_id)
            .member_id_type("open_id")
            .page_size(100)
        )
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.im.v1.chat_members.get(request, option)
            if option
            else client.im.v1.chat_members.get(request)
        )
        _check(response, "list_chat_members")

        data = response.data
        if data.items:
            members.extend(_unmarshal(data.items))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return members


def add_chat_members(
    client: lark.Client,
    chat_id: str,
    user_ids: list[str],
) -> dict:
    """Add members to a chat.

    Uses ``POST /open-apis/im/v1/chats/:chat_id/members``
    (SDK: ``im.v1.chat_members.create``).

    Args:
        client: lark-oapi client.
        chat_id: The chat ID.
        user_ids: List of user IDs (open_id) to add.

    Returns:
        API response data dict.
    """
    option = _request_option()

    body = CreateChatMembersRequestBody.builder().id_list(user_ids).build()

    request = (
        CreateChatMembersRequest.builder()
        .chat_id(chat_id)
        .member_id_type("open_id")
        .request_body(body)
        .build()
    )

    response = (
        client.im.v1.chat_members.create(request, option)
        if option
        else client.im.v1.chat_members.create(request)
    )
    _check(response, "add_chat_members")
    return _unmarshal(response.data)


def remove_chat_members(
    client: lark.Client,
    chat_id: str,
    user_ids: list[str],
) -> dict:
    """Remove members from a chat.

    Uses ``DELETE /open-apis/im/v1/chats/:chat_id/members``
    (SDK: ``im.v1.chat_members.delete``).

    Args:
        client: lark-oapi client.
        chat_id: The chat ID.
        user_ids: List of user IDs (open_id) to remove.

    Returns:
        API response data dict.
    """
    option = _request_option()

    body = DeleteChatMembersRequestBody.builder().id_list(user_ids).build()

    request = (
        DeleteChatMembersRequest.builder()
        .chat_id(chat_id)
        .member_id_type("open_id")
        .request_body(body)
        .build()
    )

    response = (
        client.im.v1.chat_members.delete(request, option)
        if option
        else client.im.v1.chat_members.delete(request)
    )
    _check(response, "remove_chat_members")
    return _unmarshal(response.data)


# ---------------------------------------------------------------------------
# Message operations
# ---------------------------------------------------------------------------


def list_chat_messages(
    client: lark.Client,
    chat_id: str,
    page_size: int = 50,
    start_time: str = "",
) -> list[dict]:
    """Get message history for a chat.

    Uses ``GET /open-apis/im/v1/messages``
    (SDK: ``im.v1.message.list``).

    Args:
        client: lark-oapi client.
        chat_id: The chat ID.
        page_size: Max results per page (max 50).
        start_time: Start time filter (epoch seconds string, optional).

    Returns:
        List of message dicts.
    """
    option = _request_option()
    messages: list[dict] = []
    page_token: str | None = None

    while True:
        builder = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .page_size(min(page_size, 50))
        )
        if start_time:
            builder = builder.start_time(start_time)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = (
            client.im.v1.message.list(request, option)
            if option
            else client.im.v1.message.list(request)
        )
        _check(response, "list_chat_messages")

        data = response.data
        if data.items:
            messages.extend(_unmarshal(data.items))
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return messages


def pin_message(client: lark.Client, message_id: str) -> bool:
    """Pin a message.

    Uses ``POST /open-apis/im/v1/pins``
    (SDK: ``im.v1.pin.create``).

    Args:
        client: lark-oapi client.
        message_id: The message ID to pin.

    Returns:
        ``True`` on success.
    """
    option = _request_option()

    body = CreatePinRequestBody.builder().message_id(message_id).build()
    request = CreatePinRequest.builder().request_body(body).build()

    response = (
        client.im.v1.pin.create(request, option) if option else client.im.v1.pin.create(request)
    )
    _check(response, "pin_message")
    return True


def add_reaction(
    client: lark.Client,
    message_id: str,
    emoji_type: str,
) -> bool:
    """Add a reaction to a message.

    Uses ``POST /open-apis/im/v1/messages/:message_id/reactions``
    (SDK: ``im.v1.message_reaction.create``).

    Args:
        client: lark-oapi client.
        message_id: The message ID to react to.
        emoji_type: Emoji type string (e.g. ``"THUMBSUP"``, ``"SMILE"``).

    Returns:
        ``True`` on success.
    """
    option = _request_option()

    emoji = Emoji.builder().emoji_type(emoji_type).build()
    body = CreateMessageReactionRequestBody.builder().reaction_type(emoji).build()

    request = (
        CreateMessageReactionRequest.builder().message_id(message_id).request_body(body).build()
    )

    response = (
        client.im.v1.message_reaction.create(request, option)
        if option
        else client.im.v1.message_reaction.create(request)
    )
    _check(response, "add_reaction")
    return True


# ---------------------------------------------------------------------------
# Async retry-wrapped variants
# ---------------------------------------------------------------------------


async def create_chat_with_retry(
    client: lark.Client,
    name: str,
    description: str = "",
    user_ids: list[str] | None = None,
    **retry_kwargs,
) -> dict:
    """create_chat with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        create_chat,
        client,
        name,
        description=description,
        user_ids=user_ids,
        action="create_chat",
        **retry_kwargs,
    )


async def get_chat_with_retry(
    client: lark.Client,
    chat_id: str,
    **retry_kwargs,
) -> dict:
    """get_chat with automatic retry on transient/rate-limit errors."""
    return await with_retry(get_chat, client, chat_id, action="get_chat", **retry_kwargs)


async def update_chat_with_retry(
    client: lark.Client,
    chat_id: str,
    name: str | None = None,
    description: str | None = None,
    **retry_kwargs,
) -> dict:
    """update_chat with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        update_chat,
        client,
        chat_id,
        name=name,
        description=description,
        action="update_chat",
        **retry_kwargs,
    )


async def list_chat_members_with_retry(
    client: lark.Client,
    chat_id: str,
    **retry_kwargs,
) -> list[dict]:
    """list_chat_members with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        list_chat_members, client, chat_id, action="list_chat_members", **retry_kwargs
    )


async def add_chat_members_with_retry(
    client: lark.Client,
    chat_id: str,
    user_ids: list[str],
    **retry_kwargs,
) -> dict:
    """add_chat_members with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        add_chat_members, client, chat_id, user_ids, action="add_chat_members", **retry_kwargs
    )


async def remove_chat_members_with_retry(
    client: lark.Client,
    chat_id: str,
    user_ids: list[str],
    **retry_kwargs,
) -> dict:
    """remove_chat_members with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        remove_chat_members, client, chat_id, user_ids, action="remove_chat_members", **retry_kwargs
    )


async def list_chat_messages_with_retry(
    client: lark.Client,
    chat_id: str,
    page_size: int = 50,
    start_time: str = "",
    **retry_kwargs,
) -> list[dict]:
    """list_chat_messages with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        list_chat_messages,
        client,
        chat_id,
        page_size=page_size,
        start_time=start_time,
        action="list_chat_messages",
        **retry_kwargs,
    )


async def pin_message_with_retry(
    client: lark.Client,
    message_id: str,
    **retry_kwargs,
) -> bool:
    """pin_message with automatic retry on transient/rate-limit errors."""
    return await with_retry(pin_message, client, message_id, action="pin_message", **retry_kwargs)


async def add_reaction_with_retry(
    client: lark.Client,
    message_id: str,
    emoji_type: str,
    **retry_kwargs,
) -> bool:
    """add_reaction with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        add_reaction, client, message_id, emoji_type, action="add_reaction", **retry_kwargs
    )
