"""Configuration module for TheOS."""

from src.config.loader import get_config_path, load_config
from src.config.schema import Config

__all__ = ["Config", "get_config_path", "load_config"]
