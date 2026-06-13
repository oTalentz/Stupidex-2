from stupidex.llm.message import (
    ChatMessage,
    MessageRole,
    MessageType,
    ToolCall,
    filter_valid_tool_messages,
)


def _tool_call(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="read_file", arguments="{}")


def _tool_result(call_id: str, content: str) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.TOOL,
        content=content,
        type=MessageType.TOOL_RESULT,
        tool_call_id=call_id,
    )


def test_keeps_all_results_for_multiple_tool_calls():
    history = [
        ChatMessage(role=MessageRole.SYSTEM, content="system"),
        ChatMessage(role=MessageRole.USER, content="inspect"),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            type=MessageType.TOOL_CALL,
            tool_calls=[_tool_call("call-a"), _tool_call("call-b")],
        ),
        _tool_result("call-a", "a"),
        _tool_result("call-b", "b"),
        ChatMessage(role=MessageRole.ASSISTANT, content="done"),
    ]

    filtered = filter_valid_tool_messages(history)

    assert [message.role for message in filtered] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert [call.id for call in filtered[2].tool_calls] == ["call-a", "call-b"]
    assert [filtered[3].tool_call_id, filtered[4].tool_call_id] == [
        "call-a",
        "call-b",
    ]


def test_repairs_partial_and_out_of_order_tool_block():
    history = [
        ChatMessage(role=MessageRole.USER, content="inspect"),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="working",
            type=MessageType.TOOL_CALL,
            tool_calls=[
                _tool_call("missing"),
                _tool_call("call-a"),
                _tool_call("call-a"),
                _tool_call("call-b"),
            ],
        ),
        _tool_result("call-b", "b"),
        _tool_result("orphan", "ignore"),
        _tool_result("call-a", "a"),
        ChatMessage(role=MessageRole.USER, content="continue"),
    ]

    filtered = filter_valid_tool_messages(history)

    assert [call.id for call in filtered[1].tool_calls] == ["call-a", "call-b"]
    assert [filtered[2].tool_call_id, filtered[3].tool_call_id] == [
        "call-a",
        "call-b",
    ]
    assert filtered[-1].role == MessageRole.USER


def test_drops_orphan_tool_messages_and_empty_unanswered_call():
    history = [
        _tool_result("orphan", "ignore"),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            type=MessageType.TOOL_CALL,
            tool_calls=[_tool_call("missing")],
        ),
        ChatMessage(role=MessageRole.USER, content="continue"),
    ]

    filtered = filter_valid_tool_messages(history)

    assert len(filtered) == 1
    assert filtered[0].role == MessageRole.USER
