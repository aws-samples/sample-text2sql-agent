import json
import logging
import os

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
AGENTCORE_RUNTIME_ARN = os.environ["AGENTCORE_RUNTIME_ARN"]

dynamodb = boto3.resource("dynamodb")
sessions_table = dynamodb.Table(SESSIONS_TABLE_NAME)
config_table = dynamodb.Table(CONFIG_TABLE_NAME)

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
# Sessions API（既存と同一、変更なし）
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


def _strands_to_display_messages(agent_messages: list[dict]) -> list[dict]:
    """Strands 生メッセージをフロント表示用に変換する。"""
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
                    for c in tr.get("content", []):
                        if "text" in c:
                            try:
                                parsed = json.loads(c["text"])
                                if "chart_spec" in parsed and tool_use_id in tool_use_by_id:
                                    tool_use_by_id[tool_use_id]["chart_spec"] = parsed["chart_spec"]
                            except (json.JSONDecodeError, KeyError):
                                pass

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
    display = _strands_to_display_messages(convert_decimals(raw_messages))

    return JSONResponse(content={
        "session_id": session_id,
        "messages": display,
    })


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    user_id = get_user_id_from_request(request)
    sessions_table.delete_item(Key={"user_id": user_id, "session_id": session_id})
    return JSONResponse(content={"message": "Session deleted", "session_id": session_id})
