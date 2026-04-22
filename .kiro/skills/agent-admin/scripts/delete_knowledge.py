# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""Remove one Knowledge entry by name.

Usage:
    uv run delete_knowledge.py \\
        --table-name TABLE --region REGION \\
        [--id ID] \\
        --name NAME

Walks the DynamoDB ``skills`` attribute, drops the entry whose frontmatter
``name`` matches ``--name``, and rewrites the attribute. If no entry matched,
exits with EXIT_NOT_FOUND.
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
    extract_knowledge_name,
    make_client,
    now_iso8601,
    print_json,
    read_text_file,  # noqa: F401 (kept available for symmetry)
    unwrap_ddb,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete one Knowledge entry.")
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--id", default="default")
    parser.add_argument("--name", required=True)
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

    existing = unwrap_ddb(resp["Item"]).get("skills", []) or []

    new_list: list[str] = []
    matched = False
    for entry in existing:
        if not isinstance(entry, str):
            new_list.append(entry)  # type: ignore[arg-type]
            continue
        try:
            entry_name = extract_knowledge_name(entry)
        except ValueError:
            entry_name = None
        if entry_name == args.name:
            matched = True
        else:
            new_list.append(entry)

    if not matched:
        die(f"knowledge not found: {args.name}", EXIT_NOT_FOUND)

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
            "name": args.name,
            "knowledge_count": len(new_list),
            "updated_at": now,
        }
    )


if __name__ == "__main__":
    main()
