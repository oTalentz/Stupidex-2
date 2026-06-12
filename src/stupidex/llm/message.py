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
    """Filter out tool messages that don't have a corresponding assistant message with tool_calls.

    This is needed for DeepSeek and other providers that require tool messages to be
    preceded by an assistant message with tool_calls.
    """
    if not history:
        return history

    filtered = []
    for i, msg in enumerate(history):
        # Always include system, user, and assistant messages
        if msg.role in (MessageRole.SYSTEM, MessageRole.USER, MessageRole.ASSISTANT):
            filtered.append(msg)
        # Only include tool messages if preceded by an assistant message with tool_calls
        elif msg.role == MessageRole.TOOL:
            if (
                i > 0
                and filtered[-1].role == MessageRole.ASSISTANT
                and filtered[-1].tool_calls
            ):
                filtered.append(msg)
            # Otherwise skip this tool message (it's orphaned)
    return filtered
