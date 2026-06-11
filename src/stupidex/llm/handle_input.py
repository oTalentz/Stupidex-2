"""Stupidex agent — streams responses and executes tools.

Key properties:
  • Per-session: each session has its own message history (SQLite-backed).
  • System prompt is injected ONCE per session and lists attached folders.
  • Tool calls return errors as `ERROR:` strings (so the LLM can see & retry).
  • The loop is bounded (MAX_TOOL_ITERATIONS) and survives a single bad call.
  • Streaming events are yielded as plain dicts; the web layer forwards them.
"""
from . import _bootstrap  # noqa: F401 — must come first

import json
import logging
import threading
import time
import traceback
from collections.abc import Generator
from dataclasses import dataclass

import litellm

# Silence the litellm "Provider List" log spam
litellm.suppress_debug_info = True
logging.getLogger("litellm").setLevel(logging.WARNING)

from .. import db
from .. import workspaces as workspaces_module
from .message import ChatMessage, MessageRole, MessageType, ToolCall, Usage
from .providers import PROVIDERS, get_provider, resolve_request_model
from .tools import TOOL_DEFINITIONS, TOOL_FUNCTIONS

MAX_TOOL_ITERATIONS = 20
MAX_HISTORY_MESSAGES = 200  # safety net

AGENT_SYSTEM_PROMPT = """\
You are Stupidex, a secure coding agent. You operate EXCLUSIVELY inside the user's
workspace — a sandboxed directory containing only files the user uploaded or cloned.
You CANNOT access any files outside this workspace. Attempting to do so will fail.

## Sandbox Rules (MUST FOLLOW)
1. **NEVER** try to read, list, or search files outside the active workspace path.
2. **NEVER** try to access system directories (/etc, /home, /tmp, /var, /root).
3. **NEVER** try to access the Stupidex source code or configuration files.
4. **NEVER** try to access other users' workspaces — each user is isolated.
5. The workspace path is pre-filled for you. **Do NOT override it** with an
   absolute path pointing elsewhere. The system will reject such calls.
6. `run_shell` commands run inside the workspace directory — do not cd elsewhere.

## Tooling
- read_file(path, working_dir?) — read a text file inside the workspace
- write_file(path, content, working_dir?) — create or overwrite a file
- edit_file(path, old_text, new_text, replace_all?, working_dir?) — surgical edits
- list_dir(path?, working_dir?) — list directory contents
- search_files(path, pattern, recursive?, working_dir?) — regex search
- mkdir(path, working_dir?) — create a directory
- delete(path, working_dir?) — remove a file or directory
- run_shell(command, cwd?, timeout?) — execute a shell command inside the workspace
- git(args, cwd?) — run a git subcommand

## Operating principles
1. **Inspect first.** Use list_dir and read_file to understand the project.
2. **Prefer edit_file over write_file** for existing files.
3. **Be surgical.** Match old_text exactly. Use replace_all for bulk changes.
4. **No destructive actions without cause.**
5. **Stay concise.** Summarize changes in 2–6 lines after finishing.
6. **Acknowledge ambiguity.** Ask clarifying questions before mutating files.

## Active workspace
{workspace_summary}

Format your final answers in clean Markdown. Use fenced code blocks with the correct language tag.
"""


@dataclass
class AgentContext:
    session_id: str
    provider_id: str
    api_key: str
    model: str
    base_url: str | None
    cancel_event: threading.Event | None = None
    user_msg_id: int | None = None
    user_id: str = ""  # per-user isolation


def _build_system_message(user_id: str) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.SYSTEM,
        content=AGENT_SYSTEM_PROMPT.format(workspace_summary=_workspace_summary(user_id)),
        type=MessageType.TEXT,
    )


def _workspace_summary(user_id: str = "") -> str:
    if not user_id:
        return "(no user context — workspace unavailable)"
    ws = workspaces_module.get_active_workspace(user_id)
    if not ws:
        return "(no active workspace — upload files or clone a repository first)"
    ws_path = workspaces_module._user_dir(user_id) / ws.id
    summary = f"Active workspace: '{ws.name}' (id={ws.id})\nPath: {ws_path}\nSource: {ws.source}"
    if ws.git_url:
        summary += f"\nGit: {ws.git_url}" + (f" (branch {ws.git_branch})" if ws.git_branch else "")
    summary += f"\nFiles: {ws.file_count} ({ws.size_bytes:,} bytes)"
    return summary


def _active_workspace_path(user_id: str) -> str | None:
    if not user_id:
        return None
    ws = workspaces_module.get_active_workspace(user_id)
    if not ws:
        return None
    return str(workspaces_module._user_dir(user_id) / ws.id)


def _history_for_llm(session_id: str, user_id: str = "") -> list[ChatMessage]:
    """Load the persisted history for a session and prepend the system message."""
    raw = db.get_messages(session_id)
    history: list[ChatMessage] = []
    if not raw or raw[0].role != MessageRole.SYSTEM:
        history.append(_build_system_message(user_id))
    for r in raw:
        if r.role == MessageRole.SYSTEM:
            continue
        history.append(_reconstruct(r))
    if len(history) > MAX_HISTORY_MESSAGES:
        history = [history[0]] + history[-MAX_HISTORY_MESSAGES + 1 :]
    return history


def _reconstruct(r: db.DBMessage) -> ChatMessage:
    role = MessageRole(r.role)
    type_ = MessageType(r.type)
    tool_calls: list[ToolCall] = []
    for tc in r.tool_calls:
        if isinstance(tc, dict):
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                arguments=tc.get("arguments", ""),
                result=tc.get("result"),
                error=tc.get("error", False),
            ))
    return ChatMessage(
        role=role,
        content=r.content,
        type=type_,
        tool_calls=tool_calls,
        tool_call_id=r.tool_call_id,
        metadata=r.metadata,
    )


_DEFAULT_WD_TOOLS = {
    "read_file", "write_file", "edit_file", "list_dir",
    "search_files", "mkdir", "delete",
}
_CWD_TOOLS = {"run_shell", "git"}


def _resolve_working_dir(args: dict, user_id: str = "") -> None:
    """Force `working_dir` to the user's active workspace.

    This is the sandbox: we NEVER let the LLM choose its own working_dir.
    The path is always the user's workspace directory, validated.
    """
    active = _active_workspace_path(user_id)
    if active:
        args["working_dir"] = active
        # Expose the workspace root to subprocess-based tools so the
        # `run_shell` sandbox can also enforce containment.
        import os
        os.environ["STUPIDEX_WORKSPACE_ROOT"] = active


def _resolve_cwd(args: dict, user_id: str = "") -> None:
    """Force `cwd` (used by run_shell/git) to the user's active workspace."""
    active = _active_workspace_path(user_id)
    if active:
        args["cwd"] = active
        import os
        os.environ["STUPIDEX_WORKSPACE_ROOT"] = active


def _litellm_kwargs(ctx: AgentContext) -> dict:
    kw: dict = {
        "model": ctx.model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": TOOL_DEFINITIONS,
    }
    if ctx.base_url:
        kw["base_url"] = ctx.base_url
    if ctx.api_key:
        kw["api_key"] = ctx.api_key
    return kw


def stream_response(
    session_id: str,
    user_text: str,
    ctx: AgentContext,
    regenerate_user_msg_id: int | None = None,
) -> Generator[dict, None, None]:
    """Stream an assistant turn for the given session and user message.

    Persists everything to the DB, executes tools, and yields SSE-ready events.

    If `regenerate_user_msg_id` is set, the assistant is asked to redo the
    turn that follows that user message. We:
      1. Delete the prior assistant turn (and any tool messages after it).
      2. Re-append the user message (idempotent — the UI replays it).
      3. Stream a fresh response.
    """
    if regenerate_user_msg_id is not None:
        # Remove the previous assistant response and any tool messages that
        # followed it; the user message itself stays.
        db.delete_messages_from(session_id, regenerate_user_msg_id + 1)
    else:
        user_msg = db.append_message(session_id, MessageRole.USER.value, user_text, MessageType.TEXT.value)
        ctx.user_msg_id = user_msg.id
    db.auto_title(session_id, user_text)
    db.touch_session(session_id)

    history = _history_for_llm(session_id, ctx.user_id)

    yield {
        "type": "session_meta",
        "session_id": session_id,
        "title": db.get_session(session_id).title if db.get_session(session_id) else "",
    }

    pending_text = ""
    pending_thinking = ""
    pending_calls: dict[str, ToolCall] = {}
    last_usage: Usage | None = None
    cancelled = False

    for iteration in range(MAX_TOOL_ITERATIONS):
        pending_text = ""
        pending_thinking = ""
        pending_calls = {}
        chunk_usage: Usage | None = None

        if ctx.cancel_event and ctx.cancel_event.is_set():
            cancelled = True
            break

        try:
            response = litellm.completion(
                **_litellm_kwargs(ctx),
                messages=[m.to_litellm() for m in history],
            )
        except Exception as exc:
            tb = traceback.format_exc(limit=2)
            err = f"LLM error: {exc}"
            db.append_message(
                session_id, MessageRole.SYSTEM.value, err, MessageType.TEXT.value,
                metadata={"error": True, "trace": tb},
            )
            yield {"type": "error", "content": err}
            return

        for chunk in response:
            if ctx.cancel_event and ctx.cancel_event.is_set():
                cancelled = True
                break
            if not getattr(chunk, "choices", None):
                if hasattr(chunk, "usage") and chunk.usage:
                    chunk_usage = _extract_usage(chunk)
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "reasoning_content", None):
                pending_thinking += delta.reasoning_content
                yield {"type": "thinking", "content": pending_thinking}

            if getattr(delta, "content", None):
                pending_text += delta.content
                yield {"type": "text", "content": pending_text}

            for tc_delta in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc_delta, "index", None)
                key = f"call_{iteration}_{idx if idx is not None else 0}"
                if key not in pending_calls:
                    pending_calls[key] = ToolCall(
                        id=getattr(tc_delta, "id", "") or f"{key}",
                        name="",
                        arguments="",
                    )
                call = pending_calls[key]
                if getattr(tc_delta, "id", None):
                    call.id = tc_delta.id
                fn = getattr(tc_delta, "function", None)
                if fn is not None:
                    if fn.name:
                        call.name = fn.name
                    if fn.arguments:
                        call.arguments += fn.arguments

            if hasattr(chunk, "usage") and chunk.usage:
                chunk_usage = _extract_usage(chunk)

        if chunk_usage:
            last_usage = chunk_usage

        # No tool calls → final assistant turn, persist and finish.
        if not pending_calls:
            msg = ChatMessage(
                role=MessageRole.ASSISTANT,
                content=pending_text,
                type=MessageType.TEXT,
                usage=last_usage,
            )
            history.append(msg)
            db.append_message(
                session_id, MessageRole.ASSISTANT.value, pending_text, MessageType.TEXT.value,
                tool_calls=[], tool_call_id=None, metadata={"usage": last_usage.to_dict() if last_usage else None},
            )
            yield {
                "type": "done",
                "content": pending_text,
                "usage": last_usage.to_dict() if last_usage else None,
            }
            return

        # Tool calls → persist assistant message with the call list, execute, and loop.
        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=pending_text,
            type=MessageType.TOOL_CALL,
            tool_calls=list(pending_calls.values()),
            usage=last_usage,
        )
        history.append(msg)
        db.append_message(
            session_id, MessageRole.ASSISTANT.value, pending_text, MessageType.TOOL_CALL.value,
            tool_calls=[c.to_dict() for c in pending_calls.values()],
        )

        yield {
            "type": "tool_calls",
            "calls": [
                {"id": c.id, "name": c.name, "arguments": c.arguments}
                for c in pending_calls.values()
            ],
        }

        for call in pending_calls.values():
            if ctx.cancel_event and ctx.cancel_event.is_set():
                cancelled = True
                break
            try:
                args = json.loads(call.arguments) if call.arguments else {}
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            if call.name in _DEFAULT_WD_TOOLS:
                _resolve_working_dir(args, ctx.user_id)
                call.arguments = json.dumps(args, ensure_ascii=False)
            elif call.name in _CWD_TOOLS:
                _resolve_cwd(args, ctx.user_id)
                call.arguments = json.dumps(args, ensure_ascii=False)

            yield {
                "type": "tool_result",
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "content": "(running...)",
                "error": False,
            }

            fn = TOOL_FUNCTIONS.get(call.name)
            if fn is None:
                result = f"ERROR: unknown tool '{call.name}'"
                is_error = True
            else:
                try:
                    result = fn(args)
                    is_error = result.startswith("ERROR:")
                except Exception as exc:
                    result = f"ERROR: tool '{call.name}' raised: {exc}"
                    is_error = True

            call.result = result
            call.error = is_error

            yield {
                "type": "tool_result",
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "content": result,
                "error": is_error,
            }

            tool_msg = ChatMessage(
                role=MessageRole.TOOL,
                content=result,
                type=MessageType.TOOL_RESULT,
                tool_call_id=call.id,
                metadata={"error": is_error, "tool_name": call.name},
            )
            history.append(tool_msg)
            db.append_message(
                session_id, MessageRole.TOOL.value, result, MessageType.TOOL_RESULT.value,
                tool_call_id=call.id, metadata={"error": is_error, "tool_name": call.name},
            )

    err = f"agent exceeded {MAX_TOOL_ITERATIONS} tool iterations"
    db.append_message(session_id, MessageRole.SYSTEM.value, err, MessageType.TEXT.value)
    yield {"type": "error", "content": err}


def _extract_usage(chunk) -> Usage:
    u = chunk.usage
    return Usage(
        prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(u, "completion_tokens", 0) or 0,
        total_tokens=getattr(u, "total_tokens", 0) or 0,
    )


def build_context(provider_id: str, api_key_override: str | None, user_id: str = "") -> AgentContext:
    """Build an AgentContext for a request from a session or default config."""
    from ..config import load_config
    cfg = load_config()
    provider = get_provider(provider_id or cfg.provider)
    model = (cfg.custom_model or provider.default_model).strip()
    api_key = api_key_override or cfg.api_key
    return AgentContext(
        session_id="",
        provider_id=provider.id,
        api_key=api_key,
        model=model,
        base_url=provider.base_url,
        user_id=user_id,
    )
