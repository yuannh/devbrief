"""Token-efficient evidence packet builder.

Reads raw JSONL message dicts (as stored in Claude Code transcripts) and
produces a compact text representation suitable for summarization, plus
metadata about how much was included vs. excluded.

Never calls any LLM.  Never reads files outside the JSONL.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable

# ── constants ─────────────────────────────────────────────────────────────────

_MAX_USER_TEXT = 2000          # chars per user turn
_MAX_ASSISTANT_TEXT = 2000     # chars per assistant text block
_MAX_ERROR_SNIPPET = 500       # chars per error/stderr snippet
_MAX_TOOL_INPUT_REPR = 300     # chars for a tool input summary
_MAX_COMMAND_REPR = 300        # chars for a command line

# Patterns that indicate the block is a devbrief prompt/output we should skip.
_DEVBRIEF_PATTERNS = [
    re.compile(r"devbrief", re.IGNORECASE),
]

# Tool names whose full output we never want (only the command/path).
_NOISY_TOOLS = frozenset({
    "Read", "Write", "Edit", "MultiEdit", "Bash",
    "computer", "str_replace_editor",
})

# File-related tool input keys (we extract the path but not the content).
_PATH_KEYS = ("path", "file_path", "filename", "target_path", "source_path")
_COMMAND_KEYS = ("command",)

_INTERNAL_MODEL_PROMPT_MARKERS = (
    "you are a dev session analyzer",
    "return only valid json",
    "analyze this dev session",
    "given a claude code development session transcript",
    "extract structured information",
    "short session title, 5-10 words",
    "non-technical summary for a manager",
    "recursive analyzer",
    "recursive self-invocation",
)


def is_internal_model_prompt(text: str) -> bool:
    """True when text looks like devbrief/analyzer prompt scaffolding."""
    lower = text.lower()
    return any(marker in lower for marker in _INTERNAL_MODEL_PROMPT_MARKERS)


def build_history_preview(
    messages: list[dict],
    *,
    full: bool = False,
) -> dict:
    """Extract local, no-LLM history preview details from raw transcript JSON."""
    user_requests: list[str] = []
    internal_prompts: list[str] = []
    assistant_texts: list[str] = []
    commands: list[str] = []
    files: set[str] = set()
    tools: list[str] = []
    errors: list[str] = []
    user_limit = 2_000 if full else 1_000
    assistant_limit = 3_000 if full else 1_200
    internal_limit = 4_000 if full else 1_000
    command_limit = 600 if full else _MAX_COMMAND_REPR

    for msg in messages:
        msg_type = msg.get("type", "")
        content = msg.get("message", {}).get("content", "")

        if msg_type == "user":
            for text in _extract_user_texts(content):
                clean = text.strip()
                if not clean:
                    continue
                if is_internal_model_prompt(clean):
                    internal_prompts.append(_truncate(clean, internal_limit))
                elif _is_local_command_message(clean):
                    continue
                else:
                    user_requests.append(_truncate_user_request(clean, user_limit, full=full))

            for result in _extract_tool_results(content):
                if _looks_like_error(result):
                    errors.append(_truncate(result.strip(), _MAX_ERROR_SNIPPET))

        elif msg_type == "assistant":
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        assistant_texts.append(_truncate(text, assistant_limit))
                elif btype == "tool_use":
                    tool_name = str(block.get("name") or "unknown")
                    tools.append(tool_name)
                    tool_input = block.get("input", {})
                    if isinstance(tool_input, dict):
                        commands.extend(_extract_commands(tool_input, limit=command_limit))
                        files.update(_extract_paths(tool_input))

    command_count = len(_dedupe(commands))
    file_count = len(files)
    tool_count = len(_dedupe(tools))
    error_count = len([e for e in errors if e])
    return {
        "first_user_request": user_requests[0] if user_requests else "",
        "user_requests": user_requests[:25] if full else user_requests[:10],
        "internal_model_prompts": internal_prompts[:25] if full else internal_prompts[:10],
        "final_assistant_text": assistant_texts[-1] if assistant_texts else "",
        "commands": _dedupe(commands)[:100] if full else _dedupe(commands)[:20],
        "files": sorted(files)[:200] if full else sorted(files)[:80],
        "touched_files_count": len(files),
        "tools": _dedupe(tools)[:100] if full else _dedupe(tools)[:40],
        "errors": [e for e in errors if e][:30] if full else [e for e in errors if e][:8],
        "counts": {
            "commands": command_count,
            "files": file_count,
            "tools": tool_count,
            "errors": error_count,
            "user_requests": len(user_requests),
            "internal_model_prompts": len(internal_prompts),
        },
    }


def infer_session_outcome(preview: dict, messages: list[dict]) -> dict:
    """Infer local session outcome from preview/messages without any model call."""
    final_text = str(preview.get("final_assistant_text") or "")
    human_requests = preview.get("user_requests") or []
    errors = preview.get("errors") or []
    tail_text = "\n".join(_tail_assistant_texts(messages, limit=5))
    combined_final = f"{tail_text}\n{final_text}".lower()
    combined_errors = "\n".join(str(e) for e in errors).lower()
    signals: list[str] = []

    usage_markers = (
        "out of extra usage",
        "usage limit",
        "resets",
        "claude usage limit",
        "rate limit",
    )
    if marker := _first_marker(combined_final, usage_markers):
        signals.append(f'final response contains "{marker}"')
        late_marker = _late_request_marker(human_requests)
        if late_marker:
            signals.append(f'late request asks for "{late_marker}"')
            reason = (
                "Final closeout/verification was requested, but Claude Code "
                "stopped because usage limit was reached."
            )
        else:
            reason = "Claude Code stopped because usage limit was reached."
        return {
            "status": "usage_limited",
            "completion": "incomplete",
            "confidence": "high",
            "reason": reason,
            "signals": signals,
        }

    completion_markers = (
        "done",
        "completed",
        "implemented",
        "fixed",
        "committed",
        "pushed",
        "tests passing",
        "all checks pass",
        "deployed",
        "verified",
        "no further action needed",
    )
    completion_signal = _first_marker(combined_final, completion_markers)

    interrupted_markers = (
        "interrupted",
        "cancelled",
        "tool_use_error",
        "keyboardinterrupt",
        "terminated",
    )
    if marker := _first_marker(f"{combined_final}\n{combined_errors}", interrupted_markers):
        signals.append(f'contains "{marker}"')
        return {
            "status": "interrupted",
            "completion": "incomplete",
            "confidence": "high",
            "reason": "The session appears to have been interrupted or cancelled.",
            "signals": signals,
        }

    blocker_markers = (
        "blocked",
        "failed",
        "cannot proceed",
        "permission denied",
        "not found",
        "connection refused",
        "fatal",
        "error:",
        "traceback",
    )
    blocker_signal = _first_marker(f"{combined_final}\n{combined_errors}", blocker_markers)
    if blocker_signal and not completion_signal:
        signals.append(f'contains "{blocker_signal}"')
        return {
            "status": "blocked",
            "completion": "incomplete",
            "confidence": "medium",
            "reason": "Errors or blocker language were detected near the session end.",
            "signals": signals,
        }

    if completion_signal:
        signals.append(f'final response contains "{completion_signal}"')
        return {
            "status": "completed",
            "completion": "complete",
            "confidence": "medium",
            "reason": "The final response includes completion language.",
            "signals": signals,
        }

    followup_markers = (
        "next step",
        "follow up",
        "remaining",
        "todo",
        "needs",
        "not yet",
        "pending",
        "verify",
        "deploy",
        "manual check",
    )
    if marker := _first_marker(combined_final, followup_markers):
        signals.append(f'final response contains "{marker}"')
        return {
            "status": "needs_followup",
            "completion": "incomplete",
            "confidence": "medium",
            "reason": "The final response mentions remaining work or verification.",
            "signals": signals,
        }

    if human_requests and not final_text.strip():
        signals.append("human request exists but no final assistant response was found")
        return {
            "status": "incomplete",
            "completion": "incomplete",
            "confidence": "medium",
            "reason": "The session has a human request but no useful final response.",
            "signals": signals,
        }

    if human_requests:
        signals.append("human request exists but no completion signal was detected")
        return {
            "status": "incomplete",
            "completion": "incomplete",
            "confidence": "low",
            "reason": "The session has a human request but no strong completion signal.",
            "signals": signals,
        }

    signals.append("no human request or useful final response detected")
    return {
        "status": "unknown",
        "completion": "unknown",
        "confidence": "low",
        "reason": "No meaningful human request or final response was found.",
        "signals": signals,
    }


# ── public API ────────────────────────────────────────────────────────────────

def build_evidence_packet(
    messages: list[dict],
    max_chars: int = 16_000,
    session_meta: dict | None = None,
) -> tuple[str, dict]:
    """Build a compact evidence string from raw JSONL message dicts.

    Parameters
    ----------
    messages:
        Raw dicts as returned by :func:`parser.extract_raw_messages`.
    max_chars:
        Hard cap on the returned packet string length.
    session_meta:
        Optional dict with keys like session_id, project_name, cwd,
        user_turn_count — prepended as a header if provided.

    Returns
    -------
    (packet_text, metadata_dict)
    """
    parts: list[str] = []
    excluded: dict[str, int] = {
        "long_tool_outputs": 0,
        "full_file_contents": 0,
        "large_diffs": 0,
        "devbrief_blocks": 0,
        "repeated_json": 0,
        "oversized_markdown": 0,
    }
    raw_chars = 0

    # Header
    if session_meta:
        header_parts = ["=== SESSION METADATA ==="]
        for k in ("session_id", "project_name", "cwd", "user_turn_count", "created_at"):
            if k in session_meta:
                header_parts.append(f"{k}: {session_meta[k]}")
        parts.append("\n".join(header_parts))

    last_assistant_text: str | None = None

    for msg in messages:
        msg_type = msg.get("type", "")
        raw_chars += len(json.dumps(msg))

        if msg_type == "user":
            content = msg.get("message", {}).get("content", "")
            if isinstance(content, str):
                if _is_devbrief_block(content):
                    excluded["devbrief_blocks"] += 1
                    continue
                text = _truncate(content, _MAX_USER_TEXT)
                if text.strip():
                    parts.append(f"[USER]: {text}")

            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        text = block.get("text", "")
                        if _is_devbrief_block(text):
                            excluded["devbrief_blocks"] += 1
                            continue
                        truncated = _truncate(text, _MAX_USER_TEXT)
                        if truncated.strip():
                            parts.append(f"[USER]: {truncated}")
                    elif btype == "tool_result":
                        # Tool result content — capture only errors/small output
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            for rc in result_content:
                                if isinstance(rc, dict) and rc.get("type") == "text":
                                    result_content = rc.get("text", "")
                                    break
                            else:
                                result_content = ""
                        if isinstance(result_content, str):
                            lc = result_content.lower()
                            is_error = (
                                block.get("is_error")
                                or "error" in lc
                                or "traceback" in lc
                                or "exception" in lc
                                or "stderr" in lc
                            )
                            if is_error:
                                snippet = _truncate(result_content, _MAX_ERROR_SNIPPET)
                                if snippet.strip():
                                    parts.append(f"[TOOL ERROR]: {snippet}")
                            else:
                                excluded["long_tool_outputs"] += 1

        elif msg_type == "assistant":
            content = msg.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")

                if btype == "text":
                    text = block.get("text", "").strip()
                    if not text:
                        continue
                    if _is_devbrief_block(text):
                        excluded["devbrief_blocks"] += 1
                        continue
                    # Skip overly long markdown/diff blocks
                    if _is_large_diff_or_fence(text):
                        excluded["oversized_markdown"] += 1
                        last_assistant_text = _truncate(text, _MAX_ASSISTANT_TEXT)
                        continue
                    truncated = _truncate(text, _MAX_ASSISTANT_TEXT)
                    parts.append(f"[ASSISTANT]: {truncated}")
                    last_assistant_text = truncated

                elif btype == "tool_use":
                    tool_name = block.get("name", "unknown")
                    inp = block.get("input", {})
                    summary = _summarize_tool_input(tool_name, inp, excluded)
                    if summary:
                        parts.append(f"[TOOL:{tool_name}]: {summary}")

    # Append final assistant response if it wasn't already the last part.
    if last_assistant_text and parts and not parts[-1].startswith("[ASSISTANT]:"):
        parts.append(f"[FINAL ASSISTANT]: {last_assistant_text}")

    packet = "\n\n".join(parts)

    truncated_flag = len(packet) > max_chars
    if truncated_flag:
        packet = packet[:max_chars] + "\n\n[... packet truncated to fit max_chars ...]"

    compact_chars = len(packet)
    estimated_tokens = compact_chars // 4

    metadata = {
        "raw_chars": raw_chars,
        "compact_chars": compact_chars,
        "estimated_tokens": estimated_tokens,
        "truncated": truncated_flag,
        "excluded_counts": excluded,
    }
    return packet, metadata


# ── helpers ───────────────────────────────────────────────────────────────────

def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[+{len(text) - limit} chars]"


def _truncate_user_request(text: str, limit: int, *, full: bool) -> str:
    if len(text) <= limit:
        return text
    if full:
        return _truncate(text, limit)
    return text[:limit] + f"\n[...+{len(text) - limit} chars, use --full for complete request]"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _extract_user_texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []

    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                texts.append(text)
    return texts


def _extract_tool_results(content: object) -> list[str]:
    if not isinstance(content, list):
        return []

    results: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        result_content = block.get("content", "")
        if isinstance(result_content, str):
            results.append(result_content)
        elif isinstance(result_content, list):
            for item in result_content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if isinstance(text, str):
                        results.append(text)
    return results


def _looks_like_error(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in ("error", "traceback", "exception", "stderr", "failed")
    )


def _first_marker(text: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        if marker in text:
            return marker
    return ""


def _late_request_marker(human_requests: list[str]) -> str:
    if not human_requests:
        return ""
    late_text = "\n".join(human_requests[-2:]).lower()
    markers = (
        "final closeout",
        "confirm",
        "verify",
        "report",
        "deploy status",
        "remaining risks",
    )
    return _first_marker(late_text, markers)


def _tail_assistant_texts(messages: list[dict], limit: int) -> list[str]:
    texts: list[str] = []
    for msg in reversed(messages):
        if msg.get("type") != "assistant":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    texts.append(text)
                    break
        if len(texts) >= limit:
            break
    return list(reversed(texts))


def _is_local_command_message(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("<local-command-") or stripped.startswith("<command-")


def _extract_commands(value: object, limit: int = _MAX_COMMAND_REPR) -> list[str]:
    commands: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _COMMAND_KEYS and isinstance(item, str):
                commands.append(_truncate(item, limit))
            else:
                commands.extend(_extract_commands(item, limit=limit))
    elif isinstance(value, list):
        for item in value:
            commands.extend(_extract_commands(item, limit=limit))
    return commands


def _extract_paths(value: object) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _PATH_KEYS and isinstance(item, str):
                paths.add(item)
            else:
                paths.update(_extract_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(_extract_paths(item))
    return paths


def _is_devbrief_block(text: str) -> bool:
    sample = text[:500]
    return any(p.search(sample) for p in _DEVBRIEF_PATTERNS)


def _is_large_diff_or_fence(text: str) -> bool:
    # Heuristic: very long code fence blocks or diff outputs
    fence_count = text.count("```")
    if fence_count >= 2 and len(text) > 1000:
        return True
    if text.startswith("diff ") and len(text) > 1000:
        return True
    return False


def _summarize_tool_input(
    tool_name: str, inp: dict, excluded: dict[str, int]
) -> str:
    """Return a short string summarising what a tool call did."""
    parts: list[str] = []

    # File paths
    for key in _PATH_KEYS:
        val = inp.get(key)
        if val and isinstance(val, str):
            parts.append(f"{key}={val}")

    # Commands
    for key in _COMMAND_KEYS:
        val = inp.get(key)
        if val and isinstance(val, str):
            cmd = _truncate(val, _MAX_COMMAND_REPR)
            parts.append(f"cmd={cmd}")

    # For write/edit tools, just note the file + operation, not the content
    if tool_name in ("Write", "Edit", "MultiEdit", "str_replace_editor"):
        excluded["full_file_contents"] += 1
        if not parts:
            return "(file operation — content excluded)"
        return " ".join(parts) + " (content excluded)"

    # For Read, note the file
    if tool_name == "Read":
        excluded["full_file_contents"] += 1
        return " ".join(parts) if parts else "(file read — path unknown)"

    # For other tools, include a short JSON representation of input
    if not parts:
        try:
            raw = json.dumps(inp, ensure_ascii=False)
            return _truncate(raw, _MAX_TOOL_INPUT_REPR)
        except Exception:
            return "(complex input)"

    return " ".join(parts)
