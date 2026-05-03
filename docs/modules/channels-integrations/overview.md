# Channels & Integrations

> Module documentation is not a requirements doc or a changelog.

## Purpose

- **Owns**: Receiving messages from external chat platforms, routing them to the internal message bus, and delivering agent responses back to the originating platform. Also owns the Feishu API client layer (document/wiki/drive/messaging operations) and the WhatsApp Node.js bridge process.
- **Does Not Own**: Message processing, agent orchestration, session management (owned by `src/bus/`, `src/session/`, `src/agent/`). Does not own LLM provider selection or tool execution.

## Source Scope

```
src/channels/          # Python channel adapters (10 platforms)
src/channels/base.py   # BaseChannel ABC
src/channels/manager.py # ChannelManager lifecycle + outbound dispatch
src/channels/registry.py # ChannelSpec declarations + lazy import
src/feishu/            # Feishu/Lark API client, token management, doc conversion
bridge/                # Node.js WhatsApp bridge (Baileys + WebSocket server)
```

## Entry Points

| Entry Point | Role |
|---|---|
| `ChannelManager.__init__` (`manager.py:28`) | Reads config, instantiates enabled channels via registry |
| `ChannelManager.start_all` (`manager.py:70`) | Starts outbound dispatcher + all channel event loops |
| `BaseChannel._handle_message` (`base.py:98`) | Inbound path: ACL check then `bus.publish_inbound()` |
| `ChannelManager._dispatch_outbound` (`manager.py:143`) | Outbound path: consumes from bus, routes to channel `.send()` |
| `BridgeServer.start` (`bridge/src/server.ts:27`) | Starts WhatsApp WebSocket bridge on `127.0.0.1` |

## Architecture

### Channel Registry

`registry.py` defines a tuple of `ChannelSpec` dataclasses (`registry.py:33-95`). Each spec declares `name`, `config_attr`, `module`, `class_name`, and optional `extra_kwargs` (config dotpaths resolved at init time). The manager uses `importlib.import_module` for lazy loading -- a channel's dependencies are only imported if enabled.

Registered channels: `telegram`, `whatsapp`, `discord`, `feishu`, `mochat`, `dingtalk`, `email`, `slack`, `qq`, `matrix`.

### BaseChannel Contract

All channels extend `BaseChannel` (`base.py:12`). The ABC requires three methods:

- `start()` -- long-running async task connecting to the platform
- `stop()` -- graceful shutdown
- `send(msg: OutboundMessage)` -- deliver an outbound message

The base class provides:

- `_handle_message()` (`base.py:98`) -- ACL gate (`is_allowed`, `_is_owner_sender`) then publishes `InboundMessage` to the bus
- `pause_inbound()` / `resume_inbound()` -- quiesce support for graceful restarts
- `supports_internal_progress` property -- whether the channel surfaces agent/tool progress (default `True`; Feishu overrides to `False`)
- `transform_progress_message()` -- hook to rewrite or suppress progress messages per channel

### Outbound Dispatch

`ChannelManager._dispatch_outbound` (`manager.py:143`) runs a perpetual loop consuming from `bus.outbound`. It filters progress/streaming messages based on config flags (`send_tool_hints`, `send_progress`) and the channel's `supports_internal_progress`. Non-progress messages are delivered via `channel.send()`. A `_restart_after_send` metadata flag triggers a restart callback after delivery.

### WhatsApp Bridge (Node.js)

The WhatsApp channel uses a two-process architecture:

1. **Bridge process** (`bridge/src/`): Node.js WebSocket server using `@whiskeysockets/baileys` for WhatsApp Web protocol. `BridgeServer` (`server.ts:20`) binds to `127.0.0.1` only, with optional `BRIDGE_TOKEN` auth. It wraps `WhatsAppClient` (`whatsapp.ts:36`) which handles connection lifecycle, QR auth, reconnection, and message extraction.

2. **Python adapter** (`channels/whatsapp.py:13`): Connects to the bridge via `websockets`. Handles `message`, `status`, `qr`, and `error` events from the bridge. Outbound sends are JSON `{type: "send", to, text}` over the WebSocket.

### Feishu Integration

Two layers:

- **Channel adapter** (`channels/feishu.py:249`): Uses lark-oapi WebSocket long connection for event subscription. Runs the WS client in a daemon thread (`feishu.py:352-365`), bridging sync callbacks to the async event loop via `run_coroutine_threadsafe`. Handles text, post (rich text), image, audio, file, interactive card, and share card message types. Outbound sends use interactive cards with markdown-to-card conversion and plain-text fallback.

- **API client** (`feishu/client.py:37`): `FeishuClient` wraps `api.py` functions with file-based JSON caching, user token lifecycle management (auto-reload from disk), and markdown conversion. `api.py` provides typed SDK wrappers for docx, wiki, drive, sheets, contacts, search, and messaging operations. Token precedence: user access token (via `ctx_current_token` ContextVar) > app/tenant token.

## Data Flow

```
Platform SDK/WS
    |
    v
Channel.start() event loop
    |  (parse platform-specific format)
    v
BaseChannel._handle_message()
    |  ACL check (is_allowed + is_owner)
    v
bus.publish_inbound(InboundMessage)
    |
    v
[... agent processing ...]
    |
    v
bus.publish_outbound(OutboundMessage)
    |
    v
ChannelManager._dispatch_outbound()
    |  progress filtering, channel routing
    v
Channel.send(OutboundMessage)
    |  (format to platform-specific API)
    v
Platform SDK/WS
```

## State & Persistence

- **Runtime state**: `ChannelManager.channels` dict (name -> BaseChannel), `_inflight_sends` counter, per-channel `_running` and `_accept_inbound` flags.
- **Telegram**: `_typing_tasks` dict, `_media_group_buffers` for multi-photo aggregation.
- **Feishu**: `_processed_message_ids` OrderedDict (capped at 1000) for dedup, `_keepalive_message_ids` for progress suppression.
- **Slack**: `_bot_user_id` resolved at startup for mention handling.
- **WhatsApp bridge**: Baileys auth state persisted in `authDir` on disk.
- **Feishu client**: File-based cache at `~/.theos/feishu_cache/`, token files at `~/.theos/feishu_tokens/`.
- **Media**: Downloaded media saved to `~/.theos/media/`.
- **Dashboard**: Channel online/offline status written to dashboard via `DashboardWriter`.

## Invariants

1. All inbound messages pass through `BaseChannel._handle_message` -- no channel may bypass the ACL check.
2. `ChannelManager._dispatch_outbound` is the single outbound path; channels never send unsolicited messages.
3. The WhatsApp bridge binds to `127.0.0.1` only (`server.ts:29`) -- never exposed externally.
4. Feishu dedup cache is bounded at 1000 entries (`feishu.py:812-813`).
5. `InboundMessage.session_key` defaults to `"{channel}:{chat_id}"` unless overridden (e.g., Slack thread-scoped sessions use `"slack:{chat_id}:{thread_ts}"`).
6. Config secret refs are resolved at channel init time via `resolve_data_secret_refs` (`manager.py:47`).

## Extension Points

- **Add a new channel**: Create `src/channels/<name>.py` extending `BaseChannel`, add a `ChannelSpec` to `registry.py:CHANNELS`, add config model to `src/config/schema.py`.
- **Custom progress behavior**: Override `supports_internal_progress` and `transform_progress_message` on the channel class.
- **Feishu API extensions**: Add new `api_*.py` modules in `src/feishu/`, wire through `FeishuClient`.

## Failure Modes

| Failure | Impact | Mitigation |
|---|---|---|
| Channel start fails | Channel excluded, others continue (`manager.py:63-68`) | Logged as warning, no crash |
| Outbound send fails | Message lost for that delivery | Logged with context; no retry queue |
| WhatsApp bridge disconnect | Auto-reconnect in both bridge (`whatsapp.ts:88-106`) and Python adapter (`whatsapp.py:61-68`) with 5s backoff |
| Feishu WS disconnect | Daemon thread auto-reconnects with 5s sleep (`feishu.py:354-362`) |
| Feishu user token expired | Falls back to app/tenant token with reduced permissions (`client.py:96-99`) |
| Telegram HTML parse fail | Falls back to plain text send (`telegram.py:283-289`) |
| Feishu card send fail | Falls back to plain text message (`feishu.py:712-737`) |

## Verification

```bash
# Channel manager
uv run pytest tests/test_channel_manager.py -q

# Channel adapters
uv run pytest tests/test_email_channel.py tests/test_matrix_channel.py -q

# Feishu integration
uv run pytest tests/test_feishu_chat.py tests/test_feishu_message_types.py tests/test_feishu_channel_progress.py tests/test_feishu_file.py tests/test_feishu_sheets.py tests/test_feishu_contacts.py tests/test_feishu_tasks.py tests/test_feishu_perm.py tests/test_feishu_permission.py tests/test_feishu_retry.py tests/test_feishu_calendar.py tests/test_feishu_comments_write.py tests/test_feishu_oauth_callback.py tests/test_feishu_remote_auth.py tests/test_feishu2md_extended.py -q

# Messaging tools and conversion
uv run pytest tests/test_message_tool.py tests/test_message_tool_suppress.py tests/test_md2blocks.py -q

# Bridge: cd bridge && npm run build
```

## Related Files

- `src/bus/events.py` -- `InboundMessage` / `OutboundMessage` dataclasses
- `src/bus/queue.py` -- `MessageBus` (publish/consume interface)
- `src/config/schema.py` -- Channel config models (`TelegramConfig`, `SlackConfig`, etc.)
- `src/security/secret_refs.py` -- Secret reference resolution at init
- `src/store/dashboard_writer.py` -- Dashboard channel status updates
- `src/utils/text.py` -- `split_message` for Telegram chunk splitting
