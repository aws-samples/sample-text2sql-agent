# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""Shared helpers for the agent-admin skill scripts.

Intentionally small: boto3 client factory, ISO8601 timestamps, UUID generation,
JSON output, fatal error exit, strict frontmatter `name` extractor, and a
db_schema write guard. All agent-admin scripts import from this module.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

# Exit codes (aligned with design.md)
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_AWS_ERROR = 4
EXIT_IO_ERROR = 5
EXIT_FORBIDDEN = 6

# db_schema is intentionally excluded. No script writes it.
WRITABLE_FIELDS = frozenset({"system_prompt", "skills", "agent_name"})


def make_client(region: str) -> Any:
    """Return a low-level DynamoDB client in the given region."""
    return boto3.client("dynamodb", region_name=region)


def now_iso8601() -> str:
    """UTC timestamp like '2025-01-02T03:04:05+00:00' (matches existing data)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_agent_id() -> str:
    """Lowercase uuid4 string."""
    return str(uuid.uuid4()).lower()


def print_json(obj: Any) -> None:
    """Print a single JSON object (UTF-8, indented) to stdout."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def die(msg: str, code: int = 1) -> None:
    """Print ``msg`` to stderr and exit with ``code``."""
    print(msg, file=sys.stderr)
    sys.exit(code)


def warn(msg: str) -> None:
    """Print ``msg`` to stderr without exiting."""
    print(msg, file=sys.stderr)


def read_text_file(path: str) -> str:
    """Read a UTF-8 file; die with EXIT_IO_ERROR on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        die(f"cannot read file: {path}: {e}", EXIT_IO_ERROR)
        raise  # unreachable; for type-checkers


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_KEY_LINE_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(.*)$")


def extract_knowledge_name(entry: str) -> str:
    """Extract the ``name`` field from a Knowledge entry.

    Strict grammar: the entry must start with ``---\\n``, be followed by one or
    more ``key: value`` lines, then a terminating ``---\\n``, then the body.
    Only the ``name`` field is extracted; other keys are ignored.

    Raises ``ValueError`` with a human-readable message on any deviation.
    """
    if not entry.startswith("---\n"):
        raise ValueError("knowledge frontmatter missing: entry must start with '---\\n'")

    # Skip the opening fence line.
    rest = entry[len("---\n"):]
    lines = rest.split("\n")

    name: str | None = None
    terminated = False
    for i, line in enumerate(lines):
        if line == "---":
            terminated = True
            break
        m = _KEY_LINE_RE.match(line)
        if not m:
            # Allow blank lines inside the header defensively.
            if line.strip() == "":
                continue
            raise ValueError(
                f"knowledge frontmatter malformed at line {i + 2}: {line!r}"
            )
        key, value = m.group(1), m.group(2)
        if key == "name":
            name = value.strip()

    if not terminated:
        raise ValueError("knowledge frontmatter unterminated: missing closing '---'")
    if name is None:
        raise ValueError("knowledge name missing from frontmatter")
    if name == "":
        raise ValueError("knowledge name empty")
    return name


# ---------------------------------------------------------------------------
# Defensive: no writer script should ever accept db_schema as a field.
# ---------------------------------------------------------------------------

def assert_writable_field(name: str) -> None:
    """Abort with EXIT_FORBIDDEN if ``name`` is not in WRITABLE_FIELDS."""
    if name not in WRITABLE_FIELDS:
        die(
            f"field '{name}' is not writable from this skill "
            "(db_schema is managed by the Admin UI only)",
            code=EXIT_FORBIDDEN,
        )


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

def unwrap_ddb(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a DynamoDB low-level item to plain Python.

    Only ``S`` (string) and ``L`` (list of S) are handled because that is all
    this table uses. Unknown types are passed through unchanged so unexpected
    attributes remain visible for debugging.
    """
    out: dict[str, Any] = {}
    for k, v in item.items():
        if not isinstance(v, dict) or len(v) != 1:
            out[k] = v
            continue
        (tag, val), = v.items()
        if tag == "S":
            out[k] = val
        elif tag == "L":
            out[k] = [elem.get("S", elem) for elem in val]
        else:
            out[k] = v
    return out
