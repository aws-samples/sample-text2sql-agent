"""
Strands Agents カスタムツール: Redshift Data API + チャート描画
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal

import boto3
from strands import tool

logger = logging.getLogger(__name__)

redshift_data = boto3.client("redshift-data", region_name=os.environ.get("AWS_REGION"))

SQL_RESULT_THRESHOLD = int(os.environ.get("SQL_RESULT_THRESHOLD", "200"))


# ---------------------------------------------------------------------------
# ツール間共有状態
# ---------------------------------------------------------------------------
@dataclass
class ToolSharedState:
    """agent.py の invoke() にチャート仕様を受け渡すための状態"""

    _pending_chart_specs: list[dict] = field(default_factory=list)

    def add_chart_spec(self, spec: dict) -> None:
        self._pending_chart_specs.append(spec)

    def pop_chart_specs(self) -> list[dict]:
        """保留中のチャート仕様をすべて取り出す（取り出し後クリア）"""
        specs = list(self._pending_chart_specs)
        self._pending_chart_specs.clear()
        return specs


# ---------------------------------------------------------------------------
# ツールファクトリ
# ---------------------------------------------------------------------------
def create_tools(
    workgroup_name: str, database: str, secret_arn: str,
) -> tuple[list, ToolSharedState]:
    """全ツールと共有状態を生成して返す"""

    state = ToolSharedState()

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
            formatted = _format_result(sql_query, result)
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

    return [_redshift_query, _render_chart], state


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


def _format_result(sql_query: str, result: dict) -> str:
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
        lines.append(
            f"\n注意: クエリ結果は全{total}件ですが、先頭{SQL_RESULT_THRESHOLD}件のみ返しています。"
            "全件が必要な場合はユーザーに必ず確認したうえで、WHERE句やLIMIT句で結果を絞り込んだり、GROUP BYして集計してください。"
        )

    return "\n".join(lines)
