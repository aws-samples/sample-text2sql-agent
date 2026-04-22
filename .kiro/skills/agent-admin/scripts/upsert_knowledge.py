# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""Add or replace one Knowledge entry.

Usage:
    uv run upsert_knowledge.py \\
        --table-name TABLE --region REGION \\
        [--id ID] \\
        --knowledge-file PATH

The file must contain exactly one Knowledge entry in the form::

    ---
    name: <kebab-case>
    description: <short description>
    ---
    <markdown body>

If an existing entry with the same ``name`` is found in the DynamoDB
``skills`` attribute, it is replaced in place. Otherwise the new entry is
appended. The whole list is rewritten via ``update_item``, but the caller
only ever supplies one file — the merge happens inside this script.

Note on terminology: the DynamoDB attribute name remains ``skills`` for
backward compatibility with ``agent/agent.py`` and
``lambda/adminwebbackend/app.py``. At the script boundary everything is
called ``knowledge``.
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import ClientError

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import (  # noqa: E402
    EXIT_AWS_ERROR,
    EXIT_NOT_FOUND,
    EXIT_USAGE,
    die,
    extract_knowledge_name,
    make_client,
    now_iso8601,
    print_json,
    read_text_file,
    unwrap_ddb,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add or replace one Knowledge entry."
    )
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--id", default="default")
    parser.add_argument("--knowledge-file", required=True)
    args = parser.parse_args()

    raw = read_text_file(args.knowledge_file)

    try:
        name = extract_knowledge_name(raw)
    except ValueError as e:
        die(str(e), EXIT_USAGE)

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

    existing = unwrap_ddb(resp["Item"]).get("skills", []) or []

    new_list: list[str] = []
    action = "added"
    matched = False
    for entry in existing:
        if not isinstance(entry, str):
            # Unexpected shape: pass through unchanged.
            new_list.append(entry)  # type: ignore[arg-type]
            continue
        try:
            entry_name = extract_knowledge_name(entry)
        except ValueError:
            entry_name = None
        if entry_name == name:
            new_list.append(raw)
            action = "updated"
            matched = True
        else:
            new_list.append(entry)
    if not matched:
        new_list.append(raw)

    now = now_iso8601()
    try:
        client.update_item(
            TableName=args.table_name,
            Key={"id": {"S": args.id}},
            UpdateExpression="SET skills = :sk, updated_at = :ua",
            ExpressionAttributeValues={
                ":sk": {"L": [{"S": s} for s in new_list]},
                ":ua": {"S": now},
            },
            ConditionExpression="attribute_exists(id)",
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            die(f"agent not found: {args.id}", EXIT_NOT_FOUND)
        msg = e.response.get("Error", {}).get("Message", str(e))
        die(f"aws error: {code}: {msg}", EXIT_AWS_ERROR)

    print_json(
        {
            "id": args.id,
            "name": name,
            "action": action,
            "knowledge_count": len(new_list),
            "updated_at": now,
        }
    )


if __name__ == "__main__":
    main()
