import json
import logging
import os
import re
import time

import boto3
import boto3.dynamodb.conditions
from botocore.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from utils import convert_decimals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

ALLOW_ORIGIN = os.environ["ALLOW_ORIGIN"]
SESSIONS_TABLE_NAME = os.environ["SESSIONS_TABLE_NAME"]
CONFIG_TABLE_NAME = os.environ["CONFIG_TABLE_NAME"]
FILES_TABLE_NAME = os.environ["FILES_TABLE_NAME"]
AGENTCORE_RUNTIME_ARN = os.environ["AGENTCORE_RUNTIME_ARN"]
# CSV ダウンロード機能が無効な構成 (existingRedshift モード) では未設定
DOWNLOAD_BUCKET_NAME = os.environ.get("DOWNLOAD_BUCKET_NAME", "")
DOWNLOAD_PRESIGN_TTL_SECONDS = int(os.environ.get("DOWNLOAD_PRESIGN_TTL_SECONDS", "3600"))

dynamodb = boto3.resource("dynamodb")
sessions_table = dynamodb.Table(SESSIONS_TABLE_NAME)
config_table = dynamodb.Table(CONFIG_TABLE_NAME)
files_table = dynamodb.Table(FILES_TABLE_NAME)

s3_client = boto3.client("s3")

# AgentCore クライアント（長時間ストリーミング対応）
brconfig = Config(
    read_timeout=600,
    connect_timeout=120,
    retries={"max_attempts": 2, "mode": "adaptive"},
)
agentcore_client = boto3.client("bedrock-agentcore", config=brconfig)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOW_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

# _create_csv_file の戻り値文字列から file_id を抜き出すパターン
_CSV_FILE_ID_PATTERN = re.compile(r"file_id=([0-9a-fA-F-]+)")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def get_user_id_from_request(request: Request) -> str:
    """x-amzn-request-context ヘッダーから Cognito sub を取得"""
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


# ---------------------------------------------------------------------------
# CSV 関連ヘルパー
# ---------------------------------------------------------------------------
def regenerate_presigned_url(user_id: str, file_id: str) -> dict:
    """FilesTable + S3 から presigned URL を再発行する。

    戻り値は呼び出し側が状態を区別できるよう構造化する:
      - {"status": "ready", "url", "filename", "expires_at"}
      - {"status": "not_found"}
      - {"status": "expired", "filename"}
      - {"status": "objects_missing", "filename"}

    No Silent Fallbacks: 「正常な空結果」を返さず、すべて状態フィールドで区別する。
    """
    if not DOWNLOAD_BUCKET_NAME:
        # CSV ダウンロード機能が無効な構成ではファイルが存在し得ない
        return {"status": "not_found"}

    resp = files_table.get_item(Key={"user_id": user_id, "file_id": file_id})
    item = resp.get("Item")
    if not item:
        return {"status": "not_found"}

    filename = item.get("filename", "")

    # 期限切れチェック
    expires_at = int(item.get("expires_at", 0))
    if expires_at < int(time.time()):
        return {"status": "expired", "filename": filename}

    s3_prefix = item.get("s3_prefix", "")
    objects = s3_client.list_objects_v2(Bucket=DOWNLOAD_BUCKET_NAME, Prefix=s3_prefix)
    contents = objects.get("Contents") or []
    if not contents:
        # メタデータは残っているのに S3 のファイルが消えている (ライフサイクル削除等)
        logger.error(
            "regenerate_presigned_url: S3 objects missing for prefix=%s user_id=%s file_id=%s",
            s3_prefix, user_id, file_id,
        )
        return {"status": "objects_missing", "filename": filename}

    key = contents[0]["Key"]
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": DOWNLOAD_BUCKET_NAME,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=DOWNLOAD_PRESIGN_TTL_SECONDS,
    )
    return {
        "status": "ready",
        "url": url,
        "filename": filename,
        "expires_at": expires_at,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str
    message: str
    agent_id: str


@app.get("/")
async def health():
    return {"status": "ok"}


@app.get("/agents")
async def list_agents(request: Request):
    get_user_id_from_request(request)
    response = config_table.scan()
    agents = [
        {
            "agent_id": item["id"],
            "agent_name": item.get("agent_name", ""),
        }
        for item in response.get("Items", [])
    ]
    return JSONResponse(content={"agents": agents})


@app.post("/chat")
async def chat(request: Request, body: ChatRequest):
    """AgentCore Runtime を invoke し、SSE レスポンスをフロントにそのまま中継する。"""
    logger.info("POST /chat start: session_id=%s", body.session_id)
    user_id = get_user_id_from_request(request)

    payload = json.dumps({
        "prompt": body.message,
        "user_id": user_id,
        "agent_id": body.agent_id,
        "title": body.message[:50],
    }).encode("utf-8")

    def generate():
        try:
            response = agentcore_client.invoke_agent_runtime(
                agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
                runtimeSessionId=body.session_id,
                payload=payload,
            )

            content_type = response.get("contentType", "")
            logger.info("AgentCore response contentType: %s", content_type)

            if "text/event-stream" in content_type:
                # SSE ストリーミング: 各行をそのままフロントに中継
                for line in response["response"].iter_lines(chunk_size=64):
                    if line:
                        line_str = line.decode("utf-8")
                        yield f"{line_str}\n\n"
            else:
                # 非ストリーミング（フォールバック）
                logger.warning("Unexpected contentType: %s", content_type)
                body_bytes = response["response"].read()
                yield f"data: {body_bytes.decode('utf-8')}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            logger.exception("AgentCore invoke error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ---------------------------------------------------------------------------
# Sessions API
# ---------------------------------------------------------------------------
@app.get("/sessions")
async def get_sessions(request: Request, agent_id: str | None = None):
    user_id = get_user_id_from_request(request)
    kwargs = {
        "KeyConditionExpression": boto3.dynamodb.conditions.Key("user_id").eq(user_id),
    }
    if agent_id:
        kwargs["FilterExpression"] = boto3.dynamodb.conditions.Attr("agent_id").eq(agent_id)
    response = sessions_table.query(**kwargs)
    items = response.get("Items", [])
    sessions = [
        {
            "session_id": s["session_id"],
            "title": s.get("title", "New Chat"),
            "updated_at": s.get("updated_at", ""),
        }
        for s in items
    ]
    sessions.sort(key=lambda x: x["updated_at"], reverse=True)
    return JSONResponse(content={"sessions": sessions})


def _strands_to_display_messages(agent_messages: list[dict], user_id: str) -> list[dict]:
    """Strands 生メッセージをフロント表示用に変換する。

    `_create_csv_file` の toolUse / toolResult を検出した場合、
    FilesTable から presigned URL を再発行して toolUse エントリに添付する。
    """
    display = []
    tool_use_by_id: dict[str, dict] = {}

    for raw in agent_messages:
        msg = raw.get("message", raw)
        role = msg.get("role")
        content_blocks = msg.get("content", [])

        if role == "assistant":
            texts = []
            tool_uses = []
            for block in content_blocks:
                if "text" in block:
                    texts.append(block["text"])
                elif "toolUse" in block:
                    tu = block["toolUse"]
                    tool_name = tu.get("name", "")
                    tool_input = tu.get("input", {})
                    tool_use_id = tu.get("toolUseId", "")
                    entry = {"tool": tool_name, "input": json.dumps(tool_input, ensure_ascii=False)}

                    if tool_name == "_redshift_query":
                        entry["sql"] = tool_input.get("sql_query", "")
                        entry["description"] = tool_input.get("description", "")
                    elif tool_name == "_create_csv_file":
                        entry["sql"] = tool_input.get("sql_query", "")
                        entry["description"] = tool_input.get("description", "")
                        # csv_status / url は後段の toolResult 処理で埋める
                        entry["csv_status"] = "pending"

                    tool_uses.append(entry)
                    if tool_use_id:
                        tool_use_by_id[tool_use_id] = entry

            if display and display[-1]["role"] == "assistant":
                prev = display[-1]
                if texts:
                    prev["content"] = (prev["content"] + "\n" + "\n".join(texts)).strip()
                prev["tool_uses"].extend(tool_uses)
            else:
                display.append({
                    "role": "assistant",
                    "content": "\n".join(texts),
                    "tool_uses": tool_uses,
                })

        elif role == "user":
            texts = [b["text"] for b in content_blocks if "text" in b]

            for block in content_blocks:
                if "toolResult" in block:
                    tr = block["toolResult"]
                    tool_use_id = tr.get("toolUseId", "")
                    entry = tool_use_by_id.get(tool_use_id)
                    if not entry:
                        continue

                    # toolResult のテキストを連結
                    text_blocks = [c.get("text", "") for c in tr.get("content", []) if "text" in c]
                    full_text = "".join(text_blocks)

                    if entry.get("tool") == "_render_chart":
                        # _render_chart は chart_spec を JSON で返す
                        try:
                            parsed = json.loads(full_text)
                            if "chart_spec" in parsed:
                                entry["chart_spec"] = parsed["chart_spec"]
                        except (json.JSONDecodeError, KeyError):
                            pass

                    elif entry.get("tool") == "_create_csv_file":
                        # 戻り値は "CSV file created. file_id=<uuid>" 形式
                        m = _CSV_FILE_ID_PATTERN.search(full_text)
                        if not m:
                            # ツール内で失敗したケース (戻り値が "CSV ファイル作成失敗..." など)
                            entry["csv_status"] = "failed"
                            entry["error_message"] = full_text[:300]
                            continue
                        file_id = m.group(1)
                        entry["file_id"] = file_id
                        try:
                            result = regenerate_presigned_url(user_id, file_id)
                        except Exception as e:
                            # presigned URL 発行失敗。ユーザー越境ではないので 5xx 相当
                            logger.exception(
                                "regenerate_presigned_url unexpected error: user_id=%s file_id=%s",
                                user_id, file_id,
                            )
                            entry["csv_status"] = "error"
                            entry["error_message"] = str(e)[:300]
                            continue
                        status = result["status"]
                        entry["csv_status"] = status
                        if status == "ready":
                            entry["url"] = result["url"]
                            entry["filename"] = result["filename"]
                            entry["expires_at"] = result["expires_at"]
                        elif status in ("expired", "objects_missing"):
                            entry["filename"] = result.get("filename", "")

            if not texts:
                continue
            display.append({
                "role": "user",
                "content": "\n".join(texts),
            })

    return display


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    user_id = get_user_id_from_request(request)
    resp = sessions_table.get_item(Key={"user_id": user_id, "session_id": session_id})
    item = resp.get("Item")
    if not item:
        return JSONResponse(content={"session_id": session_id, "messages": []})

    raw_messages = item.get("agent_messages", {}).get("default", [])
    display = _strands_to_display_messages(convert_decimals(raw_messages), user_id)

    return JSONResponse(content={
        "session_id": session_id,
        "messages": display,
    })


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    user_id = get_user_id_from_request(request)
    sessions_table.delete_item(Key={"user_id": user_id, "session_id": session_id})
    return JSONResponse(content={"message": "Session deleted", "session_id": session_id})


# ---------------------------------------------------------------------------
# CSV ダウンロード API
# ---------------------------------------------------------------------------
@app.get("/csv/{file_id}")
async def get_csv_url(file_id: str, request: Request):
    """presigned URL を任意のタイミングで再発行する (失効時の再要求用)。

    No Silent Fallbacks: not_found / expired / objects_missing をそれぞれ
    別のステータスコードでフロントに返す。
    """
    user_id = get_user_id_from_request(request)

    result = regenerate_presigned_url(user_id, file_id)
    status = result["status"]

    if status == "not_found":
        raise HTTPException(status_code=404, detail="File not found")
    if status == "expired":
        raise HTTPException(status_code=410, detail="File expired")
    if status == "objects_missing":
        # メタデータは残っているが S3 オブジェクトが消えている
        raise HTTPException(status_code=410, detail="File objects missing in storage")

    # status == "ready"
    return JSONResponse(content={
        "url": result["url"],
        "filename": result["filename"],
        "expires_at": result["expires_at"],
    })
