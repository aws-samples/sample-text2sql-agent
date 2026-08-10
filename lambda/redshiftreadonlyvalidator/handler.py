"""CloudFormation カスタムリソース: 既存 Redshift の DB ユーザーが読み取り専用であることを検証する

existingRedshift モードのデプロイ時ガードレール。
提供された Secret の DB ユーザーで Data API に接続し、以下を検証する:

  1. superuser でないこと (pg_user.usesuper = false)
  2. どのユーザースキーマにも CREATE 権限を持たないこと (has_schema_privilege)
  3. どのユーザーテーブルにも INSERT / UPDATE / DELETE 権限を持たないこと (has_table_privilege)

1 つでも違反があれば例外を投げ、CloudFormation のデプロイ自体を失敗させる。

Delete 時は何もしない (このリソースは何も作成しないため)。
"""

import logging
import os
import time

import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

redshift_data = boto3.client("redshift-data")

# システムスキーマは検証対象から除外する
_SYSTEM_SCHEMAS = "('pg_catalog', 'pg_internal', 'information_schema', 'catalog_history', 'pg_automv', 'pg_auto_copy', 'pg_mv', 'pg_s3', 'pg_temp_1', 'pg_toast')"

# ブロッキング検証クエリ。行が返ったら違反 (既存データを変更できてしまう)。
_BLOCKING_QUERIES: list[tuple[str, str]] = [
    (
        "superuser check",
        "SELECT usename FROM pg_user WHERE usename = current_user AND usesuper = true",
    ),
    # 注: Redshift の has_table_privilege は PostgreSQL と異なり
    # 'insert,update,delete' のようなカンマ区切り指定を受け付けないため、
    # 権限ごとに OR で列挙する
    # standard_conforming_strings が ON のため SQL 側は 'pg\_%' (LIKE の _ をエスケープ)
    (
        "table write privilege check",
        "SELECT schemaname || '.' || tablename FROM pg_tables "
        f"WHERE schemaname NOT LIKE 'pg\\_%' AND schemaname NOT IN {_SYSTEM_SCHEMAS} "
        "AND (has_table_privilege(current_user, quote_ident(schemaname) || '.' || quote_ident(tablename), 'insert') "
        "OR has_table_privilege(current_user, quote_ident(schemaname) || '.' || quote_ident(tablename), 'update') "
        "OR has_table_privilege(current_user, quote_ident(schemaname) || '.' || quote_ident(tablename), 'delete')) "
        "LIMIT 20",
    ),
]

# 警告のみの検証クエリ。
# Redshift はデフォルトで public スキーマの CREATE を PUBLIC (全ユーザー) に許可しているため、
# スキーマ CREATE 権限はブロックせず警告に留める (CREATE では既存データは変更できない)。
# 塞ぎたい場合は `REVOKE CREATE ON SCHEMA public FROM PUBLIC;` を実行する。
_WARNING_QUERIES: list[tuple[str, str]] = [
    (
        "schema CREATE privilege check",
        "SELECT nspname FROM pg_namespace "
        f"WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN {_SYSTEM_SCHEMAS} "
        "AND has_schema_privilege(current_user, nspname, 'create') "
        "LIMIT 20",
    ),
]


def _execute_sql(workgroup_name: str, database: str, secret_arn: str, sql: str) -> list[list[dict]]:
    """Data API で SQL を実行し、結果レコードを返す (同期待ち)"""
    resp = redshift_data.execute_statement(
        WorkgroupName=workgroup_name,
        Database=database,
        SecretArn=secret_arn,
        Sql=sql,
    )
    statement_id = resp["Id"]
    for _ in range(120):
        desc = redshift_data.describe_statement(Id=statement_id)
        status = desc["Status"]
        if status == "FINISHED":
            break
        if status in ("FAILED", "ABORTED"):
            error = desc.get("Error", "Unknown error")
            raise RuntimeError(f"Validation query failed ({status}): {error}")
        time.sleep(1)
    else:
        raise RuntimeError("Validation query timed out")

    if not desc.get("HasResultSet"):
        return []
    result = redshift_data.get_statement_result(Id=statement_id)
    return result.get("Records", [])


def _first_column_values(records: list[list[dict]]) -> list[str]:
    values = []
    for row in records:
        field = row[0] if row else {}
        values.append(str(field.get("stringValue", field)))
    return values


def validate_readonly(workgroup_name: str, database: str, secret_arn: str) -> None:
    """readonly でなければ RuntimeError を投げる"""
    violations: list[str] = []
    for name, sql in _BLOCKING_QUERIES:
        records = _execute_sql(workgroup_name, database, secret_arn, sql)
        if records:
            values = _first_column_values(records)
            violations.append(f"{name}: {', '.join(values[:10])}")
            logger.error("readonly validation violation — %s: %s", name, values)
        else:
            logger.info("readonly validation passed: %s", name)

    for name, sql in _WARNING_QUERIES:
        records = _execute_sql(workgroup_name, database, secret_arn, sql)
        if records:
            values = _first_column_values(records)
            logger.warning(
                "readonly validation warning — %s: %s "
                "(CREATE cannot modify existing data; run "
                "`REVOKE CREATE ON SCHEMA <schema> FROM PUBLIC;` to remove it)",
                name, values,
            )
        else:
            logger.info("readonly validation passed: %s", name)

    if violations:
        raise RuntimeError(
            "The provided Redshift DB user is NOT read-only. "
            "Deployment is blocked to protect the existing Redshift data. "
            "Violations: " + " / ".join(violations)
        )


def on_event(event, context):
    """CDK Provider Framework エントリポイント"""
    request_type = event["RequestType"]
    props = event.get("ResourceProperties", {})
    physical_id = "redshift-readonly-validation"

    if request_type == "Delete":
        # 何も作成していないので何もしない
        return {"PhysicalResourceId": event.get("PhysicalResourceId", physical_id)}

    workgroup_name = props["WorkgroupName"]
    database = props["Database"]
    secret_arn = props["SecretArn"]

    logger.info(
        "validating readonly user: workgroup=%s database=%s secret=%s",
        workgroup_name, database, secret_arn,
    )
    validate_readonly(workgroup_name, database, secret_arn)
    logger.info("readonly validation succeeded")

    return {"PhysicalResourceId": physical_id}
