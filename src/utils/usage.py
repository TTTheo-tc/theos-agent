"""Usage tracking utilities."""

_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def merge_usage(target: dict[str, int], source: dict[str, int]) -> None:
    """Accumulate usage counters from source into target."""
    for key in _USAGE_KEYS:
        val = source.get(key, 0)
        if val:
            target[key] = target.get(key, 0) + val
