# deep_research.py — opt-in autonomous multi-step tool-calling loop for /chat.
#
# Runs only when ChatRequest.deep_research=True (user-triggered per request,
# same framing as web_search — see main.py's ChatRequest). Once started, the
# LLM decides whether to keep going each iteration via a text convention,
# since llm.chat() has no OpenAI-style function-calling support (neither
# backend payload sends a `tools` field). The model requests a tool call by
# emitting a marker anywhere in its reply:
#
#   <<TOOL_CALL>>{"tool": "web_search", "arguments": {"query": "..."}}<<END>>
#
# Absence of the marker in a reply means the model is done; that reply is
# the final answer and the loop stops.
#
# Restricted to READ-ONLY tools only (see mcp-server's registry.go for the
# full tool list) — there is no human-in-the-loop approval gate left in this
# codebase (actions.py/<<DRAFT_ACTION>> was removed in d5038c9e), so write
# tools (file_write, memory_set, http_request) must never be auto-invoked
# here.
#
# Safety nets, in priority order:
#   1. Hard step cap (settings.deep_research_max_steps).
#   2. Repeat-call guard — if a (tool, canonical-args) pair repeats within
#      the same loop invocation, stop immediately without re-executing it.
#   3. On cap or repeat-guard trip: one forced final-answer LLM call with the
#      tool-instructions system block swapped for an explicit "no more tools
#      available, answer now" instruction.
#
# Every step (successful call, disallowed-tool, malformed-JSON, or MCPError)
# is written to the scratchpad under a per-step key — see scratchpad.py.

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from config import settings
from mcp_client import MCPClient, MCPError
from scratchpad import ScratchpadStore

logger = logging.getLogger("uvicorn.error")

# Only these tools may be auto-invoked by the loop. Everything else in
# registry.go (memory_set, file_write, http_request) is a write/side-effecting
# tool and is deliberately excluded — no approval gate exists to guard them.
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "web_search",
        "web_fetch",
        "file_read",
        "file_list",
        "memory_get",
        "memory_list",
    }
)

# Matches <<TOOL_CALL>>{...json...}<<END>> anywhere in the reply text.
# DOTALL so the JSON blob can itself contain newlines.
_TOOL_CALL_RE = re.compile(r"<<TOOL_CALL>>(.*?)<<END>>", re.DOTALL)

_TOOL_INSTRUCTIONS = (
    "You are in deep-research mode. You may call a read-only tool by "
    "replying with EXACTLY this format:\n\n"
    '<<TOOL_CALL>>{"tool": "<name>", "arguments": {...}}<<END>>\n\n'
    f"Allowed tools: {', '.join(sorted(ALLOWED_TOOLS))}.\n"
    "If you already have enough information, reply normally with your final "
    "answer and do NOT include a <<TOOL_CALL>> block — that is how the loop "
    "knows you are done."
)

_FORCED_FINAL_INSTRUCTIONS = (
    "You have used all available research steps (or repeated a call you "
    "already made). No more tool calls are available. Answer the user's "
    "original question now, as best you can, using only the information "
    "already gathered above. Do not emit a <<TOOL_CALL>> block."
)


@dataclass
class ResearchStep:
    """One iteration of the loop, for logging/debugging by the caller."""

    tool: str | None  # None for a forced-final or non-tool reply
    arguments: dict | None
    result: dict | None
    error: str | None
    reply_text: str  # raw LLM reply for this iteration


@dataclass
class ResearchResult:
    reply: str  # final answer to return to the user
    steps: list[ResearchStep] = field(default_factory=list)


def _canonical_args(arguments: dict) -> str:
    """Stable JSON encoding for repeat-call comparison (sorted keys)."""
    return json.dumps(arguments, sort_keys=True, default=str)


def _parse_tool_call(reply: str) -> tuple[str | None, dict | None, str | None]:
    """Look for a <<TOOL_CALL>>...<<END>> marker in `reply`.

    Returns (tool_name, arguments, error):
      - no marker present            -> (None, None, None): final answer, stop.
      - marker present but malformed -> (None, None, "<error message>"): caller
        feeds this back as a synthetic tool-result error instead of crashing.
      - marker present and valid     -> (tool_name, arguments_dict, None).
    """
    match = _TOOL_CALL_RE.search(reply)
    if not match:
        return None, None, None
    raw = match.group(1).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, None, f"malformed <<TOOL_CALL>> JSON: {exc}"
    if not isinstance(parsed, dict) or "tool" not in parsed:
        return None, None, "malformed <<TOOL_CALL>> payload: missing 'tool' key"
    tool = parsed.get("tool")
    arguments = parsed.get("arguments") or {}
    if not isinstance(arguments, dict):
        return None, None, "malformed <<TOOL_CALL>> payload: 'arguments' must be an object"
    return tool, arguments, None


async def run_research_loop(
    messages: list[dict],
    *,
    project_id: str,
    session_id: str,
    mcp: MCPClient,
    scratchpad: ScratchpadStore,
    chat_fn: Callable[[list[dict]], Awaitable[str]],
    max_steps: int | None = None,
) -> ResearchResult:
    """Run the multi-step tool-calling loop and return the final reply.

    `messages` is the fully-built prompt (system blocks + history + user
    message) exactly as main.py assembles it today. This function copies the
    list (never mutates the caller's own), appends the tool-instructions
    system block right before the trailing user message, then repeatedly
    calls chat_fn, parses/executes tool calls, appends assistant + synthetic
    tool-result messages, and writes each step to the scratchpad.
    """
    if max_steps is None:
        max_steps = settings.deep_research_max_steps

    working_messages = list(messages)
    # Insert right before the trailing user message — closest to the point
    # of decision, per design — rather than at the front of the prompt.
    working_messages.insert(len(working_messages) - 1, {"role": "system", "content": _TOOL_INSTRUCTIONS})

    steps: list[ResearchStep] = []
    seen_calls: set[tuple[str, str]] = set()
    step_num = 0

    while True:
        reply = await chat_fn(working_messages)
        tool, arguments, parse_error = _parse_tool_call(reply)

        if tool is None and parse_error is None:
            # Clean final answer — no marker present. Done.
            steps.append(ResearchStep(None, None, None, None, reply))
            return ResearchResult(reply=reply, steps=steps)

        step_num += 1
        working_messages.append({"role": "assistant", "content": reply})

        repeat_guard_tripped = False
        result_text: str

        if parse_error is not None:
            result_text = f"ERROR: {parse_error}"
            steps.append(ResearchStep(tool, arguments, None, parse_error, reply))
        elif tool not in ALLOWED_TOOLS:
            error = f"tool {tool!r} is not allowed in deep-research mode"
            result_text = f"ERROR: {error}"
            steps.append(ResearchStep(tool, arguments, None, error, reply))
        else:
            call_key = (tool, _canonical_args(arguments))
            if call_key in seen_calls:
                repeat_guard_tripped = True
                result_text = "ERROR: repeated call detected, forcing final answer"
                steps.append(ResearchStep(tool, arguments, None, "repeat_call_guard_tripped", reply))
            else:
                seen_calls.add(call_key)
                try:
                    result = await mcp.call(tool, arguments, project_id=project_id)
                    result_text = json.dumps(result, default=str)[:2000]
                    steps.append(ResearchStep(tool, arguments, result, None, reply))
                except MCPError as exc:
                    result_text = f"ERROR: {exc}"
                    steps.append(ResearchStep(tool, arguments, None, str(exc), reply))

        await scratchpad.set_entry(
            project_id,
            session_id,
            f"step_{step_num}",
            json.dumps({"tool": tool, "arguments": arguments, "result_or_error": result_text}, default=str),
        )

        if repeat_guard_tripped or step_num >= max_steps:
            working_messages.append({"role": "system", "content": _FORCED_FINAL_INSTRUCTIONS})
            final_reply = await chat_fn(working_messages)
            steps.append(ResearchStep(None, None, None, None, final_reply))
            return ResearchResult(reply=final_reply, steps=steps)

        working_messages.append(
            {"role": "system", "content": f"Tool result for {tool!r}: {result_text}"}
        )
