"""Core message and tool call data classes."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageType(str, Enum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str
    result: str | None = None
    error: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
            "error": self.error,
        }


@dataclass
class ChatMessage:
    """A message in a session, stored in the DB and exchanged with the LLM."""

    role: MessageRole
    content: str | list[dict[str, Any]] = ""
    type: MessageType = MessageType.TEXT
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: Usage | None = None

    def to_litellm(self) -> dict[str, Any]:
        """OpenAI-compatible dict for litellm."""
        msg: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        if self.role == MessageRole.TOOL and self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg


def filter_valid_tool_messages(history: list["ChatMessage"]) -> list["ChatMessage"]:
    """Return only provider-valid assistant/tool blocks.

    Providers such as DeepSeek require every assistant tool call to be followed
    by one contiguous tool response with the matching ``tool_call_id``. Partial
    blocks can exist after cancellation or history truncation, so retain only
    calls that have responses inside their own block.
    """
    if not history:
        return history

    filtered: list[ChatMessage] = []
    index = 0
    while index < len(history):
        msg = history[index]
        if msg.role == MessageRole.TOOL:
            index += 1
            continue
        if msg.role != MessageRole.ASSISTANT or not msg.tool_calls:
            filtered.append(msg)
            index += 1
            continue

        responses: dict[str, ChatMessage] = {}
        cursor = index + 1
        while cursor < len(history) and history[cursor].role == MessageRole.TOOL:
            response = history[cursor]
            if response.tool_call_id and response.tool_call_id not in responses:
                responses[response.tool_call_id] = response
            cursor += 1

        valid_calls: list[ToolCall] = []
        seen_call_ids: set[str] = set()
        for call in msg.tool_calls:
            if (
                call.id
                and call.id in responses
                and call.id not in seen_call_ids
            ):
                valid_calls.append(call)
                seen_call_ids.add(call.id)
        if valid_calls:
            filtered.append(
                ChatMessage(
                    role=msg.role,
                    content=msg.content,
                    type=msg.type,
                    tool_calls=valid_calls,
                    metadata=msg.metadata,
                    usage=msg.usage,
                )
            )
            filtered.extend(responses[call.id] for call in valid_calls)
        elif msg.content:
            filtered.append(
                ChatMessage(
                    role=msg.role,
                    content=msg.content,
                    type=MessageType.TEXT,
                    metadata=msg.metadata,
                    usage=msg.usage,
                )
            )
        index = cursor
    return filtered
