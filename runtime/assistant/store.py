"""Persistent conversation storage; it never writes project artifacts."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    title: str
    created_at: str
    updated_at: str
    archived: bool = False


@dataclass(frozen=True)
class Message:
    message_id: str
    conversation_id: str
    role: str
    content: str
    created_at: str
    status: str = "completed"
    trace_id: str = ""
    source_manifest: dict[str, Any] | None = None
    context_snapshot_id: str = ""


class ConversationStore:
    """SQLite-backed history isolated under the application user-data root."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
                    trace_id TEXT NOT NULL DEFAULT '',
                    source_manifest_json TEXT NOT NULL DEFAULT '{}',
                    context_snapshot_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS context_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_events (
                    event_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def list_conversations(self, *, include_archived: bool = False) -> list[Conversation]:
        query = "SELECT * FROM conversations"
        params: tuple[Any, ...] = ()
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY updated_at DESC"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [Conversation(str(row["conversation_id"]), str(row["title"]), str(row["created_at"]), str(row["updated_at"]), bool(row["archived"])) for row in rows]

    def create_conversation(self, title: str = "新对话") -> Conversation:
        conversation = Conversation(str(uuid.uuid4()), title.strip() or "新对话", _now(), _now())
        with self._connect() as db:
            db.execute(
                "INSERT INTO conversations(conversation_id,title,created_at,updated_at,archived) VALUES(?,?,?,?,0)",
                (conversation.conversation_id, conversation.title, conversation.created_at, conversation.updated_at),
            )
        return conversation

    def archive_conversation(self, conversation_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE conversations SET archived=1, updated_at=? WHERE conversation_id=?", (_now(), conversation_id))

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE conversations SET title=?, updated_at=? WHERE conversation_id=?", (title.strip() or "新对话", _now(), conversation_id))

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        status: str = "completed",
        trace_id: str = "",
        source_manifest: dict[str, Any] | None = None,
        context_snapshot_id: str = "",
    ) -> Message:
        message = Message(
            str(uuid.uuid4()), conversation_id, role, content, _now(), status, trace_id,
            dict(source_manifest or {}), context_snapshot_id,
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO messages(message_id,conversation_id,role,content,created_at,status,trace_id,source_manifest_json,context_snapshot_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (message.message_id, conversation_id, role, content, message.created_at, status, trace_id, json.dumps(message.source_manifest, ensure_ascii=False), context_snapshot_id),
            )
            db.execute("UPDATE conversations SET updated_at=? WHERE conversation_id=?", (_now(), conversation_id))
        return message

    def messages(self, conversation_id: str) -> list[Message]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at, rowid", (conversation_id,)).fetchall()
        result: list[Message] = []
        for row in rows:
            try:
                manifest = json.loads(str(row["source_manifest_json"] or "{}"))
            except json.JSONDecodeError:
                manifest = {}
            result.append(Message(str(row["message_id"]), str(row["conversation_id"]), str(row["role"]), str(row["content"]), str(row["created_at"]), str(row["status"]), str(row["trace_id"]), manifest if isinstance(manifest, dict) else {}, str(row["context_snapshot_id"])))
        return result

    def save_snapshot(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib

        snapshot_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO context_snapshots(snapshot_id,payload_json,created_at) VALUES(?,?,?)", (snapshot_id, encoded, _now()))
        return snapshot_id

    def snapshot(self, snapshot_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM context_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def add_tool_event(self, conversation_id: str, message_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO tool_events(event_id,conversation_id,message_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), conversation_id, message_id, event_type, json.dumps(payload, ensure_ascii=False), _now()))
