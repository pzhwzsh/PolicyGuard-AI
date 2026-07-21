"""Small, explicit security controls for model/tool boundaries."""

import re
from typing import Any

INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"reveal\s+(the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"bypass\s+(the\s+)?tool\s+(policy|allowlist)", re.IGNORECASE),
    re.compile(r"execute\s+arbitrary\s+(code|command)", re.IGNORECASE),
)
SENSITIVE_KEYS = {"api_key", "authorization", "token", "secret", "password"}


def detect_prompt_injection(value: Any) -> list[str]:
    text = str(value)
    return [pattern.pattern for pattern in INJECTION_PATTERNS if pattern.search(text)]


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if key.casefold() in SENSITIVE_KEYS else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value
