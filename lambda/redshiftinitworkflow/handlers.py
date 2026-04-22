"""Step Functions ハンドラー: Redshift テーブル初期化ワークフロー

Lambda 1 (handle_start_build):
  旧テーブル DROP → CREATE TABLE → COPY 投入（非同期）

Lambda 2 (handle_check_and_finalize):
  COPY 完了確認 → GRANT → DynamoDB config 更新
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", force=True)
logger = logging.getLogger(__name__)

REDSHIFT_WORKGROUP_NAME = os.environ["REDSHIFT_WORKGROUP_NAME"]
REDSHIFT_DATABASE = os.environ["REDSHIFT_DATABASE"]
REDSHIFT_SECRET_ARN = os.environ["REDSHIFT_SECRET_ARN"]
REDSHIFT_AGENT_SECRET_ARN = os.environ["REDSHIFT_AGENT_SECRET_ARN"]
REDSHIFT_ADMIN_ROLE_ARN = os.environ["REDSHIFT_ADMIN_ROLE_ARN"]
CSV_BUCKET_NAME = os.environ["CSV_BUCKET_NAME"]
CONFIG_TABLE_NAME = os.environ["CONFIG_TABLE_NAME"]

redshift_data = boto3.client("redshift-data")
secrets_client = boto3.client("secretsmanager")
dynamodb = boto3.resource("dynamodb")
config_table = dynamodb.Table(CONFIG_TABLE_NAME)


# ---------------------------------------------------------------------------
# Redshift helpers
# ---------------------------------------------------------------------------

def _execute_sql(sql: str) -> str:
    """execute_statement + 完了待ち（DROP / CREATE 用）"""
    logger.info("execute_sql: %s", sql[:200])
    resp = redshift_data.execute_statement(
        WorkgroupName=REDSHIFT_WORKGROUP_NAME,
        Database=REDSHIFT_DATABASE,
        SecretArn=REDSHIFT_SECRET_ARN,
        Sql=sql,
    )
    statement_id = resp["Id"]
    for _ in range(300):
        desc = redshift_data.describe_statement(Id=statement_id)
        status = desc["Status"]
        if status == "FINISHED":
            return "success"
        if status in ("FAILED", "ABORTED"):
            error = desc.get("Error", "Unknown error")
            raise RuntimeError(f"SQL failed ({status}): {error}\nSQL: {sql[:200]}")
        time.sleep(1)
    raise RuntimeError(f"SQL timeout: {sql[:200]}")


def _execute_sql_async(sql: str) -> str:
    """execute_statement のみ（COPY 用、完了を待たない）。statement_id を返す。"""
    logger.info("execute_sql_async: %s", sql[:200])
    resp = redshift_data.execute_statement(
        WorkgroupName=REDSHIFT_WORKGROUP_NAME,
        Database=REDSHIFT_DATABASE,
        SecretArn=REDSHIFT_SECRET_ARN,
        Sql=sql,
    )
    return resp["Id"]


def _fetch_load_errors(query_ids: list[int], max_rows: int = 20) -> list[dict]:
    """COPY 失敗した query_id の sys_load_error_detail から詳細エラーを取得する（Redshift Serverless 用）"""
    query_id_list = ", ".join(str(qid) for qid in query_ids)
    sql = (
        "SELECT query_id, table_id, "
        "TRIM(file_name) AS file_name, line_number, "
        "TRIM(column_name) AS column_name, TRIM(column_type) AS column_type, "
        "TRIM(error_message) AS error_message "
        "FROM sys_load_error_detail "
        f"WHERE query_id IN ({query_id_list}) "
        "ORDER BY start_time DESC "
        f"LIMIT {max_rows};"
    )
    try:
        resp = redshift_data.execute_statement(
            WorkgroupName=REDSHIFT_WORKGROUP_NAME,
            Database=REDSHIFT_DATABASE,
            SecretArn=REDSHIFT_SECRET_ARN,
            Sql=sql,
        )
        statement_id = resp["Id"]
        for _ in range(60):
            desc = redshift_data.describe_statement(Id=statement_id)
            if desc["Status"] == "FINISHED":
                break
            if desc["Status"] in ("FAILED", "ABORTED"):
                logger.warning("sys_load_error_detail query failed: %s", desc.get("Error"))
                return []
            time.sleep(1)
        else:
            logger.warning("sys_load_error_detail query timed out")
            return []

        result = redshift_data.get_statement_result(Id=statement_id)
        records = result.get("Records", [])
        if not records:
            return []

        columns = [col["name"] for col in result["ColumnMetadata"]]
        details = []
        seen = set()
        for row in records:
            values = {}
            for i, field in enumerate(row):
                val = field.get("stringValue") or field.get("longValue") or field.get("doubleValue")
                if val is not None:
                    values[columns[i]] = str(val).strip()
            # 同一エラーの重複排除（複数ノードスライスや過去の実行で同じエラーが記録される）
            dedup_key = (
                values.get("column_name", ""),
                values.get("line_number", ""),
                values.get("error_message", ""),
            )
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            details.append({
                "column_name": values.get("column_name", ""),
                "column_type": values.get("column_type", ""),
                "line_number": int(values["line_number"]) if values.get("line_number", "").isdigit() else 0,
                "error_message": values.get("error_message", ""),
                "file_name": values.get("file_name", ""),
            })

        logger.error("COPY load error details: %s", json.dumps(details, ensure_ascii=False))
        return details

    except Exception as e:
        logger.warning("Failed to fetch sys_load_error_detail: %s", e)
        return []


def _generate_ddl(table: dict) -> str:
    cols = []
    for col in table["columns"]:
        cols.append(f'  "{col["name"]}" {col["type"]}')
    col_defs = ",\n".join(cols)
    return f'CREATE TABLE "{table["table_name"]}" (\n{col_defs}\n) DISTSTYLE AUTO SORTKEY AUTO;'


def _generate_copy(table: dict) -> list[str]:
    """s3_keys の各ファイルに対して COPY 文を生成"""
    opts = table.get("csv_options", {})
    delimiter = opts.get("delimiter", ",")
    quote_char = opts.get("quote_char", '"')
    null_as = opts.get("null_as", "")

    statements = []
    for key in table["s3_keys"]:
        s3_path = f"s3://{CSV_BUCKET_NAME}/{key}"

        copy_sql = (
            f'COPY "{table["table_name"]}" FROM \'{s3_path}\' '
            f"IAM_ROLE '{REDSHIFT_ADMIN_ROLE_ARN}' "
            f"CSV DELIMITER '{delimiter}' "
            f"IGNOREHEADER 1 "
            f"REGION '{boto3.session.Session().region_name}'"
            f" ENCODING UTF8"
        )

        if quote_char and quote_char != '"':
            copy_sql += f" QUOTE '{quote_char}'"

        if null_as:
            copy_sql += f" NULL AS '{null_as}'"

        dateformat = opts.get("dateformat", "")
        if dateformat:
            copy_sql += f" DATEFORMAT '{dateformat}'"

        timeformat = opts.get("timeformat", "")
        if timeformat:
            copy_sql += f" TIMEFORMAT '{timeformat}'"

        statements.append(copy_sql + ";")

    return statements


# ---------------------------------------------------------------------------
# Lambda 1: StartBuild
# ---------------------------------------------------------------------------

def handle_start_build(event, context):
    """DROP → CREATE TABLE → COPY (async) を実行"""
    system_prompt = event["system_prompt"]
    db_schema = event["db_schema"]
    tables = db_schema.get("tables", [])

    if not tables:
        raise ValueError("テーブル定義がありません")

    # a. 旧テーブル DROP（全 Agent の db_schema から旧テーブル名を収集し、重複排除して DROP）
    try:
        old_table_names: set[str] = set()
        scan_resp = config_table.scan()
        for item in scan_resp.get("Items", []):
            old_schema_str = item.get("db_schema", "")
            if old_schema_str:
                old_schema = json.loads(old_schema_str) if isinstance(old_schema_str, str) else old_schema_str
                for old_table in old_schema.get("tables", []):
                    old_name = old_table.get("table_name")
                    if old_name:
                        old_table_names.add(old_name)
        for old_name in old_table_names:
            try:
                _execute_sql(f'DROP TABLE IF EXISTS "{old_name}";')
            except Exception as e:
                logger.warning("DROP TABLE %s failed: %s", old_name, e)
    except Exception as e:
        logger.warning("Failed to drop old tables: %s", e)

    # b. CREATE TABLE (同期、完了待ち)
    #    CREATE 前に今回のテーブルも DROP しておく（旧 config に無いテーブルが
    #    Redshift に残っている場合の "already exists" を防ぐ）
    tables_created = []
    errors = []
    for table in tables:
        try:
            _execute_sql(f'DROP TABLE IF EXISTS "{table["table_name"]}";')
        except Exception as e:
            logger.warning("DROP TABLE %s (pre-create) failed: %s", table["table_name"], e)
        try:
            ddl = _generate_ddl(table)
            _execute_sql(ddl)
            tables_created.append(table["table_name"])
        except Exception as e:
            errors.append(f"CREATE TABLE {table['table_name']}: {str(e)}")

    # c. COPY (非同期、投げるだけ) — 1テーブルに複数 COPY の場合あり
    copy_statements = {}
    for table in tables:
        if table["table_name"] not in tables_created:
            continue
        try:
            copy_sqls = _generate_copy(table)
            stmt_ids = []
            for copy_sql in copy_sqls:
                stmt_id = _execute_sql_async(copy_sql)
                stmt_ids.append(stmt_id)
            copy_statements[table["table_name"]] = stmt_ids
        except Exception as e:
            errors.append(f"COPY start {table['table_name']}: {str(e)}")

    # CREATE / COPY 開始時点でエラーがあれば即失敗
    if errors:
        raise RuntimeError(json.dumps({
            "status": "failed",
            "tables_created": tables_created,
            "errors": errors,
        }, ensure_ascii=False))

    return {
        "statement_ids": copy_statements,
        "system_prompt": system_prompt,
        "db_schema": db_schema,
        "agent_name": event.get("agent_name", "Default Agent"),
        "tables_created": tables_created,
        "total_tables": len(tables),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Lambda 2: CheckAndFinalize
# ---------------------------------------------------------------------------

def handle_check_and_finalize(event, context):
    """COPY の完了を確認し、全完了なら GRANT + DynamoDB 更新"""
    statement_ids = event["statement_ids"]
    system_prompt = event["system_prompt"]
    db_schema = event["db_schema"]
    agent_name = event.get("agent_name", "Default Agent")
    tables_created = event["tables_created"]
    total_tables = event["total_tables"]
    errors = event.get("errors", [])

    completed = []
    still_running = []
    failed = []

    failed_query_ids: list[int] = []  # COPY 失敗した query_id

    for table_name, stmt_ids in statement_ids.items():
        table_failed = False
        table_running = False
        for stmt_id in stmt_ids:
            desc = redshift_data.describe_statement(Id=stmt_id)
            status = desc["Status"]
            if status in ("FAILED", "ABORTED"):
                error_msg = desc.get("Error", "Unknown error")
                logger.error("COPY %s failed: %s", table_name, error_msg)
                failed.append(f"COPY failed: {table_name}")
                query_id = desc.get("RedshiftQueryId")
                if query_id:
                    failed_query_ids.append(query_id)
                table_failed = True
                break
            elif status != "FINISHED":
                table_running = True
                break
        if table_failed:
            continue
        if table_running:
            still_running.append(table_name)
        else:
            completed.append(table_name)

    if still_running:
        return {
            "all_done": False,
            "completed_tables": len(completed),
            "statement_ids": statement_ids,
            "system_prompt": system_prompt,
            "db_schema": db_schema,
            "agent_name": agent_name,
            "tables_created": tables_created,
            "total_tables": total_tables,
            "errors": errors,
        }

    # 全 COPY 完了（成功 or 失敗）
    all_errors = errors + failed

    # COPY 失敗テーブルがあれば sys_load_error_detail から詳細を取得
    load_error_details = []
    if failed_query_ids:
        load_error_details = _fetch_load_errors(failed_query_ids)

    if failed:
        raise RuntimeError(json.dumps({
            "status": "failed",
            "tables_created": tables_created,
            "errors": all_errors,
            "load_error_details": load_error_details,
        }, ensure_ascii=False))

    # GRANT + DynamoDB 更新
    try:
        secret_resp = secrets_client.get_secret_value(SecretId=REDSHIFT_AGENT_SECRET_ARN)
        agent_creds = json.loads(secret_resp["SecretString"])
        agent_password = agent_creds["password"]
        try:
            _execute_sql(f"CREATE USER agent_readonly PASSWORD '{agent_password}';")
        except RuntimeError as e:
            if "already exists" in str(e).lower():
                logger.info("agent_readonly user already exists")
            else:
                raise
    except Exception as e:
        all_errors.append(f"CREATE USER agent_readonly: {str(e)}")

    grant_sqls = [
        "GRANT USAGE ON SCHEMA public TO agent_readonly;",
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_readonly;",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agent_readonly;",
    ]
    for sql in grant_sqls:
        try:
            _execute_sql(sql)
        except Exception as e:
            all_errors.append(f"GRANT: {str(e)}")

    try:
        # 全 Agent 削除
        scan_resp = config_table.scan()
        with config_table.batch_writer() as batch:
            for item in scan_resp.get("Items", []):
                batch.delete_item(Key={"id": item["id"]})

        # 新規 Agent 作成（Upload & Build で作られる標準 Agent は固定 ID "default"）
        agent_id = "default"
        now = datetime.now(timezone.utc).isoformat()
        config_table.put_item(Item={
            "id": agent_id,
            "agent_name": agent_name,
            "system_prompt": system_prompt,
            "db_schema": json.dumps(db_schema, ensure_ascii=False),
            "skills": [],
            "created_at": now,
            "updated_at": now,
        })
    except Exception as e:
        all_errors.append(f"DynamoDB update: {str(e)}")

    if all_errors:
        raise RuntimeError(json.dumps({
            "status": "failed",
            "tables_created": tables_created,
            "errors": all_errors,
        }, ensure_ascii=False))

    return {
        "all_done": True,
        "status": "completed",
        "tables_created": tables_created,
        "errors": [],
    }
