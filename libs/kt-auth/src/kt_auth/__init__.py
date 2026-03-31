"""Shared authentication utilities for Knowledge Tree services.

Provides token hashing, verification, and Redis-cached API token lookup
so that both the API and MCP services share identical, fast auth logic.
"""

from kt_auth.tokens import (
    ApiTokenVerifier,
    generate_token,
    hash_token,
    verify_token,
)

__all__ = [
    "ApiTokenVerifier",
    "generate_token",
    "hash_token",
    "verify_token",
]
