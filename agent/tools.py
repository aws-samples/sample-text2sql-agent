"""
Strands Agents カスタムツール: Redshift Data API + チャート描画 + CSV ダウンロード
"""
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from strands import tool

logger = logging.getLogger(__name__)

redshift_data = boto3.client("redshift-data", region_name=os.environ.get("AWS_REGION"))
s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
dynamodb_resource = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION"))

SQL_RESULT_THRESHOLD = int(os.environ.get("SQL_RESULT_THRESHOLD", "200"))


# ---------------------------------------------------------------------------
# ツール間共有状態
# ---------------------------------------------------------------------------
@dataclass
class ToolSharedState:
    """agent.py の invoke() にチャート / CSV ダウンロード情報を受け渡すための状態"""

    _pending_chart_specs: list[dict] = field(default_factory=list)
    _pending_csv_files: list[dict] = field(default_factory=list)

    def add_chart_spec(self, spec: dict) -> None:
        self._pending_chart_specs.append(spec)

    def pop_chart_specs(self) -> list[dict]:
        """保留中のチャート仕様をすべて取り出す（取り出し後クリア）"""
        specs = list(self._pending_chart_specs)
        self._pending_chart_specs.clear()
        return specs

    def add_csv_file(self, info: dict) -> None:
        self._pending_csv_files.append(info)

    def pop_csv_files(self) -> list[dict]:
        """保留中の CSV ダウンロード情報をすべて取り出す（取り出し後クリア）"""
        files = list(self._pending_csv_files)
        self._pending_csv_files.clear()
        return files


# ---------------------------------------------------------------------------
# UNLOAD 用 SQL バリデーション / 加工
# ---------------------------------------------------------------------------
def _validate_sql_for_unload(sql_query: str) -> None:
    """UNLOAD への注入と複文実行を防ぐためのバリデーション

    対策:
      1. 複文実行 (`;` の後に文字が続く) を拒否
      2. UNLOAD キーワード自体を拒否 (UNLOAD 注入対策)

    コメントは事前に除去してから判定するため、`-- ; DROP ...` のような
    コメント経由の複文も検出できる。
    """
    normalized = sql_query.upper().strip()

    # コメント除去
    normalized = re.sub(r"--.*$", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)

    # 複文 (`;` 後に非空白文字)
    if re.search(r";\s*\S", normalized):
        raise ValueError("Multiple SQL statements are not allowed")

    # UNLOAD 注入 (テーブル名等に UNLOAD_X が含まれていても false positive にならないよう \b で囲む)
    if re.search(r"\bUNLOAD\b", normalized):
        raise ValueError("UNLOAD statements are not allowed in user queries")


def _prepare_query_for_unload(sql_query: str) -> str:
    """UNLOAD ('...') 内に埋め込めるように SQL を加工する

    1. バリデーション
    2. トレイリングセミコロン除去
    3. LIMIT 等のクエリ末尾制約を回避するためサブクエリでラップ
    4. シングルクォートをエスケープ (UNLOAD 文字列リテラルの破壊防止)
    """
    _validate_sql_for_unload(sql_query)

    clean = sql_query.strip().rstrip(";")
    wrapped = f"SELECT * FROM ({clean}) subq"
    escaped = wrapped.replace("'", "''")
    return escaped


def _format_iam_role_clause(role_value: str) -> str:
    """UNLOAD ... IAM_ROLE 句に埋め込む文字列を整形する。

    - "default" の場合 → そのまま `IAM_ROLE default`
    - ARN (arn:aws:iam::...) の場合 → `IAM_ROLE 'arn:aws:iam::...'` (要シングルクォート)

    ARN 文字列に `'` が含まれる事は無いが、念のため不正値はバリデーションで弾く。
    """
    role_value = role_value.strip()
    if role_value == "default":
        return "IAM_ROLE default"
    if not role_value.startswith("arn:"):
        # No Silent Fallback: 想定外の値が来たら raise する。
        # (default でもなく ARN 形式でもない値はそもそも UNLOAD で受理されない)
        raise ValueError(f"Invalid IAM role value for UNLOAD: {role_value!r}")
    if "'" in role_value or "\n" in role_value:
        raise ValueError(f"Invalid characters in IAM role ARN: {role_value!r}")
    return f"IAM_ROLE '{role_value}'"


# ---------------------------------------------------------------------------
# ツールファクトリ
# ---------------------------------------------------------------------------
def create_tools(
    workgroup_name: str,
    database: str,
    secret_arn: str,
    user_id: str,
    download_bucket: str,
    files_table_name: str,
    presign_ttl_seconds: int,
    redshift_unload_iam_role: str,
    enable_csv_download: bool = True,
) -> tuple[list, ToolSharedState]:
    """全ツールと共有状態を生成して返す

    Args:
        workgroup_name: Redshift Workgroup 名
        database: Redshift データベース名
        secret_arn: agent_readonly 接続情報の Secrets Manager ARN
        user_id: 実行ユーザーの Cognito sub。CSV ファイルの所有者として使う
        download_bucket: CSV ダウンロード用 S3 バケット
        files_table_name: ファイルメタデータを保存する DynamoDB テーブル名
        presign_ttl_seconds: presigned URL の有効期限秒
        redshift_unload_iam_role: UNLOAD ... IAM_ROLE に渡す値 ("default" または ARN)
        enable_csv_download: False の場合 _create_csv_file ツールを登録しない
            (existingRedshift モード。UNLOAD 用 IAM Role を既存 Namespace に
            関連付けられないため機能ごと無効化する)
    """

    state = ToolSharedState()
    files_table = dynamodb_resource.Table(files_table_name)

    # IAM_ROLE 句は SQL 組み立て前に整形・検証する (毎回再生成する必要はない)
    # CSV ダウンロード無効時は IAM Role 自体が存在しないため整形しない
    iam_role_clause = _format_iam_role_clause(redshift_unload_iam_role) if enable_csv_download else ""

    @tool
    def _redshift_query(sql_query: str, description: str) -> str:
        """Redshift Serverless で SQL クエリを実行し、結果を返す。

        Args:
            sql_query: 実行する SQL クエリ
            description: このクエリで何をしているかの簡潔な説明。ユーザーに表示される。例: "商品カテゴリごとの売上を集計しています", "月別の注文件数を取得しています", "ユーザーごとのアクセス数を集計しています"
        """
        t0 = time.time()
        try:
            logger.info("redshift_query: %s", sql_query)

            response = redshift_data.execute_statement(
                WorkgroupName=workgroup_name,
                Database=database,
                SecretArn=secret_arn,
                Sql=sql_query,
            )
            statement_id = response["Id"]

            status = _wait_for_completion(statement_id)
            if status != "FINISHED":
                desc = redshift_data.describe_statement(Id=statement_id)
                error = desc.get("Error", "Unknown error")
                return f"クエリ失敗 (status={status}): {error}"

            result = _fetch_result(statement_id)
            formatted = _format_result(sql_query, result, csv_download_enabled=enable_csv_download)
            return formatted

        except Exception as e:
            logger.error("redshift_query error: %s", e)
            return f"クエリ実行エラー: {str(e)}"
        finally:
            logger.info("[_redshift_query] elapsed=%.2fs", time.time() - t0)

    @tool
    def _render_chart(
        sql_query: str, chart_type: str, title: str, x_key: str, y_keys: list[str],
    ) -> str:
        """SQL クエリを実行し、その結果をチャートとして可視化してユーザーに表示する。
        _redshift_query で取得済みのデータをチャートにしたい場合、同じ SQL を sql_query に指定すること。
        ユーザーから明示的にグラフを依頼された時のみこのツールを呼ぶ。そうでない場合の SQL 実行は _redshift_query を使うこと。なぜならこの _render_chart は分析結果をAgentに返さないため。このツールを呼ぶ前にかならず _redshift_query を呼び、分析してから呼ぶこと。
        また、グラフの描画の色などの見た目ここからはわからないため、ユーザーに教えないこと。

        Args:
            sql_query: 実行する SQL クエリ。_redshift_query で使用した SQL をそのまま指定する
            chart_type: グラフの種類。"bar"（棒グラフ、カテゴリ間の比較に適する）, "line"（折れ線グラフ、時系列の推移に適する）, "pie"（円グラフ、構成比率の表示に適する）のいずれか
            title: グラフのタイトル。例: "カテゴリ別売上", "月別注文件数の推移", "地域別売上構成比"
            x_key: X軸に使用するカラム名。pie の場合は各スライスの名前（ラベル）として使用される。_redshift_query の結果に含まれるカラム名を正確に指定すること
            y_keys: Y軸に使用するカラム名のリスト。複数指定で複数系列を重ねて表示できる。pie の場合は先頭の1つのみ使用される。数値型のカラムを指定すること
        """
        # SQL 実行
        t0 = time.time()
        try:
            logger.info("render_chart query: %s", sql_query)
            response = redshift_data.execute_statement(
                WorkgroupName=workgroup_name,
                Database=database,
                SecretArn=secret_arn,
                Sql=sql_query,
            )
            statement_id = response["Id"]

            status = _wait_for_completion(statement_id)
            if status != "FINISHED":
                desc = redshift_data.describe_statement(Id=statement_id)
                error = desc.get("Error", "Unknown error")
                return f"チャート用クエリ失敗 (status={status}): {error}"

            result = _fetch_result(statement_id)
        except Exception as e:
            logger.error("render_chart query error: %s", e)
            return f"チャート用クエリ実行エラー: {str(e)}"
        finally:
            logger.info("[_render_chart] elapsed=%.2fs", time.time() - t0)

        # 構造化データ抽出
        columns = [col["name"] for col in result["ColumnMetadata"]]
        records = result["Records"]

        if not records:
            return "エラー: クエリ結果が0件のためチャートを生成できません。"

        if x_key not in columns:
            return f"エラー: カラム '{x_key}' は結果に存在しません。利用可能なカラム: {columns}"

        missing = [k for k in y_keys if k not in columns]
        if missing:
            return f"エラー: カラム {missing} は結果に存在しません。利用可能なカラム: {columns}"

        max_rows = min(SQL_RESULT_THRESHOLD, len(records))
        chart_data = []
        for record in records[:max_rows]:
            row = {col: _extract_field_value(f) for col, f in zip(columns, record)}
            entry = {x_key: row.get(x_key)}
            valid = False
            for yk in y_keys:
                val = row.get(yk)
                try:
                    entry[yk] = Decimal(str(val)) if val is not None else None
                    if val is not None:
                        valid = True
                except (ValueError, TypeError, ArithmeticError):
                    entry[yk] = None
            if valid:
                chart_data.append(entry)

        if not chart_data:
            return f"エラー: カラム {y_keys} に数値データがありません。数値型のカラムを指定してください。利用可能なカラム: {columns}"

        chart_spec = {
            "type": chart_type,
            "title": title,
            "xKey": x_key,
            "yKeys": y_keys,
            "data": chart_data,
        }
        state.add_chart_spec(chart_spec)
        logger.info("[_render_chart] chart_spec added: %s", json.dumps(chart_spec, ensure_ascii=False, default=str)[:200])
        # 戻り値に chart_spec を含める（Strands messages の toolResult に残り、セッション復元時に利用される）
        def _decimal_serializer(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        return json.dumps({"status": "ok", "chart_spec": chart_spec}, ensure_ascii=False, default=_decimal_serializer)

    @tool
    def _create_csv_file(sql_query: str, description: str) -> str:
        """SQL の結果を CSV ファイル (gzip 圧縮) として S3 に書き出し、ユーザーがダウンロードできるようにする。

        使用するタイミング:
          - クエリ結果が _redshift_query の閾値を超えた、または超える見込みの場合
          - ユーザーが明示的に CSV ダウンロードを要求した場合
        呼び出し後はユーザーに「ダウンロードリンクを生成しました」と伝えるだけでよい。
        URL や file_id は LLM への戻り値に含まれないので言及しないこと。

        Args:
            sql_query: 結果を CSV 化したい SELECT 文
            description: 何のためのファイルかをユーザー向けに簡潔に表す説明。例: "2024年の月別売上データ", "全顧客リスト"
        """
        t0 = time.time()
        try:
            # 1. バリデーション + UNLOAD 用に整形
            prepared = _prepare_query_for_unload(sql_query)

            # 2. UNLOAD 実行
            file_id = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            s3_prefix = f"csv/{user_id}/{file_id}/{timestamp}"
            s3_path = f"s3://{download_bucket}/{s3_prefix}/"
            filename = f"query_results_{timestamp}.csv.gz"

            unload_sql = (
                f"UNLOAD ('{prepared}')\n"
                f"TO '{s3_path}'\n"
                f"{iam_role_clause}\n"
                f"CSV HEADER GZIP ALLOWOVERWRITE PARALLEL OFF"
            )
            logger.info("[_create_csv_file] UNLOAD start: file_id=%s prefix=%s", file_id, s3_prefix)

            response = redshift_data.execute_statement(
                WorkgroupName=workgroup_name,
                Database=database,
                SecretArn=secret_arn,
                Sql=unload_sql,
            )
            statement_id = response["Id"]

            status = _wait_for_completion(statement_id)
            if status != "FINISHED":
                desc = redshift_data.describe_statement(Id=statement_id)
                error = desc.get("Error", "Unknown error")
                # No Silent Fallback: 失敗は LLM に明示的に返す (raise はせず文字列で返す)
                logger.error("[_create_csv_file] UNLOAD failed: status=%s error=%s", status, error)
                return f"CSV ファイル作成失敗 (status={status}): {error}"

            # 3. UNLOAD で書かれたオブジェクトを確認 (空でも 1 ファイルは出る前提)
            objects = s3_client.list_objects_v2(Bucket=download_bucket, Prefix=s3_prefix)
            contents = objects.get("Contents") or []
            if not contents:
                logger.error("[_create_csv_file] UNLOAD wrote no files: prefix=%s", s3_prefix)
                return "CSV ファイル作成失敗: UNLOAD はファイルを書き出しませんでした。"

            # 4. メタデータ保存 (ユーザー越境防止のため (user_id, file_id) 複合キー)
            expires_at = int(time.time()) + presign_ttl_seconds
            files_table.put_item(Item={
                "user_id": user_id,
                "file_id": file_id,
                "s3_prefix": s3_prefix,
                "filename": filename,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at,
                # 監査用に元 SQL の冒頭を残す
                "sql_query": sql_query[:500],
            })

            # 5. presigned URL を発行 (ストリーミングで返すため Runtime 内で生成)
            key = contents[0]["Key"]
            url = s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": download_bucket,
                    "Key": key,
                    "ResponseContentDisposition": f'attachment; filename="{filename}"',
                },
                ExpiresIn=presign_ttl_seconds,
            )

            # 6. SSE 用に共有状態へ登録 (LLM の戻り値には URL を含めない)
            state.add_csv_file({
                "file_id": file_id,
                "filename": filename,
                "url": url,
                "expires_at": expires_at,
                "description": description,
            })

            logger.info(
                "[_create_csv_file] success: file_id=%s rows=? key=%s",
                file_id, key,
            )

            # 7. LLM への戻り値は短い確認文字列のみ (URL は隠す)
            #    Lambda プロキシの _strands_to_display_messages がこの戻り値から file_id を抽出し
            #    セッション復元時に presigned URL を再発行する。
            return f"CSV file created. file_id={file_id}"

        except ValueError as e:
            # バリデーション失敗 (UNLOAD 注入 / 複文等)
            logger.warning("[_create_csv_file] validation failed: %s", e)
            return f"CSV ファイル作成失敗: {str(e)}"
        except Exception as e:
            logger.exception("[_create_csv_file] unexpected error: %s", e)
            return f"CSV ファイル作成エラー: {str(e)}"
        finally:
            logger.info("[_create_csv_file] elapsed=%.2fs", time.time() - t0)

    tools = [_redshift_query, _render_chart]
    if enable_csv_download:
        tools.append(_create_csv_file)
    return tools, state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _wait_for_completion(statement_id: str, max_wait: int = 120) -> str:
    """Redshift Data API のステートメント完了を待機"""
    elapsed = 0
    while elapsed < max_wait:
        desc = redshift_data.describe_statement(Id=statement_id)
        status = desc["Status"]
        if status in ("FINISHED", "FAILED", "ABORTED"):
            return status
        time.sleep(1)
        elapsed += 1
    return "TIMEOUT"


def _fetch_result(statement_id: str) -> dict:
    """Redshift Data API の結果を閾値を考慮してページネーション取得する。"""
    first_page = redshift_data.get_statement_result(Id=statement_id)
    total = first_page["TotalNumRows"]
    column_metadata = first_page["ColumnMetadata"]
    records = list(first_page.get("Records", []))
    next_token = first_page.get("NextToken")

    while next_token and len(records) <= SQL_RESULT_THRESHOLD:
        page = redshift_data.get_statement_result(
            Id=statement_id, NextToken=next_token,
        )
        records.extend(page.get("Records", []))
        next_token = page.get("NextToken")

    truncated = total > SQL_RESULT_THRESHOLD

    return {
        "ColumnMetadata": column_metadata,
        "Records": records,
        "TotalNumRows": total,
        "Truncated": truncated,
    }


def _extract_field_value(field: dict):
    """Redshift Data API のフィールドから型を保持した値を取り出す"""
    if "stringValue" in field:
        return field["stringValue"]
    if "longValue" in field:
        return field["longValue"]
    if "doubleValue" in field:
        return field["doubleValue"]
    if "booleanValue" in field:
        return field["booleanValue"]
    if "isNull" in field and field["isNull"]:
        return None
    return None


def _format_result(sql_query: str, result: dict, csv_download_enabled: bool = True) -> str:
    """Redshift Data API の結果をテキストにフォーマット"""
    columns = [col["name"] for col in result["ColumnMetadata"]]
    records = result["Records"]
    total = result["TotalNumRows"]

    if not records:
        return f"SQL: {sql_query}\nクエリは成功しましたが、結果は0件です。"

    truncated = result["Truncated"]
    display_rows = SQL_RESULT_THRESHOLD if truncated else len(records)

    lines = [f"SQL: {sql_query}", f"結果: {total} 行"]
    lines.append(" | ".join(columns))
    lines.append("-" * len(lines[-1]))

    for row in records[:display_rows]:
        row_values = [str(_extract_field_value(f)) if _extract_field_value(f) is not None else "NULL" for f in row]
        lines.append(" | ".join(row_values))

    if truncated:
        if csv_download_enabled:
            lines.append(
                f"\n注意: クエリ結果は全{total}件ですが、先頭{SQL_RESULT_THRESHOLD}件のみ返しています。"
                "全件が必要な場合はユーザーに必ず確認したうえで、_create_csv_file ツールを使って CSV ダウンロードリンクを提供するか、"
                "WHERE句やLIMIT句で結果を絞り込んだり、GROUP BYして集計してください。"
            )
        else:
            # CSV ダウンロード機能が無効な構成では CSV エクスポートに言及しない
            lines.append(
                f"\n注意: クエリ結果は全{total}件ですが、先頭{SQL_RESULT_THRESHOLD}件のみ返しています。"
                "この環境では CSV エクスポート機能は提供されていないため、ユーザーに CSV エクスポートを提案しないでください。"
                "WHERE句やLIMIT句で結果を絞り込んだり、GROUP BYして集計することを提案してください。"
            )

    return "\n".join(lines)
