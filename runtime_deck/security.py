"""Security and redaction utilities for RuntimeDeck."""

import re
from typing import Optional

# Sensitive parameter flags and prefixes
# e.g., --api-key, --api_key, --token, --hf-token, --auth, --password, --passwd, --secret
SENSITIVE_ARGS_PATTERN = re.compile(
    r"""(?xi)
    # Flag followed by = or space: e.g. --api-key=secret or --api-key secret
    (?P<flag>--(?:api[-_]?key|hf[-_]?token|token|auth|password|passwd|secret))
    (?P<sep>[=\s]+)
    (?P<val>[^\s"']+|"[^"]*"|'[^']*')
    """
)

# Authorization header style: e.g., Authorization: Bearer <token> or Bearer <token>
BEARER_PATTERN = re.compile(
    r"""(?xi)
    (?P<prefix>\b(?:bearer|authorization\s*:\s*bearer)\s+)
    (?P<val>[^\s"']+)
    """
)

# Env-style assignment: KEY=value / TOKEN=value / SECRET=value / PASSWORD=value
ENV_KEY_PATTERN = re.compile(
    r"""(?xi)
    (?P<key>\b[A-Za-z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|AUTH)[A-Za-z0-9_]*)
    (?P<sep>=)
    (?P<val>[^\s"']+|"[^"]*"|'[^']*')
    """
)


def redact_command_line(command_line: Optional[str]) -> str:
    """
    Redact sensitive keys, passwords, and tokens from a command line string.
    Retains parameter names and flags while replacing values with '[REDACTED]'.
    """
    if not command_line:
        return ""

    text = str(command_line)

    def _replace_arg(match: re.Match) -> str:
        flag = match.group("flag")
        sep = match.group("sep")
        return f"{flag}{sep}[REDACTED]"

    def _replace_bearer(match: re.Match) -> str:
        prefix = match.group("prefix")
        return f"{prefix}[REDACTED]"

    def _replace_env(match: re.Match) -> str:
        key = match.group("key")
        sep = match.group("sep")
        return f"{key}{sep}[REDACTED]"

    text = SENSITIVE_ARGS_PATTERN.sub(_replace_arg, text)
    text = BEARER_PATTERN.sub(_replace_bearer, text)
    text = ENV_KEY_PATTERN.sub(_replace_env, text)

    return text


def safe_error_detail(text: Optional[str], max_length: int = 240) -> str:
    """
    Return sanitized and bounded error detail without leaking sensitive credentials
    or unbounded stack traces / subprocess output.
    """
    if not text:
        return ""

    cleaned = redact_command_line(str(text)).strip()
    # Normalize line breaks and multiple spaces to avoid broken logs / json
    cleaned = re.sub(r"[\r\n]+", " ; ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)

    if len(cleaned) > max_length:
        return cleaned[:max_length] + "..."
    return cleaned
