import codecs
import csv
import io
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

ALLOW_ORIGIN = os.environ["ALLOW_ORIGIN"]
CONFIG_TABLE_NAME = os.environ["CONFIG_TABLE_NAME"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
CSV_BUCKET_NAME = os.environ["CSV_BUCKET_NAME"]
INIT_WORKFLOW_STATE_MACHINE_ARN = os.environ["INIT_WORKFLOW_STATE_MACHINE_ARN"]

dynamodb = boto3.resource("dynamodb")
config_table = dynamodb.Table(CONFIG_TABLE_NAME)

s3 = boto3.client(
    "s3",
    region_name=os.environ["AWS_REGION"],
    config=BotoConfig(signature_version="v4", s3={"addressing_style": "path"}),
)
sfn_client = boto3.client("stepfunctions")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOW_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
def get_user_id_from_request(request: Request) -> str:
    context_header = request.headers.get("x-amzn-request-context", "")
    if context_header:
        try:
            context = json.loads(context_header)
            sub = context.get("authorizer", {}).get("claims", {}).get("sub")
            if sub:
                return sub
        except (json.JSONDecodeError, AttributeError):
            pass
    raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------
class CreateAgentRequest(BaseModel):
    agent_name: str
    system_prompt: str | None = None


@app.get("/admin/agents")
async def list_agents(request: Request):
    get_user_id_from_request(request)
    response = config_table.scan()
    agents = [
        {
            "agent_id": item["id"],
            "agent_name": item.get("agent_name", ""),
            "system_prompt": item.get("system_prompt", "")[:100],
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
        }
        for item in response.get("Items", [])
    ]
    agents.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return JSONResponse(content={"agents": agents})


@app.post("/admin/agents")
async def create_agent(request: Request, body: CreateAgentRequest):
    get_user_id_from_request(request)
    agent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    config_table.put_item(Item={
        "id": agent_id,
        "agent_name": body.agent_name,
        "system_prompt": body.system_prompt or "",
        "db_schema": "",
        "skills": [],
        "created_at": now,
        "updated_at": now,
    })
    return JSONResponse(content={"agent_id": agent_id})


@app.get("/admin/agents/{agent_id}")
async def get_agent(request: Request, agent_id: str):
    get_user_id_from_request(request)
    response = config_table.get_item(Key={"id": agent_id})
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Agent not found")
    return JSONResponse(content={
        "agent_id": item["id"],
        "agent_name": item.get("agent_name", ""),
        "system_prompt": item.get("system_prompt", ""),
        "db_schema": item.get("db_schema", ""),
        "skills": item.get("skills", []),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    })


class UpdateAgentRequest(BaseModel):
    agent_name: str | None = None
    system_prompt: str | None = None


@app.put("/admin/agents/{agent_id}")
async def update_agent(request: Request, agent_id: str, body: UpdateAgentRequest):
    get_user_id_from_request(request)
    response = config_table.get_item(Key={"id": agent_id})
    if not response.get("Item"):
        raise HTTPException(status_code=404, detail="Agent not found")

    update_parts = []
    expr_values = {}
    if body.agent_name is not None:
        update_parts.append("agent_name = :an")
        expr_values[":an"] = body.agent_name
    if body.system_prompt is not None:
        update_parts.append("system_prompt = :sp")
        expr_values[":sp"] = body.system_prompt

    now = datetime.now(timezone.utc).isoformat()
    update_parts.append("updated_at = :ua")
    expr_values[":ua"] = now

    config_table.update_item(
        Key={"id": agent_id},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeValues=expr_values,
    )
    return JSONResponse(content={"status": "updated"})


@app.delete("/admin/agents/{agent_id}")
async def delete_agent(request: Request, agent_id: str):
    get_user_id_from_request(request)
    config_table.delete_item(Key={"id": agent_id})
    return JSONResponse(content={"status": "deleted"})


# ---------------------------------------------------------------------------
# Presigned URLs (Step 1 - Mode A)
# ---------------------------------------------------------------------------
class PresignedUrlsRequest(BaseModel):
    filenames: list[str]


@app.post("/admin/presigned-urls")
async def presigned_urls(request: Request, body: PresignedUrlsRequest):
    get_user_id_from_request(request)
    logger.info("POST /admin/presigned-urls: filenames=%s", body.filenames)

    prefix = f"uploads/{datetime.now().strftime('%Y%m%d%H%M%S')}/"
    urls: dict[str, str] = {}
    for filename in body.filenames:
        key = f"{prefix}{filename}"
        url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": CSV_BUCKET_NAME,
                "Key": key,
                "ContentType": "application/octet-stream",
            },
            ExpiresIn=8 * 3600,  # 8 hours
        )
        urls[filename] = url

    return JSONResponse(content={"prefix": prefix, "urls": urls})


# ---------------------------------------------------------------------------
# List CSV (Step 1 - Mode B)
# ---------------------------------------------------------------------------
class ListCsvRequest(BaseModel):
    prefix: str


@app.post("/admin/list-csv")
async def list_csv(request: Request, body: ListCsvRequest):
    get_user_id_from_request(request)
    prefix = body.prefix.lstrip("/")
    logger.info("POST /admin/list-csv: prefix=%s", prefix)

    # analyze と同じルート（再帰列挙 + 末尾 / 正規化）で CSV と manifest を列挙する
    csv_keys = _list_csv_files(prefix)
    manifest_keys = _list_manifest_files(prefix)

    normalized = _normalize_prefix(prefix)

    # 画面表示用にキー先頭のプレフィックスを除去し、相対パスにする
    def _to_relative(key: str) -> str:
        return key[len(normalized):] if normalized and key.startswith(normalized) else key

    files = [_to_relative(k) for k in csv_keys]
    manifests = [_to_relative(k) for k in manifest_keys]

    return JSONResponse(content={
        "prefix": prefix,
        "files": files,
        "manifests": manifests,
    })


# ---------------------------------------------------------------------------
# Analyze (Step 2)
# ---------------------------------------------------------------------------
bedrock_runtime = boto3.client("bedrock-runtime")


def _detect_encoding(bucket: str, key: str) -> None:
    """S3 の CSV ファイル先頭を読み取って UTF-8 であることを検証。UTF-8 以外はエラー。"""
    resp = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-8192")
    raw = resp["Body"].read()
    # Range GET の末尾がマルチバイト文字の途中で切れる可能性があるため、
    # incremental decoder を使って「末尾の不完全バイト列」を許容する。
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        decoder.decode(raw, final=False)
    except UnicodeDecodeError:
        raise ValueError(f"UTF-8 以外のエンコーディングが検出されました: {key}。CSV は UTF-8 で保存してください。")


def _read_head_lines(bucket: str, key: str, max_bytes: int = 524288) -> str:
    """S3 から先頭 max_bytes を Range GET で取得してデコード"""
    resp = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max_bytes - 1}")
    return resp["Body"].read().decode("utf-8", errors="replace")


def _read_tail_lines(bucket: str, key: str, max_bytes: int = 524288) -> str:
    """S3 から末尾 max_bytes を Range GET で取得してデコード"""
    # ファイルサイズ取得
    head_resp = s3.head_object(Bucket=bucket, Key=key)
    file_size = head_resp["ContentLength"]
    if file_size <= max_bytes:
        return ""  # 小さいファイルは先頭だけで十分
    start = file_size - max_bytes
    resp = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes={start}-{file_size - 1}")
    return resp["Body"].read().decode("utf-8", errors="replace")


def _get_row_count(bucket: str, key: str) -> int | None:
    """S3 Select で行数を取得（CSV ヘッダー除く）"""
    try:
        resp = s3.select_object_content(
            Bucket=bucket,
            Key=key,
            ExpressionType="SQL",
            Expression="SELECT COUNT(*) FROM s3object",
            InputSerialization={"CSV": {"FileHeaderInfo": "USE"}, "CompressionType": "NONE"},
            OutputSerialization={"JSON": {}},
        )
        for event in resp["Payload"]:
            if "Records" in event:
                data = event["Records"]["Payload"].decode("utf-8").strip()
                if data:
                    row = json.loads(data)
                    return int(row.get("_1", 0))
    except Exception as e:
        logger.warning("S3 Select COUNT failed for %s: %s", key, e)
    return None


def _read_middle_lines(bucket: str, key: str, max_bytes: int = 524288) -> str:
    """S3 からファイル中程の max_bytes を Range GET で取得してデコード"""
    head_resp = s3.head_object(Bucket=bucket, Key=key)
    file_size = head_resp["ContentLength"]
    if file_size <= max_bytes * 2:
        return ""  # 小さいファイルは先頭+末尾で十分
    mid_start = (file_size // 2) - (max_bytes // 2)
    mid_end = mid_start + max_bytes - 1
    resp = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes={mid_start}-{mid_end}")
    return resp["Body"].read().decode("utf-8", errors="replace")


def _analyze_csv_file(bucket: str, key: str) -> dict:
    """S3 の CSV を Range GET で先頭・中程・末尾のみ取得して分析する。
    GB 級ファイルでもメモリを消費しない。"""

    try:
        _detect_encoding(bucket, key)

        # 先頭を取得
        head_text = _read_head_lines(bucket, key)

        # 区切り文字の推定
        delimiter = ","
        try:
            sample = "\n".join(head_text.split("\n")[:5])
            dialect = csv.Sniffer().sniff(sample)
            delimiter = dialect.delimiter
        except csv.Error:
            pass

        # 先頭パース
        head_reader = csv.reader(io.StringIO(head_text), delimiter=delimiter)
        head_all = list(head_reader)
        if not head_all:
            return {"error": "空のCSVファイルです", "key": key}

        headers = head_all[0]
        head_rows = head_all[1:21]  # 先頭20行

        # 中程を取得
        mid_text = _read_middle_lines(bucket, key)
        mid_rows: list[list[str]] = []
        if mid_text:
            mid_lines = mid_text.split("\n")
            # 先頭・末尾の不完全行を除去
            if mid_lines:
                mid_lines = mid_lines[1:]
            while mid_lines and not mid_lines[-1].strip():
                mid_lines.pop()
            if mid_lines:
                mid_reader = csv.reader(io.StringIO("\n".join(mid_lines)), delimiter=delimiter)
                all_mid = list(mid_reader)
                # 中央付近から20行を抽出
                center = len(all_mid) // 2
                start = max(0, center - 10)
                mid_rows = all_mid[start:start + 20]

        # 末尾を取得
        tail_text = _read_tail_lines(bucket, key)
        tail_rows: list[list[str]] = []
        if tail_text:
            tail_lines = tail_text.split("\n")
            # 先頭の不完全行を除去
            if tail_lines:
                tail_lines = tail_lines[1:]
            # 末尾の空行を除去
            while tail_lines and not tail_lines[-1].strip():
                tail_lines.pop()
            tail_reader = csv.reader(io.StringIO("\n".join(tail_lines)), delimiter=delimiter)
            all_tail = list(tail_reader)
            tail_rows = all_tail[-20:] if all_tail else []

        def rows_to_text(rows: list[list[str]]) -> str:
            return "\n".join([delimiter.join(row) for row in rows])

        return {
            "key": key,
            "detected_delimiter": delimiter,
            "headers": headers,
            "head_sample": rows_to_text(head_rows),
            "mid_sample": rows_to_text(mid_rows) if mid_rows else "(小さいファイルのため省略)",
            "tail_sample": rows_to_text(tail_rows) if tail_rows else "(小さいファイルのため省略)",
        }
    except Exception as e:
        return {"error": f"CSV分析エラー: {str(e)}", "key": key}


def _normalize_prefix(prefix: str) -> str:
    """S3 prefix を列挙用に正規化する（末尾に `/` を強制）。

    list_objects_v2 は Prefix に完全一致の文字列前方一致を行うため、末尾に `/` を
    付けないと例えば `sales` 指定時に `sales_2024/...` まで巻き込んでしまう。
    空プレフィックス（バケット全体）の指定は許容する。
    """
    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else prefix + "/"


def _list_csv_files(prefix: str) -> list[str]:
    """S3 prefix 配下を再帰的に列挙し、.csv ファイルキーを返す。

    サブディレクトリ配下も含めて返す。例: prefix/sub/a.csv も検知される。
    """
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    normalized = _normalize_prefix(prefix)
    for page in paginator.paginate(Bucket=CSV_BUCKET_NAME, Prefix=normalized):
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            if key.lower().endswith(".csv"):
                keys.append(key)
    return keys


def _list_manifest_files(prefix: str) -> list[str]:
    """S3 prefix 配下を再帰的に列挙し、.manifest ファイルキーを返す。"""
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    normalized = _normalize_prefix(prefix)
    for page in paginator.paginate(Bucket=CSV_BUCKET_NAME, Prefix=normalized):
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            if key.lower().endswith(".manifest"):
                keys.append(key)
    return keys


class ManifestValidationError(Exception):
    """manifest 検証エラー（entries に壊れた参照がある場合）"""


def _read_manifest_entries(
    bucket: str,
    key: str,
    allowed_keys: set[str],
    normalized_prefix: str,
) -> list[str]:
    """Redshift COPY MANIFEST を読み、entries の url から s3 キーのリストを返す。

    manifest 形式: {"entries": [{"url": "s3://bucket/key", "mandatory": true}, ...]}

    厳格検証（いずれかに該当したら ManifestValidationError を投げる）:
      1. `s3://<bucket>/` 以外で始まる url（別バケット参照 / 相対パス）
      2. 指定 prefix 配下でない key（列挙範囲外）
      3. prefix 配下だが `list_objects_v2` で列挙されなかった key（実在しない）

    url はパーセントエンコーディング（%20 等）を unquote して照合する。
    """
    from urllib.parse import unquote

    resp = s3.get_object(Bucket=bucket, Key=key)
    raw = resp["Body"].read()
    data = json.loads(raw)
    bucket_prefix = f"s3://{bucket}/"

    out: list[str] = []
    invalid_bucket: list[str] = []
    out_of_prefix: list[str] = []
    missing_keys: list[str] = []

    for entry in data.get("entries", []):
        url = entry.get("url", "")
        if not url.startswith(bucket_prefix):
            invalid_bucket.append(url)
            continue
        entry_key = unquote(url[len(bucket_prefix):])
        if normalized_prefix and not entry_key.startswith(normalized_prefix):
            out_of_prefix.append(entry_key)
            continue
        if entry_key not in allowed_keys:
            missing_keys.append(entry_key)
            continue
        out.append(entry_key)

    errors: list[str] = []
    if invalid_bucket:
        errors.append(
            f"CSV バケット外または不正な URL を参照しています ({len(invalid_bucket)} 件): "
            + ", ".join(invalid_bucket[:5])
            + (" ..." if len(invalid_bucket) > 5 else "")
        )
    if out_of_prefix:
        errors.append(
            f"指定 prefix '{normalized_prefix}' の外を参照しています ({len(out_of_prefix)} 件): "
            + ", ".join(out_of_prefix[:5])
            + (" ..." if len(out_of_prefix) > 5 else "")
        )
    if missing_keys:
        errors.append(
            f"実在しない CSV を参照しています ({len(missing_keys)} 件): "
            + ", ".join(missing_keys[:5])
            + (" ..." if len(missing_keys) > 5 else "")
        )
    if errors:
        raise ManifestValidationError(
            f"manifest '{key}' の検証に失敗しました: " + " / ".join(errors)
        )

    return out


def _basename_without_ext(key: str) -> str:
    """S3 キーから拡張子なしの basename を取り出す（テーブル名ヒント用）"""
    filename = key.rsplit("/", 1)[-1]
    dot = filename.rfind(".")
    return filename[:dot] if dot > 0 else filename


TABLE_ANALYZE_SYSTEM_PROMPT = """あなたはデータベース設計の専門家です。
ユーザーから提供される CSV ファイルのヘッダー、先頭・中程・末尾サンプルデータを分析し、Redshift テーブル定義を生成してください。

ルール:
- テーブル名は CSV ファイル名（拡張子除く）をスネークケースにする
- ファイル名が日本語など非アルファベットの場合、テーブル名は意味を英訳してスネークケースにする（例: 売り上げ.csv → sales、顧客一覧.csv → customers）
- 複数ファイルがグルーピングされている場合は、全ファイル名リストから共通部分を読み取り、適切なテーブル名を推定する
  - 例: sales_202401.csv, sales_202402.csv → テーブル名 sales
  - 例: order_items_part1.csv, order_items_part2.csv → テーブル名 order_items
- カラムの型は VARCHAR(4096), BIGINT, DECIMAL(38,10), DATE, TIMESTAMP, BOOLEAN から選択
- 文字列カラムは必ず VARCHAR(4096) を使用すること（サンプルデータだけでは最大長を判定できないため、安全のため固定。Redshift はカラムナー圧縮により実データ長で格納される）
- 整数カラムは必ず BIGINT を使用すること（INTEGER では21億超の値で溢れるリスクがある。Redshift のカラムナー圧縮により実質的なストレージ差は軽微）
- 小数カラムは DECIMAL(38,10) を使用すること（精度不足を防ぐため）
- csv_options の delimiter は検出結果を尊重する
- カラムの description は、わかりにくい場合は架空のデータ例を含めてわかりやすく記述する（例：「顧客ID（例：C001234）」）
- 個人情報は実データから抜き出さず、フォーマットを揃えた架空の例を使用する

DATE / TIMESTAMP カラムの判定ルール:
- サンプルデータ全行で日付・日時として一貫したフォーマットが確認できる場合のみ DATE または TIMESTAMP を使用する
- フォーマットが不統一、または確信が持てない場合は VARCHAR(4096) にすること（COPY 失敗を防ぐため）
- DATE/TIMESTAMP を使う場合、csv_options に dateformat または timeformat を必ず指定する
  - dateformat の例: "YYYY-MM-DD", "YYYY/MM/DD", "YYYYMMDD"
  - timeformat の例: "YYYY-MM-DD HH:MI:SS", "YYYY/MM/DD HH:MI:SS", "YYYY-MM-DD HH24:MI:SS"
  - Redshift の DATEFORMAT/TIMEFORMAT 構文に従うこと（auto は使用しない）
- DATE/TIMESTAMP カラムが存在しない場合、dateformat と timeformat は空文字にする
"""

SYSTEM_PROMPT_GENERATION_PROMPT = """あなたはデータ分析 AI Agent のシステムプロンプト設計の専門家です。
以下のテーブル定義一覧を踏まえ、このデータセット全体を使った分析 Agent に最適なシステムプロンプト（日本語）を生成してください。
データの概要、分析の観点、注意事項を含めてください。

重要: テーブル定義（テーブル名、カラム名、型など）は Agent 実行時に動的にロードされるため、システムプロンプトにテーブル定義の詳細を含める必要はありません。
データセットの概要説明、分析のヒント、業務上の注意点など、テーブル定義だけでは伝わらない情報に集中してください。
"""

# tool schema: 1テーブル分の定義
_TABLE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "table_name": {"type": "string"},
        "description": {"type": "string"},
        "s3_keys": {
            "type": "array",
            "items": {"type": "string"},
            "description": "S3 keys of CSV files to load into this table",
        },
        "csv_options": {
            "type": "object",
            "properties": {
                "delimiter": {"type": "string"},
                "quote_char": {"type": "string"},
                "null_as": {"type": "string"},
                "dateformat": {"type": "string", "description": "DATE カラムのフォーマット（例: YYYY-MM-DD）。該当カラムがなければ空文字"},
                "timeformat": {"type": "string", "description": "TIMESTAMP カラムのフォーマット（例: YYYY-MM-DD HH:MI:SS）。該当カラムがなければ空文字"},
            },
            "required": ["delimiter", "quote_char", "null_as", "dateformat", "timeformat"],
        },
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "type", "description"],
            },
        },
    },
    "required": ["table_name", "description", "s3_keys", "csv_options", "columns"],
}


def _analyze_single_csv_with_bedrock(info: dict, group_keys: list[str], table_name_hint: str | None = None) -> dict:
    """1 つの CSV 分析結果を Bedrock に送り、テーブル定義を取得する。
    group_keys にはグループ内の全 S3 キーを渡す。
    table_name_hint を渡すと、Bedrock に対してテーブル名の命名ヒントとして提示する
    （manifest ベースのグループで manifest の basename をテーブル名に使いたい場合）。"""

    user_message = f"""以下の CSV ファイルを分析してテーブル定義を生成してください。

ファイル: {info['key']}
検出区切り文字: {repr(info['detected_delimiter'])}

ヘッダー: {info['headers']}

先頭20行:
```
{info['head_sample']}
```

中程20行:
```
{info['mid_sample']}
```

末尾20行:
```
{info['tail_sample']}
```"""

    # グループ内の全ファイル名を追加
    if len(group_keys) > 1:
        filenames = "\n".join(f"  - {k.rsplit('/', 1)[-1]}" for k in group_keys)
        user_message += f"\n\nこのテーブルには以下の {len(group_keys)} ファイルがグルーピングされています:\n{filenames}\nファイル名の共通部分からテーブル名を推定してください。"

    # manifest ベースのグループでは basename をテーブル名のヒントとして強く提示する
    if table_name_hint:
        user_message += (
            f"\n\nこのテーブルは manifest '{table_name_hint}.manifest' で定義されています。"
            f"テーブル名は原則 '{table_name_hint}' を採用してください"
            f"（非アルファベットの場合は意味を英訳してスネークケースに変換して構いません）。"
        )

    tool_config = {
        "tools": [
            {
                "toolSpec": {
                    "name": "submit_table",
                    "description": "1テーブル分の定義を提出する",
                    "inputSchema": {"json": _TABLE_TOOL_SCHEMA},
                }
            }
        ],
        "toolChoice": {"tool": {"name": "submit_table"}},
    }

    resp = bedrock_runtime.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": TABLE_ANALYZE_SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        toolConfig=tool_config,
        inferenceConfig={"maxTokens": 8192},
    )

    tool_block = next(
        (b for b in resp["output"]["message"]["content"] if "toolUse" in b),
        None,
    )
    if not tool_block:
        raise RuntimeError(f"AI 分析失敗: {info['key']}")

    return tool_block["toolUse"]["input"]


def _generate_system_prompt_with_bedrock(tables: list[dict]) -> str:
    """全テーブル定義をもとに、データセット全体の system_prompt を生成する"""

    tables_summary = json.dumps(
        [{"table_name": t["table_name"], "description": t["description"],
          "columns": [c["name"] for c in t["columns"]]} for t in tables],
        ensure_ascii=False, indent=2,
    )

    tool_config = {
        "tools": [
            {
                "toolSpec": {
                    "name": "submit_system_prompt",
                    "description": "生成したシステムプロンプトを提出する",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "system_prompt": {
                                    "type": "string",
                                    "description": "AI Agent 用のシステムプロンプト（日本語）",
                                },
                            },
                            "required": ["system_prompt"],
                        }
                    },
                }
            }
        ],
        "toolChoice": {"tool": {"name": "submit_system_prompt"}},
    }

    resp = bedrock_runtime.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT_GENERATION_PROMPT}],
        messages=[{"role": "user", "content": [{"text": f"テーブル定義一覧:\n{tables_summary}"}]}],
        toolConfig=tool_config,
        inferenceConfig={"maxTokens": 16384},
    )

    tool_block = next(
        (b for b in resp["output"]["message"]["content"] if "toolUse" in b),
        None,
    )
    if not tool_block:
        raise RuntimeError("system_prompt の生成に失敗しました")

    return tool_block["toolUse"]["input"]["system_prompt"]


def _get_csv_header(bucket: str, key: str) -> list[str] | None:
    """S3 の CSV ファイルからヘッダー行のみを軽量に取得する（Range GET）"""
    try:
        resp = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-8192")
        raw = resp["Body"].read().decode("utf-8", errors="replace")
        first_line = raw.split("\n", 1)[0].strip()
        if not first_line:
            return None
        # 区切り文字の推定
        delimiter = ","
        try:
            dialect = csv.Sniffer().sniff(first_line)
            delimiter = dialect.delimiter
        except csv.Error:
            pass
        reader = csv.reader(io.StringIO(first_line), delimiter=delimiter)
        headers = next(reader, None)
        return headers
    except Exception as e:
        logger.warning("Failed to read header from %s: %s", key, e)
        return None


def _group_csv_by_header(bucket: str, keys: list[str]) -> list[dict]:
    """ヘッダー行が一致する CSV をグルーピングする。
    Returns: [{"header": [...], "keys": [key1, key2, ...]}, ...]
    """
    groups: dict[str, list[str]] = {}  # header_key -> [keys]
    header_map: dict[str, list[str]] = {}  # header_key -> header list

    for key in keys:
        header = _get_csv_header(bucket, key)
        if header is None:
            # ヘッダー取得失敗は単独グループ
            header_key = f"__error__{key}"
            groups[header_key] = [key]
            header_map[header_key] = []
            continue
        header_key = ",".join(header)
        if header_key not in groups:
            groups[header_key] = []
            header_map[header_key] = header
        groups[header_key].append(key)

    return [{"header": header_map[hk], "keys": gkeys} for hk, gkeys in groups.items()]


def _deduplicate_table_names(tables: list[dict]) -> list[dict]:
    """テーブル名が重複している場合、_1, _2, ... のサフィックスを付与する"""
    seen: dict[str, int] = {}
    for table in tables:
        name = table["table_name"]
        if name in seen:
            seen[name] += 1
            table["table_name"] = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
    return tables


class AnalyzeRequest(BaseModel):
    prefix: str


@app.post("/admin/analyze")
async def analyze(request: Request, body: AnalyzeRequest):
    get_user_id_from_request(request)
    prefix = body.prefix.lstrip("/")
    logger.info("POST /admin/analyze: prefix=%s", prefix)

    # CSV と manifest を並行して列挙（サブディレクトリ含む再帰列挙）
    all_csv_keys = _list_csv_files(prefix)
    manifest_keys = _list_manifest_files(prefix)

    # manifest の entries を厳格検証しつつ解決
    # （entries が CSV バケット外 / prefix 外 / 実在しない場合は即エラー）
    normalized_prefix = _normalize_prefix(prefix)
    all_csv_keys_set = set(all_csv_keys)
    manifest_groups: list[dict] = []  # [{"manifest_key": ..., "entries": [csv_key, ...]}]
    csv_keys_in_manifests: set[str] = set()
    manifest_errors: list[str] = []
    for mkey in manifest_keys:
        try:
            entries = _read_manifest_entries(
                CSV_BUCKET_NAME, mkey, all_csv_keys_set, normalized_prefix,
            )
        except ManifestValidationError as e:
            manifest_errors.append(str(e))
            logger.warning("manifest validation failed: %s", e)
            continue
        except Exception as e:
            manifest_errors.append(f"manifest '{mkey}' の読み取りに失敗しました: {e}")
            logger.warning("manifest %s の読み取り失敗: %s", mkey, e)
            continue
        if not entries:
            manifest_errors.append(f"manifest '{mkey}' の entries が空です")
            logger.warning("manifest %s は空", mkey)
            continue
        manifest_groups.append({"manifest_key": mkey, "entries": entries})
        csv_keys_in_manifests.update(entries)

    if manifest_errors:
        raise HTTPException(
            status_code=400,
            detail="; ".join(manifest_errors),
        )

    # manifest に取り込まれていない CSV のみを通常ルートで扱う
    csv_keys = [k for k in all_csv_keys if k not in csv_keys_in_manifests]

    if not csv_keys and not manifest_groups:
        raise HTTPException(status_code=400, detail="指定された prefix 配下に CSV / manifest ファイルが見つかりません")

    def _sse(event_type: str, data: dict) -> str:
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():
        tables: list[dict] = []
        errors: list[str] = []

        # Phase 0: ヘッダーでグルーピング（manifest 対象外の CSV のみ）
        csv_groups = _group_csv_by_header(CSV_BUCKET_NAME, csv_keys) if csv_keys else []
        total = len(manifest_groups) + len(csv_groups)
        logger.info(
            "analyze: %d csv + %d manifest → %d table candidates",
            len(csv_keys), len(manifest_groups), total,
        )

        idx = 0

        # --- manifest ベースのテーブル ---
        for mg in manifest_groups:
            idx += 1
            manifest_key = mg["manifest_key"]
            entries = mg["entries"]
            representative_key = entries[0]
            filename = manifest_key.rsplit("/", 1)[-1]
            yield _sse("progress", {"step": "analyze_csv", "current": idx, "total": total, "file": filename})

            info = _analyze_csv_file(CSV_BUCKET_NAME, representative_key)
            if "error" in info:
                error_msg = info["error"]
                if "UTF-8 以外" in error_msg:
                    logger.error("analyze: エンコーディングエラー %s: %s", representative_key, error_msg)
                    yield _sse("error", {"message": error_msg})
                    return
                errors.append(f"{manifest_key}: {error_msg}")
                logger.warning("analyze: manifest 参照 CSV 読み取りエラー %s: %s", representative_key, error_msg)
                continue
            try:
                logger.info(
                    "analyze: [%d/%d] manifest=%s (%d entries, rep=%s) を AI 分析中...",
                    idx, total, manifest_key, len(entries), representative_key,
                )
                # manifest の basename をテーブル名ヒントとして渡す
                table_def = _analyze_single_csv_with_bedrock(
                    info, entries, table_name_hint=_basename_without_ext(manifest_key),
                )
                # s3_keys には manifest キー 1 本のみ格納
                # （apply 時に .manifest 拡張子で COPY ... MANIFEST に振り分ける）
                table_def["s3_keys"] = [manifest_key]
                tables.append(table_def)
                logger.info(
                    "analyze: [%d/%d] → テーブル '%s' 完了 (manifest, %d files)",
                    idx, total, table_def.get("table_name", "?"), len(entries),
                )
            except Exception as e:
                errors.append(f"{manifest_key}: {str(e)}")
                logger.warning("analyze: [%d/%d] %s AI 分析失敗: %s", idx, total, manifest_key, e)

        # --- 通常 CSV グループベースのテーブル ---
        for group in csv_groups:
            idx += 1
            group_keys = group["keys"]
            representative_key = group_keys[0]
            filename = representative_key.rsplit("/", 1)[-1]
            yield _sse("progress", {"step": "analyze_csv", "current": idx, "total": total, "file": filename})

            info = _analyze_csv_file(CSV_BUCKET_NAME, representative_key)
            if "error" in info:
                error_msg = info["error"]
                if "UTF-8 以外" in error_msg:
                    logger.error("analyze: エンコーディングエラー %s: %s", representative_key, error_msg)
                    yield _sse("error", {"message": error_msg})
                    return
                errors.append(f"{representative_key}: {error_msg}")
                logger.warning("analyze: CSV 読み取りエラー %s: %s", representative_key, error_msg)
                continue
            try:
                logger.info("analyze: [%d/%d] group(%d files, rep=%s) を AI 分析中...",
                            idx, total, len(group_keys), representative_key)
                table_def = _analyze_single_csv_with_bedrock(info, group_keys)
                # Bedrock が返す s3_keys を上書き（グループ内全ファイル）
                table_def["s3_keys"] = group_keys
                tables.append(table_def)
                logger.info("analyze: [%d/%d] → テーブル '%s' 完了 (%d files)",
                            idx, total, table_def.get("table_name", "?"), len(group_keys))
            except Exception as e:
                errors.append(f"{representative_key}: {str(e)}")
                logger.warning("analyze: [%d/%d] %s AI 分析失敗: %s", idx, total, representative_key, e)

        if not tables:
            detail = "テーブル定義を生成できませんでした"
            if errors:
                detail += f" — {'; '.join(errors)}"
            yield _sse("error", {"message": detail})
            return

        # テーブル名の重複防止
        _deduplicate_table_names(tables)

        # Phase 2: system_prompt 生成
        yield _sse("progress", {"step": "generate_prompt", "current": total, "total": total, "file": ""})
        logger.info("analyze: 全 %d テーブルの分析完了。system_prompt を生成中...", len(tables))
        try:
            system_prompt = _generate_system_prompt_with_bedrock(tables)
        except Exception as e:
            logger.warning("system_prompt generation failed, using fallback: %s", e)
            table_names = ", ".join(t["table_name"] for t in tables)
            system_prompt = f"あなたはデータ分析アシスタントです。以下のテーブルを使って分析してください: {table_names}"

        yield _sse("result", {
            "system_prompt": system_prompt,
            "db_schema": {"tables": tables},
            **({"warnings": errors} if errors else {}),
        })

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Apply (Step 4): DROP → DDL → COPY → agent_readonly → GRANT → DynamoDB
# ---------------------------------------------------------------------------


class ApplyRequest(BaseModel):
    prefix: str
    system_prompt: str
    db_schema: dict
    agent_name: str = "Default Agent"


@app.post("/admin/apply")
async def apply(request: Request, body: ApplyRequest):
    get_user_id_from_request(request)
    prefix = body.prefix.lstrip("/")
    logger.info("POST /admin/apply: prefix=%s, tables=%d",
                prefix, len(body.db_schema.get("tables", [])))

    tables = body.db_schema.get("tables", [])
    if not tables:
        raise HTTPException(status_code=400, detail="テーブル定義がありません")

    # Step Functions 実行開始
    sfn_input = json.dumps({
        "prefix": prefix,
        "system_prompt": body.system_prompt,
        "db_schema": body.db_schema,
        "agent_name": body.agent_name,
    }, ensure_ascii=False)

    resp = sfn_client.start_execution(
        stateMachineArn=INIT_WORKFLOW_STATE_MACHINE_ARN,
        input=sfn_input,
    )

    # フロントにはアカウント ID を含まない実行 ID 部分のみ返す
    execution_arn = resp["executionArn"]
    execution_id = execution_arn.rsplit(":", 1)[-1]
    return JSONResponse(content={"execution_id": execution_id})


class ApplyStatusRequest(BaseModel):
    execution_id: str


@app.post("/admin/apply-status")
async def apply_status(request: Request, body: ApplyStatusRequest):
    get_user_id_from_request(request)

    # ステートマシン ARN + 実行 ID から完全な execution_arn を再構築
    execution_arn = f"{INIT_WORKFLOW_STATE_MACHINE_ARN.replace(':stateMachine:', ':execution:')}:{body.execution_id}"
    resp = sfn_client.describe_execution(executionArn=execution_arn)
    sfn_status = resp["status"]

    if sfn_status == "RUNNING":
        return JSONResponse(content={"status": "running"})

    if sfn_status == "SUCCEEDED":
        output = json.loads(resp.get("output", "{}"))
        return JSONResponse(content={
            "status": output.get("status", "completed"),
            "tables_created": output.get("tables_created", []),
            "errors": output.get("errors", []),
        })

    # FAILED / TIMED_OUT / ABORTED
    cause = resp.get("cause", resp.get("error", "Unknown error"))
    # Lambda が RuntimeError(json) を raise → Step Functions は cause に
    # {"errorMessage": "<JSON文字列>", "errorType": "RuntimeError", ...} を格納する。
    # そのため errorMessage を取り出してから中身をパースする二重パースが必要。
    # TIMED_OUT / ABORTED の場合は errorMessage が存在しないため内側のパースはスキップされる。
    tables_created = []
    errors = [cause]
    load_error_details = []
    try:
        detail = json.loads(cause)
        if isinstance(detail, dict):
            # Step Functions の Lambda エラーラッパーの場合
            error_message = detail.get("errorMessage", "")
            if error_message:
                try:
                    inner = json.loads(error_message)
                    if isinstance(inner, dict):
                        detail = inner
                except (json.JSONDecodeError, TypeError):
                    pass
            tables_created = detail.get("tables_created", [])
            errors = detail.get("errors", [cause])
            load_error_details = detail.get("load_error_details", [])
    except (json.JSONDecodeError, TypeError):
        pass
    return JSONResponse(content={
        "status": "failed",
        "tables_created": tables_created,
        "errors": errors,
        "load_error_details": load_error_details,
    })


# ---------------------------------------------------------------------------
# Config (GET) — 廃止予定、後方互換のため agent_id パラメータ対応
# ---------------------------------------------------------------------------
@app.get("/admin/config")
async def get_config(request: Request, agent_id: str | None = None):
    get_user_id_from_request(request)
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    response = config_table.get_item(Key={"id": agent_id})
    item = response.get("Item", {})
    return JSONResponse(content={
        "system_prompt": item.get("system_prompt", ""),
        "db_schema": item.get("db_schema", ""),
        "skills": item.get("skills", []),
    })


# ---------------------------------------------------------------------------
# Knowledge (POST) — skills のみ更新
# ---------------------------------------------------------------------------
class KnowledgeUpdateRequest(BaseModel):
    agent_id: str
    skills: list[str]


@app.post("/admin/knowledge")
async def update_knowledge(request: Request, body: KnowledgeUpdateRequest):
    get_user_id_from_request(request)
    logger.info("POST /admin/knowledge: agent_id=%s", body.agent_id)

    now = datetime.now(timezone.utc).isoformat()
    config_table.update_item(
        Key={"id": body.agent_id},
        UpdateExpression="SET skills = :sk, updated_at = :ua",
        ExpressionAttributeValues={":sk": body.skills, ":ua": now},
    )
    return JSONResponse(content={"status": "updated"})


# ---------------------------------------------------------------------------
# System Prompt (GET / PUT)
# ---------------------------------------------------------------------------
@app.get("/admin/system-prompt")
async def get_system_prompt(request: Request, agent_id: str | None = None):
    get_user_id_from_request(request)
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    response = config_table.get_item(Key={"id": agent_id})
    item = response.get("Item", {})
    return JSONResponse(content={
        "system_prompt": item.get("system_prompt", ""),
    })


class SystemPromptUpdateRequest(BaseModel):
    agent_id: str
    system_prompt: str


@app.put("/admin/system-prompt")
async def update_system_prompt(request: Request, body: SystemPromptUpdateRequest):
    get_user_id_from_request(request)
    logger.info("PUT /admin/system-prompt: agent_id=%s", body.agent_id)

    now = datetime.now(timezone.utc).isoformat()
    config_table.update_item(
        Key={"id": body.agent_id},
        UpdateExpression="SET system_prompt = :sp, updated_at = :ua",
        ExpressionAttributeValues={":sp": body.system_prompt, ":ua": now},
    )
    return JSONResponse(content={"status": "updated"})
