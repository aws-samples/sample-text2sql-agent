# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""Send a prompt to an AgentCore Runtime agent and return a structured summary.

Usage:
    uv run chat.py \\
        --runtime-arn ARN \\
        --region REGION \\
        --session-id SID (>= 33 chars) \\
        --prompt-file PATH

The prompt file is a JSON document of the shape::

    {
      "prompt": "<natural-language question>",
      "user_id": "kiro-user",
      "agent_id": "default",
      "title": "<short title>"
    }

Only ``prompt`` is required. The script serializes this JSON as UTF-8 bytes
and passes it as the ``payload`` argument of ``invoke_agent_runtime`` — this
avoids the AWS CLI limitation where non-ASCII ``--payload`` values trigger
``string argument should contain only ASCII characters``.

Output (stdout) is a single JSON object::

    {
      "session_id": "...",
      "text": "<tokens concatenated>",
      "tool_uses": [
        {"tool": "_redshift_query", "sql": "SELECT ...", "description": "..."},
        ...
      ],
      "charts": [{"title": "..."}],
      "errors": ["..."],
      "elapsed_seconds": 12
    }

``elapsed_seconds`` is the wall-clock time the whole script spent, measured
with ``time.monotonic()`` from the start of ``main()`` until just before the
final JSON is emitted.

The raw SSE stream is consumed in-process — no intermediate files are written.
Events of type ``done`` are ignored. Malformed ``data:`` lines are recorded in
``errors`` but don't stop processing.

While consuming the stream, a near-real-time progress ticker is emitted to
stderr so that Kiro's execution log shows what the agent is doing. Every line
starts with ``[log HH:MM:SS]`` .
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Iterable

from botocore.exceptions import ClientError

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import (  # noqa: E402
    EXIT_AWS_ERROR,
    EXIT_USAGE,
    die,
    make_agentcore_client,
    print_json,
    read_json_file,
)

MIN_SESSION_ID_LEN = 33

# Flush the token buffer to stderr once this many chars have accumulated.
# Keeps the progress ticker informative without exploding line count.
PROGRESS_TOKEN_FLUSH_CHARS = 256


def _emit_progress(line: str) -> None:
    """Write one progress line to stderr with a ``[log HH:MM:SS]`` prefix.

    Flushes immediately so Kiro's execution log receives it in near-real-time.
    """
    print(
        f"[log {time.strftime('%H:%M:%S')}] {line}",
        file=sys.stderr,
        flush=True,
    )


def _sanitize_1line(s: str) -> str:
    """Collapse CR/LF into spaces so a value never breaks the 1-line format."""
    return s.replace("\r", " ").replace("\n", " ")


def _extract_tool_use(event: dict[str, Any]) -> dict[str, Any]:
    tool = event.get("tool")
    raw_input = event.get("input")
    if tool == "_redshift_query":
        # frontend/agent/src/components/ChatInterface.tsx と同じ取り方:
        # input を JSON パースして sql_query / description を取り出す。
        sql: Any = None
        description: Any = None
        parsed: Any = None
        if isinstance(raw_input, str):
            try:
                parsed = json.loads(raw_input)
            except json.JSONDecodeError:
                parsed = None
        elif isinstance(raw_input, dict):
            parsed = raw_input
        if isinstance(parsed, dict):
            sql = parsed.get("sql_query")
            description = parsed.get("description")
        return {"tool": tool, "sql": sql, "description": description}
    return {"tool": tool, "input": raw_input}


def _iter_sse_lines(body: Any, content_type: str) -> Iterable[bytes]:
    """Yield SSE payload lines (bytes without the trailing newline).

    Supports both the streaming case (``text/event-stream``) and a
    non-streaming fallback where the whole body is returned in one chunk.
    """
    if "text/event-stream" in content_type:
        for line in body.iter_lines(chunk_size=64):
            if line:
                yield line
        return
    # Non-streaming fallback: wrap the raw body as a single data line.
    data = body.read()
    if isinstance(data, bytes):
        yield b"data: " + data
    else:
        yield b"data: " + str(data).encode("utf-8")
    yield b'data: {"type": "done"}'


def main() -> None:
    started_at = time.monotonic()
    parser = argparse.ArgumentParser(
        description="Ask the AgentCore Runtime agent and return a structured summary."
    )
    parser.add_argument("--runtime-arn", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--prompt-file", required=True)
    args = parser.parse_args()

    if len(args.session_id) < MIN_SESSION_ID_LEN:
        die(
            f"session-id must be >= {MIN_SESSION_ID_LEN} chars "
            f"(got {len(args.session_id)})",
            EXIT_USAGE,
        )

    payload_obj = read_json_file(args.prompt_file)
    if not isinstance(payload_obj, dict) or not isinstance(
        payload_obj.get("prompt"), str
    ):
        die("prompt-file must contain a JSON object with 'prompt' (str)", EXIT_USAGE)

    payload_bytes = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")

    client = make_agentcore_client(args.region)
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=args.runtime_arn,
            runtimeSessionId=args.session_id,
            payload=payload_bytes,
        )
    except ClientError as e:
        err = e.response.get("Error", {})
        msg = f"aws error: {err.get('Code', '?')}: {err.get('Message', str(e))}"
        elapsed_seconds = int(time.monotonic() - started_at)
        _emit_progress(f"error: {_sanitize_1line(msg)}")
        _emit_progress(f"done (events=0, elapsed={elapsed_seconds}s)")
        die(msg, EXIT_AWS_ERROR)

    content_type = resp.get("contentType", "")
    body = resp["response"]

    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    errors: list[str] = []
    event_count = 0

    # Local token buffer — flushed to stderr every PROGRESS_TOKEN_FLUSH_CHARS
    # chars, or whenever a non-token event arrives / the loop ends.
    pending_chars = 0
    total_chars = 0

    def flush_pending_tokens() -> None:
        nonlocal pending_chars
        if pending_chars > 0:
            _emit_progress(f"streaming... +{pending_chars}ch (total {total_chars})")
            pending_chars = 0

    for i, line in enumerate(_iter_sse_lines(body, content_type), start=1):
        if not line.startswith(b"data: "):
            continue
        event_count += 1
        payload_str = line[len(b"data: "):].decode("utf-8", errors="replace")

        try:
            ev = json.loads(payload_str)
        except json.JSONDecodeError:
            msg = f"malformed event at line {i}"
            errors.append(msg)
            flush_pending_tokens()
            _emit_progress(f"error: {msg}")
            continue

        if not isinstance(ev, dict):
            msg = f"non-object event at line {i}"
            errors.append(msg)
            flush_pending_tokens()
            _emit_progress(f"error: {msg}")
            continue

        t = ev.get("type")
        if t == "token":
            content = ev.get("content", "")
            if isinstance(content, str):
                text_parts.append(content)
                pending_chars += len(content)
                total_chars += len(content)
                if pending_chars >= PROGRESS_TOKEN_FLUSH_CHARS:
                    flush_pending_tokens()
        elif t == "tool_use":
            entry = _extract_tool_use(ev)
            tool_uses.append(entry)
            flush_pending_tokens()
            tool_name = entry.get("tool") or "?"
            description = entry.get("description")
            if isinstance(description, str) and description:
                _emit_progress(
                    f"tool {tool_name}: {_sanitize_1line(description)}"
                )
            else:
                _emit_progress(f"tool {tool_name}")
        elif t == "chart":
            spec = ev.get("spec") or {}
            title = spec.get("title") if isinstance(spec, dict) else None
            charts.append({"title": title})
            flush_pending_tokens()
            if isinstance(title, str) and title:
                _emit_progress(f"chart: {_sanitize_1line(title)}")
            else:
                _emit_progress("chart")
        elif t == "error":
            raw_msg = ev.get("content") or ev.get("message") or ev
            err_str = str(raw_msg)
            errors.append(err_str)
            flush_pending_tokens()
            _emit_progress(f"error: {_sanitize_1line(err_str)}")
        elif t == "done":
            continue
        # Unknown types are silently ignored to stay forward-compatible.

    flush_pending_tokens()
    elapsed_seconds = int(time.monotonic() - started_at)
    _emit_progress(f"done (events={event_count}, elapsed={elapsed_seconds}s)")

    print_json(
        {
            "session_id": args.session_id,
            "text": "".join(text_parts),
            "tool_uses": tool_uses,
            "charts": charts,
            "errors": errors,
            "elapsed_seconds": elapsed_seconds,
        }
    )


if __name__ == "__main__":
    main()
