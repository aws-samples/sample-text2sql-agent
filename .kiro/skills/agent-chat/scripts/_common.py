# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""Shared helpers for the agent-chat skill scripts.

Small module: boto3 client factory for AgentCore, JSON I/O, session-id helper,
and exit-code constants that mirror agent-admin's conventions.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

# Exit codes
EXIT_USAGE = 2
EXIT_AWS_ERROR = 4
EXIT_IO_ERROR = 5

# AgentCore invocations can take a while because the agent may run multiple
# tool calls (SQL over Redshift, chart rendering, etc.) before the first
# byte. 900s matches the API Gateway / Lambda proxy timeout used elsewhere
# in this project. Retries are disabled: we never want a long-running agent
# call to be silently re-issued by botocore.
AGENTCORE_READ_TIMEOUT_SECONDS = 900
AGENTCORE_CONNECT_TIMEOUT_SECONDS = 10


def make_agentcore_client(region: str) -> Any:
    """Return a low-level ``bedrock-agentcore`` client.

    The client is configured with a 900-second read timeout so long-running
    agent invocations (tool use + streaming responses) don't get cut off by
    botocore's default 60-second read timeout.
    """
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            connect_timeout=AGENTCORE_CONNECT_TIMEOUT_SECONDS,
            read_timeout=AGENTCORE_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def now_iso8601() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_session_id(prefix: str = "kiro-dwh-session") -> str:
    """Generate a session id guaranteed to be >= 33 characters.

    Format: ``<prefix>-<YYYYmmddHHMMSS>-<uuid4[:8]>``. The prefix is
    truncated/padded so the overall length is >= 33.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    sid = f"{prefix}-{stamp}-{suffix}"
    if len(sid) < 33:
        sid = sid + "-" + "0" * (33 - len(sid) - 1)
    return sid


def print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        die(f"cannot read file: {path}: {e}", EXIT_IO_ERROR)
        raise  # unreachable


def read_json_file(path: str) -> Any:
    raw = read_text_file(path)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"invalid JSON in {path}: {e}", EXIT_USAGE)
        raise  # unreachable
