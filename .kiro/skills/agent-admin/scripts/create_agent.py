# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""Create a new agent in the agent-admin config table.

Usage:
    uv run create_agent.py \\
        --table-name TABLE --region REGION \\
        --agent-name NAME \\
        [--system-prompt-file PATH]

Inserts a new item with a freshly generated lowercase UUID.
``db_schema`` is deliberately left empty — the Agent Runtime falls back to the
``default`` agent's db_schema when a manual agent has none.
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import ClientError

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import (  # noqa: E402
    EXIT_AWS_ERROR,
    die,
    make_client,
    new_agent_id,
    now_iso8601,
    print_json,
    read_text_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new agent.")
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--agent-name", required=True)
    parser.add_argument(
        "--system-prompt-file",
        default=None,
        help="Optional path to a UTF-8 file containing the system_prompt.",
    )
    args = parser.parse_args()

    agent_name = args.agent_name.strip()
    if not agent_name:
        from _common import EXIT_USAGE

        die("agent-name must not be empty", EXIT_USAGE)

    system_prompt = (
        read_text_file(args.system_prompt_file) if args.system_prompt_file else ""
    )

    agent_id = new_agent_id()
    now = now_iso8601()

    client = make_client(args.region)
    try:
        client.put_item(
            TableName=args.table_name,
            Item={
                "id": {"S": agent_id},
                "agent_name": {"S": agent_name},
                "system_prompt": {"S": system_prompt},
                "db_schema": {"S": ""},
                "skills": {"L": []},
                "created_at": {"S": now},
                "updated_at": {"S": now},
            },
            ConditionExpression="attribute_not_exists(id)",
        )
    except ClientError as e:
        err = e.response.get("Error", {})
        die(f"aws error: {err.get('Code', '?')}: {err.get('Message', str(e))}", EXIT_AWS_ERROR)

    print_json({"id": agent_id, "agent_name": agent_name, "created_at": now})


if __name__ == "__main__":
    main()
