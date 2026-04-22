import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

import boto3
from strands import Agent
from strands.models import BedrockModel
from strands.session.repository_session_manager import RepositorySessionManager
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.vended_plugins.skills import AgentSkills, Skill

from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

from tools import ToolSharedState, create_tools
from dynamodb_session import DynamoDBSessionRepository
from utils import convert_decimals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# 環境変数
SESSIONS_TABLE_NAME = os.environ["SESSIONS_TABLE_NAME"]
CONFIG_TABLE_NAME = os.environ["CONFIG_TABLE_NAME"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
REDSHIFT_WORKGROUP_NAME = os.environ["REDSHIFT_WORKGROUP_NAME"]
REDSHIFT_DATABASE = os.environ["REDSHIFT_DATABASE"]
REDSHIFT_SECRET_ARN = os.environ["REDSHIFT_SECRET_ARN"]
ENABLE_PROMPT_CACHE = os.environ.get("ENABLE_PROMPT_CACHE", "false").lower() == "true"

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION"))
sessions_table = dynamodb.Table(SESSIONS_TABLE_NAME)
config_table = dynamodb.Table(CONFIG_TABLE_NAME)


# ---------------------------------------------------------------------------
# Config / System Prompt（既存 app.py からそのまま移植）
# ---------------------------------------------------------------------------
def load_config(agent_id: str) -> dict:
    """DynamoDB config テーブルから指定 agent_id の設定を読み込む。

    db_schema が空の場合は "default" agent の設定でフォールバックする。
    """
    response = config_table.get_item(Key={"id": agent_id})
    config = response.get("Item", {})

    if config and not config.get("db_schema") and agent_id != "default":
        logger.info("agent_id=%s に db_schema がないため default からフォールバック", agent_id)
        default_resp = config_table.get_item(Key={"id": "default"})
        default_config = default_resp.get("Item", {})
        if default_config.get("db_schema"):
            config["db_schema"] = default_config["db_schema"]

    return config


def _filter_schema_for_agent(schema) -> str:
    """db_schema から Agent に不要なフィールド (csv_options, s3_keys) を除外して返す。"""
    if not schema:
        return ""
    # 文字列の場合はパース
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except (json.JSONDecodeError, TypeError):
            return schema  # パース不能ならそのまま返す

    if not isinstance(schema, dict) or "tables" not in schema:
        return json.dumps(schema, ensure_ascii=False, indent=2)

    filtered = {"tables": []}
    for table in schema["tables"]:
        filtered["tables"].append({
            "table_name": table.get("table_name", ""),
            "description": table.get("description", ""),
            "columns": table.get("columns", []),
        })
    return json.dumps(filtered, ensure_ascii=False, indent=2)


def build_system_prompt(config: dict) -> str:
    """config テーブルの system_prompt + db_schema を結合（固定部分）"""
    base = config.get("system_prompt", "あなたはデータ分析アシスタントです。")
    schema_str = _filter_schema_for_agent(config.get("db_schema", ""))
    if schema_str:
        base += f"\n\n## データベーススキーマ\n{schema_str}"
    base += "\n\n分析結果を可視化すべき場合は _render_chart ツールを活用してください。"
    return base


def _get_today_jst() -> str:
    """JST での今日の日付を YYYY-MM-DD 形式で返す"""
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y-%m-%d")


def build_system_prompt_content(config: dict) -> str | list[dict]:
    """Prompt Cache 設定に応じて system_prompt を構築する。"""
    base = build_system_prompt(config)

    if not ENABLE_PROMPT_CACHE:
        return f"{base}\n\n今日の日付は {_get_today_jst()} です。"

    return [
        {"text": base},
        {"cachePoint": {"type": "default"}},
        {"text": f"今日の日付は {_get_today_jst()} です。"},
    ]


# ---------------------------------------------------------------------------
# Agent builder（既存 app.py からそのまま移植）
# ---------------------------------------------------------------------------
def build_agent(
    user_id: str, session_id: str, config: dict, agent_id: str, title: str = "",
) -> tuple[Agent, ToolSharedState]:
    tools, shared_state = create_tools(
        REDSHIFT_WORKGROUP_NAME, REDSHIFT_DATABASE, REDSHIFT_SECRET_ARN,
    )

    # skills の読み込み (Strands AgentSkills Plugin)
    plugins = []
    skills_data = config.get("skills", [])
    if skills_data:
        try:
            loaded_skills = [Skill.from_content(s) for s in skills_data]
            if loaded_skills:
                plugins.append(AgentSkills(skills=loaded_skills))
        except Exception as e:
            logger.warning("Failed to load skills: %s", e)

    # BedrockModel 設定
    model_kwargs: dict = {"model_id": BEDROCK_MODEL_ID}
    if ENABLE_PROMPT_CACHE:
        model_kwargs["cache_tools"] = "default"

    # SessionManager: DynamoDB バックエンドで messages / state を自動永続化
    repo = DynamoDBSessionRepository(
        table=sessions_table, user_id=user_id, agent_id=agent_id, title=title,
    )
    session_manager = RepositorySessionManager(
        session_id=session_id,
        session_repository=repo,
    )

    agent = Agent(
        system_prompt=build_system_prompt_content(config),
        model=BedrockModel(**model_kwargs),
        tools=tools,
        plugins=plugins,
        session_manager=session_manager,
        conversation_manager=SlidingWindowConversationManager(window_size=40),
    )

    return agent, shared_state


# ---------------------------------------------------------------------------
# AgentCore Runtime エントリポイント
# ---------------------------------------------------------------------------
@app.entrypoint
async def invoke(payload, context: RequestContext) -> AsyncGenerator[dict, None]:
    """AgentCore Runtime エントリポイント（ストリーミング）

    yield した dict は BedrockAgentCoreApp が自動的に json.dumps して
    `data: {json}\n\n` の SSE 形式に変換する。
    Lambda プロキシ側では `data: ` 行をそのままフロントに中継する。
    """
    session_id = context.session_id  # AgentCore Runtime が HTTP ヘッダーから設定
    user_id = payload.get("user_id")
    message = payload.get("prompt")
    title = payload.get("title", message[:50] if message else "")

    agent_id = payload.get("agent_id", "default")
    logger.info("invoke start: session_id=%s, user_id=%s, agent_id=%s", session_id, user_id, agent_id)

    config = load_config(agent_id)
    if not config:
        yield {"type": "error", "content": f"Agent not found: {agent_id}"}
        yield {"type": "done"}
        return

    agent, shared_state = build_agent(user_id, session_id, config, agent_id, title)

    pending_tools: dict[str, dict] = {}  # toolUseId -> tool_info
    tool_uses = []

    def flush_all_pending():
        """pending_tools を tool_uses に移し、SSE 用 dict のリストを返す"""
        nonlocal pending_tools
        if not pending_tools:
            return []
        results = []
        for pt in pending_tools.values():
            entry = {
                "tool": pt["name"],
                "input": json.dumps(pt.get("input", {}), ensure_ascii=False),
            }
            tool_uses.append(entry)
            results.append(
                {"type": "tool_use", "tool": entry["tool"], "input": entry["input"]}
            )
        pending_tools = {}
        return results

    try:
        async for event in agent.stream_async(message):
            # --- テキストチャンク ---
            if "data" in event:
                chunk = event["data"]
                yield {"type": "token", "content": chunk}

            # --- ツール使用の蓄積 ---
            elif "current_tool_use" in event:
                tool_info = event["current_tool_use"]
                tool_id = tool_info.get("toolUseId")
                tool_name = tool_info.get("name")
                if tool_name and tool_id:
                    pending_tools[tool_id] = tool_info

            else:
                # --- チャート SSE（start_event_loop 時に flush）---
                if event.get("start_event_loop"):
                    for chart_spec in shared_state.pop_chart_specs():
                        logger.info(
                            "[invoke] sending chart: %s",
                            json.dumps(chart_spec, ensure_ascii=False, default=str)[:200],
                        )

                        yield convert_decimals({"type": "chart", "spec": chart_spec})

                # --- messageStop で tool_use をフラッシュ ---
                raw_event = event.get("event", {})
                if isinstance(raw_event, dict):
                    stop_info = raw_event.get("messageStop", {})
                    if stop_info.get("stopReason") == "tool_use" and pending_tools:
                        for item in flush_all_pending():
                            yield item

        # ストリーム終了後に残った pending をフラッシュ
        for item in flush_all_pending():
            yield item

    except Exception as e:
        logger.exception("invoke() error: %s", e)
        yield {"type": "error", "content": str(e)}

    finally:
        yield {"type": "done"}


if __name__ == "__main__":
    app.run()
