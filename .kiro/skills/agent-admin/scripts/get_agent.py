# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""Fetch the full state of a single agent.

Usage:
    uv run get_agent.py --table-name TABLE --region REGION [--id ID]

Returns one JSON object with all relevant attributes, including ``db_schema``
and the full ``knowledge`` list. The DynamoDB attribute ``skills`` is renamed
to ``knowledge`` at the script boundary (Knowledge is the user-facing term).

db_schema fallback
------------------
If the target agent has an empty ``db_schema`` and is not itself the
``default`` agent, the script performs a second ``get_item`` against
``id="default"`` and returns that db_schema instead. The output always
carries ``db_schema_source: "self" | "default"`` so the caller knows where
the value came from. This mirrors the Agent Runtime behavior in
``agent/agent.py`` when loading config.
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import ClientError

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import (  # noqa: E402
    EXIT_AWS_ERROR,
    EXIT_NOT_FOUND,
    die,
    make_client,
    print_json,
    unwrap_ddb,
    warn,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch one agent's full state.")
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--id", default="default")
    args = parser.parse_args()

    client = make_client(args.region)

    try:
        resp = client.get_item(
            TableName=args.table_name, Key={"id": {"S": args.id}}
        )
    except ClientError as e:
        err = e.response.get("Error", {})
        die(f"aws error: {err.get('Code', '?')}: {err.get('Message', str(e))}", EXIT_AWS_ERROR)

    if "Item" not in resp:
        die(f"agent not found: {args.id}", EXIT_NOT_FOUND)

    item = unwrap_ddb(resp["Item"])

    db_schema = item.get("db_schema", "") or ""
    db_schema_source = "self"

    if db_schema == "" and args.id != "default":
        try:
            default_resp = client.get_item(
                TableName=args.table_name, Key={"id": {"S": "default"}}
            )
        except ClientError as e:
            err = e.response.get("Error", {})
            die(
                f"aws error: {err.get('Code', '?')}: {err.get('Message', str(e))}",
                EXIT_AWS_ERROR,
            )
        if "Item" in default_resp:
            default_item = unwrap_ddb(default_resp["Item"])
            db_schema = default_item.get("db_schema", "") or ""
            db_schema_source = "default"
        else:
            warn("default agent not found; returning empty db_schema")
            db_schema = ""
            db_schema_source = "default"

    knowledge = item.get("skills", []) or []

    print_json(
        {
            "id": args.id,
            "agent_name": item.get("agent_name"),
            "system_prompt": item.get("system_prompt", ""),
            "db_schema": db_schema,
            "db_schema_source": db_schema_source,
            "knowledge": knowledge,
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
    )


if __name__ == "__main__":
    main()
