# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""List all agents in the agent-admin config table.

Usage:
    uv run list_agents.py --table-name TABLE --region REGION

Outputs JSON of the shape:
    {"agents": [{"id": str, "agent_name": str|null, "created_at": str|null}, ...]}
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import ClientError

# Allow running via `uv run <path>` from anywhere: add script dir to sys.path
# so the `_common` sibling module is importable.
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import (  # noqa: E402
    EXIT_AWS_ERROR,
    die,
    make_client,
    print_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="List agents in the config table.")
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args()

    client = make_client(args.region)
    agents: list[dict] = []
    last_key: dict | None = None

    try:
        while True:
            kwargs = {
                "TableName": args.table_name,
                "ProjectionExpression": "id, agent_name, created_at",
            }
            if last_key is not None:
                kwargs["ExclusiveStartKey"] = last_key
            resp = client.scan(**kwargs)
            for item in resp.get("Items", []):
                agents.append(
                    {
                        "id": item["id"]["S"],
                        "agent_name": item.get("agent_name", {}).get("S"),
                        "created_at": item.get("created_at", {}).get("S"),
                    }
                )
            last_key = resp.get("LastEvaluatedKey")
            if last_key is None:
                break
    except ClientError as e:
        err = e.response.get("Error", {})
        die(f"aws error: {err.get('Code', '?')}: {err.get('Message', str(e))}", EXIT_AWS_ERROR)

    print_json({"agents": agents})


if __name__ == "__main__":
    main()
