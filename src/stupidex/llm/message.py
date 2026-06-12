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
    """Filter out invalid tool call sequences.

    This is needed for DeepSeek and other providers that require:
    1. Tool messages must be preceded by an assistant message with tool_calls
    2. Assistant messages with tool_calls must be followed by corresponding tool messages

    If an assistant message has tool_calls but some don't have corresponding tool responses,
    we filter out the tool_calls without responses to avoid API errors.
    """
    if not history:
        return history

    # First pass: identify all tool_call_ids that have responses
    tool_responses: set[str] = set()
    for msg in history:
        if msg.role == MessageRole.TOOL and msg.tool_call_id:
            tool_responses.add(msg.tool_call_id)

    # Second pass: filter and fix messages
    filtered = []
    for msg in history:
        # Always include system and user messages
        if msg.role in (MessageRole.SYSTEM, MessageRole.USER):
            filtered.append(msg)
        # Handle assistant messages
        elif msg.role == MessageRole.ASSISTANT:
            # If this assistant message has tool_calls, filter out any that don't have responses
            if msg.tool_calls:
                # Only keep tool_calls that have corresponding responses
                valid_tool_calls = [
                    tc for tc in msg.tool_calls if tc.id in tool_responses
                ]
                if valid_tool_calls:
                    # Create a new message with only valid tool_calls
                    new_msg = ChatMessage(
                        role=msg.role,
                        content=msg.content,
                        type=msg.type,
                        tool_calls=valid_tool_calls,
                        tool_call_id=msg.tool_call_id,
                        metadata=msg.metadata,
                        usage=msg.usage,
                    )
                    filtered.append(new_msg)
                else:
                    # All tool_calls are invalid - convert to text message
                    new_msg = ChatMessage(
                        role=msg.role,
                        content=msg.content,
                        type=MessageType.TEXT,
                        tool_calls=[],
                        tool_call_id=None,
                        metadata=msg.metadata,
                        usage=msg.usage,
                    )
                    filtered.append(new_msg)
            else:
                # Assistant message without tool_calls - always include
                filtered.append(msg)
        # Only include tool messages if preceded by an assistant message with tool_calls
        elif msg.role == MessageRole.TOOL:
            if (
                filtered
                and filtered[-1].role == MessageRole.ASSISTANT
                and filtered[-1].tool_calls
                and any(tc.id == msg.tool_call_id for tc in filtered[-1].tool_calls)
            ):
                filtered.append(msg)
            # Otherwise skip this tool message (it's orphaned)
    return filtered
