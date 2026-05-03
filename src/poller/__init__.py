"""Poller service — high-frequency lightweight monitors.

Unlike cron (scheduled agent turns) or heartbeat (periodic LLM checks),
pollers are pure-Python loops that run at high frequency (e.g. every 1s)
with **zero token cost**.  They only trigger an agent loop when new content
is detected.

Architecture:
    PollerService  — manages lifecycle of all registered BasePoller instances
    BasePoller     — abstract base; subclass to implement a specific monitor
    XPoller        — monitors X/Twitter accounts for new posts
"""
