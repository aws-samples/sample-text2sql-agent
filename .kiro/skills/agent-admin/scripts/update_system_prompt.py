# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.42",
# ]
# ///
"""Replace an agent's ``system_prompt`` with the contents of a file.

Usage:
    uv run update_system_prompt.py \\
        --table-name TABLE --region REGION \\
        [--id ID] \\
        --prompt-file PATH

The script only accepts file input to sidestep shell-quoting hazards; Kiro is
expected to produce the file via its native ``fsWrite`` tool.
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
    now_iso8601,
    print_json,
    read_text_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update an agent's system_prompt.")
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--id", default="default")
    parser.add_argument("--prompt-file", required=True)
    args = parser.parse_args()

    prompt = read_text_file(args.prompt_file)
    now = now_iso8601()

    client = make_client(args.region)
    try:
        client.update_item(
            TableName=args.table_name,
            Key={"id": {"S": args.id}},
            UpdateExpression="SET system_prompt = :sp, updated_at = :ua",
            ExpressionAttributeValues={
                ":sp": {"S": prompt},
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
            "updated_at": now,
            "bytes": len(prompt.encode("utf-8")),
        }
    )


if __name__ == "__main__":
    main()
