"""CloudFormation カスタムリソース: 既存 Redshift の DB ユーザーが読み取り専用であることを検証する

existingRedshift モードのデプロイ時ガードレール。
提供された Secret の DB ユーザーで Data API に接続し、許可リスト方式で検証する。

設計方針 (デフォルト拒否):
  「危険な権限を列挙して弾く」のではなく「安全と分かっている権限だけを許可し、
  それ以外はすべて違反」とする。Redshift に新しい権限タイプが追加されても
  自動的に違反側に落ちるため、コードの追従が不要になる。

許可される権限 (_ALLOWED_PRIVILEGES):
  - SELECT    : データ読み取り (本機能の目的そのもの)
  - USAGE     : スキーマ内オブジェクトへの参照経路 (これ自体はデータに触れない)
  - TEMP/TEMPORARY : セッション限りの一時テーブル作成。既存データは変更できない。
    Redshift Spectrum クエリの実行要件でもあるため許可する

実装原則: **公式ドキュメントに記載された API / ビュー / 関数のみを使う**。
  ドキュメント未記載で偶然動く構文 (例: SHOW GRANTS FOR GROUP) には依存しない。
  逆にドキュメントに記載があっても実機で未サポートのもの (SHOW GRANTS FOR PUBLIC は
  "Permission discovery for PUBLIC is not supported" を返す) は使えないため、
  PUBLIC 経由の権限は has_table_privilege による実効権限チェックでカバーする。

権限の付与経路と対応するチェック:
  1. ユーザーへの直接 GRANT   → SHOW GRANTS FOR <user> (文書化された構文)
  2. ロール経由 (入れ子含む)   → svv_user_grants + svv_role_grants で展開し SHOW GRANTS FOR ROLE
  3. グループ経由             → pg_group で所属グループを列挙し、
                               SVV_RELATION_PRIVILEGES / SVV_SCHEMA_PRIVILEGES /
                               SVV_DATABASE_PRIVILEGES (いずれも group への付与を表示すると
                               文書に明記) で各グループの権限を検査
  4. PUBLIC への GRANT        → has_table_privilege (実効権限を確認できる文書化された手段)。
                               関数が受け付ける 6 権限のうち SELECT 以外の
                               5 権限 (INSERT/UPDATE/DELETE/DROP/REFERENCES) を検査。
                               この実効チェックはグループ経由の継承も含むため、経路 3 の
                               二重の保険にもなっている
  5. 所有権                   → pg_class.relowner / pg_namespace.nspowner
                               (owner は GRANT なしで全操作できるため)
  6. superuser               → pg_user.usesuper
  補助: ALTER DEFAULT PRIVILEGES による将来オブジェクトへの自動付与
                             → SVV_DEFAULT_PRIVILEGES で検査

警告のみ (ブロックしない):
  - スキーマへの CREATE 権限。Redshift はデフォルトで public スキーマの CREATE を
    PUBLIC (全ユーザー) に許可しており、CREATE では既存データを変更できないため。
    塞ぐ場合は `REVOKE CREATE ON SCHEMA public FROM PUBLIC;` を実行する

1 つでも違反があれば例外を投げ、CloudFormation のデプロイ自体を失敗させる。
Delete 時は何もしない (このリソースは何も作成しないため)。
"""

import logging
import os
import re
import time

import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

redshift_data = boto3.client("redshift-data")

# 許可する権限タイプ (これ以外が付与されていたら違反)
_ALLOWED_PRIVILEGES = {"SELECT", "USAGE", "TEMP", "TEMPORARY"}


def _allowed_privileges_sql() -> str:
    """SQL の NOT IN 句に埋め込む許可リスト。固定値のみでユーザー入力は含まない"""
    return "(" + ", ".join(f"'{p}'" for p in sorted(_ALLOWED_PRIVILEGES)) + ")"

# システムスキーマは所有権チェック等の対象から除外する
_SYSTEM_SCHEMAS = "('pg_catalog', 'pg_internal', 'information_schema', 'catalog_history', 'pg_automv', 'pg_auto_copy', 'pg_mv', 'pg_s3', 'pg_temp_1', 'pg_toast')"


class ValidationContext:
    """Data API 実行のまとめ役"""

    def __init__(self, workgroup_name: str, database: str, secret_arn: str):
        self.workgroup_name = workgroup_name
        self.database = database
        self.secret_arn = secret_arn

    def execute(self, sql: str) -> tuple[list[str], list[list]]:
        """SQL を実行し (カラム名, 行) を返す (同期待ち)"""
        resp = redshift_data.execute_statement(
            WorkgroupName=self.workgroup_name,
            Database=self.database,
            SecretArn=self.secret_arn,
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
                raise RuntimeError(f"Validation query failed ({status}): {error} — SQL: {sql[:120]}")
            time.sleep(1)
        else:
            raise RuntimeError(f"Validation query timed out — SQL: {sql[:120]}")

        if not desc.get("HasResultSet"):
            return [], []
        columns: list[str] = []
        rows: list[list] = []
        next_token = None
        while True:
            kwargs = {"Id": statement_id}
            if next_token:
                kwargs["NextToken"] = next_token
            result = redshift_data.get_statement_result(**kwargs)
            if not columns:
                columns = [c["name"].lower() for c in result["ColumnMetadata"]]
            for record in result.get("Records", []):
                rows.append([_field_value(f) for f in record])
            next_token = result.get("NextToken")
            if not next_token:
                break
        return columns, rows


def _field_value(field: dict):
    if field.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if key in field:
            return field[key]
    return None


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _current_user(ctx: ValidationContext) -> str:
    _, rows = ctx.execute("SELECT current_user")
    return str(rows[0][0])


# ---------------------------------------------------------------------------
# 各チェック (違反メッセージのリストを返す)
# ---------------------------------------------------------------------------

def _check_superuser(ctx: ValidationContext) -> list[str]:
    _, rows = ctx.execute(
        "SELECT usename FROM pg_user WHERE usename = current_user AND usesuper = true"
    )
    return [f"user '{rows[0][0]}' is a superuser"] if rows else []


def _disallowed_from_show_grants(columns: list[str], rows: list[list], subject: str) -> list[str]:
    """SHOW GRANTS の結果から許可リスト外の権限を抽出する"""
    violations = []
    try:
        priv_idx = columns.index("privilege_type")
    except ValueError:
        raise RuntimeError(f"SHOW GRANTS result has no privilege_type column: {columns}")
    obj_idx = columns.index("object_name") if "object_name" in columns else None
    scope_idx = columns.index("privilege_scope") if "privilege_scope" in columns else None
    for row in rows:
        priv = str(row[priv_idx] or "").upper()
        if priv in _ALLOWED_PRIVILEGES:
            continue
        obj = str(row[obj_idx]) if obj_idx is not None and row[obj_idx] is not None else "?"
        scope = str(row[scope_idx]) if scope_idx is not None and row[scope_idx] is not None else ""
        violations.append(f"{subject} has {priv} on {obj}{f' (scope: {scope})' if scope else ''}")
    return violations


def _check_direct_grants(ctx: ValidationContext, username: str) -> list[str]:
    """経路 1: ユーザーへの直接 GRANT (scoped permission 含む) を SHOW GRANTS で検査"""
    columns, rows = ctx.execute(f"SHOW GRANTS FOR {_quote_ident(username)}")
    return _disallowed_from_show_grants(columns, rows, f"user '{username}'")


def _expand_roles(ctx: ValidationContext, username: str) -> tuple[set[str], list[str]]:
    """経路 2: ユーザーが持つロールを入れ子含めて展開する。

    Returns: (通常ロールの集合, sys: ロール由来の違反リスト)
    """
    violations: list[str] = []
    _, rows = ctx.execute(
        "SELECT role_name FROM svv_user_grants WHERE user_name = current_user"
    )
    roles = {str(r[0]) for r in rows if r[0]}

    # 入れ子ロールの展開 (role に granted された role)
    _, nested_rows = ctx.execute(
        "SELECT role_name, granted_role_name FROM svv_role_grants"
    )
    edges: dict[str, set[str]] = {}
    for r in nested_rows:
        if r[0] and r[1]:
            edges.setdefault(str(r[0]), set()).add(str(r[1]))
    frontier = set(roles)
    while frontier:
        nxt = set()
        for role in frontier:
            for granted in edges.get(role, set()):
                if granted not in roles:
                    roles.add(granted)
                    nxt.add(granted)
        frontier = nxt

    # システムロール (sys:*) はシステム権限のバンドルで SHOW GRANTS では中身を列挙できない。
    # デフォルト拒否の方針に従い、付与されていたら違反とする
    normal_roles = set()
    for role in roles:
        if role.lower().startswith("sys:"):
            violations.append(f"user '{username}' has system role '{role}'")
        else:
            normal_roles.add(role)
    return normal_roles, violations


def _check_role_grants(ctx: ValidationContext, roles: set[str]) -> list[str]:
    """経路 2: 各ロールの GRANT を SHOW GRANTS FOR ROLE で検査"""
    violations = []
    for role in sorted(roles):
        columns, rows = ctx.execute(f"SHOW GRANTS FOR ROLE {_quote_ident(role)}")
        violations.extend(_disallowed_from_show_grants(columns, rows, f"role '{role}'"))
    return violations


def _user_groups(ctx: ValidationContext) -> list[str]:
    """ユーザーが所属するグループ名を列挙する"""
    _, user_rows = ctx.execute("SELECT usesysid FROM pg_user WHERE usename = current_user")
    if not user_rows:
        raise RuntimeError("could not resolve current user id")
    usesysid = str(user_rows[0][0])

    _, group_rows = ctx.execute("SELECT groname, grolist FROM pg_group")
    groups = []
    for row in group_rows:
        groname, grolist = row[0], row[1]
        if grolist is None:
            continue
        # grolist は "{100,101,...}" 形式の文字列で返る
        members = set(re.findall(r"\d+", str(grolist)))
        if usesysid in members:
            groups.append(str(groname))
    return groups


def _check_group_grants(ctx: ValidationContext, username: str) -> list[str]:
    """経路 3: グループ経由の GRANT の検査。

    SHOW GRANTS はグループを対象にできない (FOR GROUP は文書化されていない) ため、
    「group への付与を表示する」と文書に明記されている SVV_*_PRIVILEGES 系ビューで列挙する。
    テーブル (relation) / スキーマ / データベースの 3 スコープを検査すれば、
    データを変更しうる権限はカバーできる。

    ビューの可視性は「自分がアクセスできる identity の行のみ」だが、所属グループは
    メンバー本人から見えることを実機確認済み。
    """
    groups = _user_groups(ctx)
    if not groups:
        return []

    group_list = ", ".join(f"'{g.replace(chr(39), chr(39) * 2)}'" for g in groups)
    violations = []

    queries = [
        # (ビュー, オブジェクト名列)
        ("svv_relation_privileges", "relation_name"),
        # スキーマの CREATE はユーザー直接付与と同様に警告相当だが、グループ経由で
        # 明示的に CREATE を付与している構成はデフォルトではないため違反側に倒す
        ("svv_schema_privileges", "namespace_name"),
        ("svv_database_privileges", "database_name"),
    ]
    for view, obj_col in queries:
        # 許可リスト外の行だけを SQL 側で絞り込む。返ってきた行 = 違反なので、
        # LIMIT は「報告する違反の上限」にしかならず、権限行が何行あっても取りこぼさない
        _, rows = ctx.execute(
            f"SELECT {obj_col}, privilege_type, identity_name FROM {view} "
            f"WHERE identity_type = 'group' AND identity_name IN ({group_list}) "
            f"AND UPPER(privilege_type) NOT IN {_allowed_privileges_sql()} "
            "LIMIT 100"
        )
        for row in rows:
            obj, priv, grp = str(row[0]), str(row[1] or "").upper(), str(row[2])
            violations.append(
                f"group '{grp}' (member: '{username}') has {priv} on {obj} [{view}]"
            )
    return violations


def _check_default_privileges(ctx: ValidationContext, username: str) -> list[str]:
    """補助: ALTER DEFAULT PRIVILEGES による将来オブジェクトへの自動付与の検査。

    SVV_DEFAULT_PRIVILEGES は「自分に付与された default privilege」を表示する
    (文書化済み)。SELECT 以外の自動付与があれば違反とする。
    """
    # 許可リスト外の行だけを SQL 側で絞り込む (返ってきた行 = 違反)
    _, rows = ctx.execute(
        "SELECT schema_name, object_type, privilege_type, grantee_name, grantee_type "
        "FROM svv_default_privileges "
        f"WHERE UPPER(privilege_type) NOT IN {_allowed_privileges_sql()} "
        "LIMIT 100"
    )
    violations = []
    for row in rows:
        schema, obj_type, priv = str(row[0]), str(row[1]), str(row[2] or "").upper()
        grantee, gtype = str(row[3]), str(row[4])
        violations.append(
            f"default privilege {priv} on future {obj_type} in schema {schema} "
            f"is granted to {gtype} '{grantee}'"
        )
    return violations


def _check_ownership(ctx: ValidationContext, username: str) -> list[str]:
    """経路 5: 所有権 (owner は GRANT なしで DROP 含む全操作が可能)"""
    violations = []
    _, table_rows = ctx.execute(
        "SELECT n.nspname || '.' || c.relname "
        "FROM pg_class c "
        "JOIN pg_namespace n ON c.relnamespace = n.oid "
        "JOIN pg_user u ON c.relowner = u.usesysid "
        "WHERE u.usename = current_user "
        f"AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname NOT IN {_SYSTEM_SCHEMAS} "
        "LIMIT 20"
    )
    if table_rows:
        objs = ", ".join(str(r[0]) for r in table_rows[:10])
        violations.append(f"user '{username}' owns relations: {objs}")

    _, schema_rows = ctx.execute(
        "SELECT n.nspname FROM pg_namespace n "
        "JOIN pg_user u ON n.nspowner = u.usesysid "
        "WHERE u.usename = current_user "
        f"AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname NOT IN {_SYSTEM_SCHEMAS} "
        "LIMIT 20"
    )
    if schema_rows:
        objs = ", ".join(str(r[0]) for r in schema_rows[:10])
        violations.append(f"user '{username}' owns schemas: {objs}")
    return violations


def _check_public_inherited(ctx: ValidationContext, username: str) -> list[str]:
    """経路 4: PUBLIC 経由の継承を含む実効権限の保険チェック。

    has_table_privilege は PUBLIC への GRANT を含む実効権限を確認できる唯一の手段。
    関数が受け付けるのは SELECT/INSERT/UPDATE/DELETE/DROP/REFERENCES の 6 種で、
    TRUNCATE/ALTER は表現できない (それらは SHOW GRANTS 側で捕捉される)。
    """
    privs = ["insert", "update", "delete", "drop", "references"]
    cond = " OR ".join(
        f"has_table_privilege(current_user, quote_ident(schemaname) || '.' || quote_ident(tablename), '{p}')"
        for p in privs
    )
    _, rows = ctx.execute(
        "SELECT schemaname || '.' || tablename FROM pg_tables "
        f"WHERE schemaname NOT LIKE 'pg\\_%' AND schemaname NOT IN {_SYSTEM_SCHEMAS} "
        f"AND ({cond}) "
        "LIMIT 20"
    )
    if rows:
        objs = ", ".join(str(r[0]) for r in rows[:10])
        return [f"user '{username}' has effective write/drop privilege on: {objs}"]
    return []


def _warn_schema_create(ctx: ValidationContext) -> None:
    """警告のみ: スキーマ CREATE 権限 (Redshift デフォルトで public に付与されている)"""
    _, rows = ctx.execute(
        "SELECT nspname FROM pg_namespace "
        f"WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN {_SYSTEM_SCHEMAS} "
        "AND has_schema_privilege(current_user, nspname, 'create') "
        "LIMIT 20"
    )
    if rows:
        schemas = [str(r[0]) for r in rows]
        logger.warning(
            "readonly validation warning — schema CREATE privilege: %s "
            "(CREATE cannot modify existing data; run "
            "`REVOKE CREATE ON SCHEMA <schema> FROM PUBLIC;` to remove it)",
            schemas,
        )


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def validate_readonly(workgroup_name: str, database: str, secret_arn: str) -> None:
    """readonly でなければ RuntimeError を投げる"""
    ctx = ValidationContext(workgroup_name, database, secret_arn)
    username = _current_user(ctx)
    logger.info("validating user %r (allow-list: %s)", username, sorted(_ALLOWED_PRIVILEGES))

    violations: list[str] = []
    violations += _check_superuser(ctx)
    violations += _check_direct_grants(ctx, username)
    roles, role_violations = _expand_roles(ctx, username)
    violations += role_violations
    violations += _check_role_grants(ctx, roles)
    violations += _check_group_grants(ctx, username)
    violations += _check_default_privileges(ctx, username)
    violations += _check_ownership(ctx, username)
    violations += _check_public_inherited(ctx, username)

    _warn_schema_create(ctx)

    if violations:
        for v in violations:
            logger.error("readonly validation violation — %s", v)
        raise RuntimeError(
            "The provided Redshift DB user is NOT read-only. "
            "Deployment is blocked to protect the existing Redshift data. "
            f"Allowed privileges are {sorted(_ALLOWED_PRIVILEGES)} only. "
            "Violations: " + " / ".join(violations[:20])
        )
    logger.info("readonly validation succeeded for user %r (roles checked: %s)", username, sorted(roles) or "none")


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
