"""Channel registry — metadata for all supported channels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config.schema import Config


@dataclass(frozen=True)
class ChannelSpec:
    """Metadata for one chat channel."""

    name: str  # "telegram"
    config_attr: str  # attribute name on ChannelsConfig
    module: str  # "src.channels.telegram"
    class_name: str  # "TelegramChannel"
    extra_kwargs: tuple[tuple[str, str], ...] = ()  # (kwarg_name, config_dotpath) pairs


def _resolve_dotpath(config: Config, dotpath: str) -> Any:
    """Resolve a dot-separated path like 'providers.groq.api_key' on a config object."""
    from src.security.secret_refs import resolve_data_secret_refs

    obj = config
    for part in dotpath.split("."):
        obj = getattr(obj, part)
    return resolve_data_secret_refs(obj)


CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec(
        name="telegram",
        config_attr="telegram",
        module="src.channels.telegram",
        class_name="TelegramChannel",
        extra_kwargs=(("groq_api_key", "providers.groq.api_key"),),
    ),
    ChannelSpec(
        name="whatsapp",
        config_attr="whatsapp",
        module="src.channels.whatsapp",
        class_name="WhatsAppChannel",
    ),
    ChannelSpec(
        name="discord",
        config_attr="discord",
        module="src.channels.discord",
        class_name="DiscordChannel",
    ),
    ChannelSpec(
        name="feishu",
        config_attr="feishu",
        module="src.channels.feishu",
        class_name="FeishuChannel",
    ),
    ChannelSpec(
        name="mochat",
        config_attr="mochat",
        module="src.channels.mochat",
        class_name="MochatChannel",
    ),
    ChannelSpec(
        name="dingtalk",
        config_attr="dingtalk",
        module="src.channels.dingtalk",
        class_name="DingTalkChannel",
    ),
    ChannelSpec(
        name="email",
        config_attr="email",
        module="src.channels.email",
        class_name="EmailChannel",
    ),
    ChannelSpec(
        name="slack",
        config_attr="slack",
        module="src.channels.slack",
        class_name="SlackChannel",
    ),
    ChannelSpec(
        name="qq",
        config_attr="qq",
        module="src.channels.qq",
        class_name="QQChannel",
    ),
    ChannelSpec(
        name="matrix",
        config_attr="matrix",
        module="src.channels.matrix",
        class_name="MatrixChannel",
    ),
)
