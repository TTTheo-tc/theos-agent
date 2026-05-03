"""Security and isolation utilities for TheOS.

Modules:
  keychain            — OS keychain integration for master key storage
  crypto              — AES-256-GCM per-secret encryption with HKDF key derivation
  credential_injector — Zero-exposure credential injection at HTTP layer
  secret_refs         — runtime resolution of secret:// config references
"""
