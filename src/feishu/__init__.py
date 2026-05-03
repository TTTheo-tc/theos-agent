"""Feishu/Lark document management — integrated from feishu-sync."""

try:
    from src.feishu.client import FeishuClient

    __all__ = ["FeishuClient"]
except ImportError:
    __all__ = []
