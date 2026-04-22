"""DynamoDB をバックエンドとする Strands SessionRepository 実装"""

from __future__ import annotations

from strands.session.session_repository import SessionRepository
from strands.types.session import Session, SessionAgent, SessionMessage

from utils import convert_decimals, convert_floats


class DynamoDBSessionRepository(SessionRepository):
    """DynamoDB をバックエンドとする SessionRepository 実装。

    1 セッション = 1 DynamoDB アイテムに全データを格納する。
    PK=user_id, SK=session_id。
    """

    def __init__(self, table, user_id: str, agent_id: str, title: str = ""):
        self.table = table
        self.user_id = user_id
        self.agent_id = agent_id
        self.title = title

    # --- Session ---

    def create_session(self, session: Session) -> Session:
        self.table.put_item(Item=convert_floats({
            "user_id": self.user_id,
            "session_id": session.session_id,
            "agent_id": self.agent_id,
            "title": self.title,
            "session_data": session.to_dict(),
            "agents": {},
            "agent_messages": {},
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }))
        return session

    def read_session(self, session_id: str) -> Session | None:
        resp = self.table.get_item(Key={
            "user_id": self.user_id,
            "session_id": session_id,
        })
        item = resp.get("Item")
        if not item or "session_data" not in item:
            return None
        return Session.from_dict(convert_decimals(item["session_data"]))

    # --- Agent ---

    def create_agent(self, session_id: str, session_agent: SessionAgent) -> None:
        self.table.update_item(
            Key={"user_id": self.user_id, "session_id": session_id},
            UpdateExpression="SET agents.#aid = :agent",
            ExpressionAttributeNames={"#aid": session_agent.agent_id},
            ExpressionAttributeValues={":agent": convert_floats(session_agent.to_dict())},
        )

    def read_agent(self, session_id: str, agent_id: str) -> SessionAgent | None:
        resp = self.table.get_item(Key={
            "user_id": self.user_id,
            "session_id": session_id,
        })
        item = resp.get("Item", {})
        agent_data = item.get("agents", {}).get(agent_id)
        if not agent_data:
            return None
        return SessionAgent.from_dict(convert_decimals(agent_data))

    def update_agent(self, session_id: str, session_agent: SessionAgent) -> None:
        self.create_agent(session_id, session_agent)

    # --- Messages ---

    def create_message(self, session_id: str, agent_id: str,
                       session_message: SessionMessage) -> None:
        self.table.update_item(
            Key={"user_id": self.user_id, "session_id": session_id},
            UpdateExpression=(
                "SET agent_messages.#aid = list_append("
                "if_not_exists(agent_messages.#aid, :empty), :msg)"
            ),
            ExpressionAttributeNames={"#aid": agent_id},
            ExpressionAttributeValues={
                ":msg": [convert_floats(session_message.to_dict())],
                ":empty": [],
            },
        )

    def read_message(self, session_id: str, agent_id: str,
                     message_id: int) -> SessionMessage | None:
        for msg in self.list_messages(session_id, agent_id):
            if msg.message_id == message_id:
                return msg
        return None

    def update_message(self, session_id: str, agent_id: str,
                       session_message: SessionMessage) -> None:
        messages = self.list_messages(session_id, agent_id)
        for i, msg in enumerate(messages):
            if msg.message_id == session_message.message_id:
                messages[i] = session_message
                break
        self.table.update_item(
            Key={"user_id": self.user_id, "session_id": session_id},
            UpdateExpression="SET agent_messages.#aid = :msgs",
            ExpressionAttributeNames={"#aid": agent_id},
            ExpressionAttributeValues={
                ":msgs": [convert_floats(m.to_dict()) for m in messages],
            },
        )

    def list_messages(self, session_id: str, agent_id: str,
                      limit: int | None = None, offset: int = 0) -> list[SessionMessage]:
        resp = self.table.get_item(Key={
            "user_id": self.user_id,
            "session_id": session_id,
        })
        item = resp.get("Item", {})
        raw_messages = item.get("agent_messages", {}).get(agent_id, [])
        messages = [SessionMessage.from_dict(convert_decimals(m)) for m in raw_messages]
        if offset:
            messages = messages[offset:]
        if limit:
            messages = messages[:limit]
        return messages
