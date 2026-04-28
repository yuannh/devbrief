import json
import os
import shutil
import subprocess

from . import config as cfg

_SYSTEM = """\
You are a dev session analyzer. Given a Claude Code development session transcript, extract structured information.

Return ONLY valid JSON (no markdown fences) with exactly these keys:
- "title": Short session title, 5-10 words
- "problem": The core problem or feature being worked on (1-2 sentences)
- "approach": Technical solution approach taken (2-3 sentences)
- "outcome": Final result — what was accomplished, any blockers (1-2 sentences)
- "summary": Non-technical summary for a manager or team report (3-5 sentences, no jargon, focus on business value and completion status)

Be factual and concise. Infer from context if the session ended incomplete.\
"""

_USER_TMPL = """\
Analyze this dev session and return the JSON.

Language for output: {language}

Transcript:
{transcript}\
"""

_REPORT_SYSTEM = """\
You are a technical project manager writing a concise progress report.
Given a list of completed development sessions (as JSON), write a clear, readable report
suitable for a team lead or business stakeholder with no deep technical background.

Guidelines:
- Group by project if multiple projects are present
- Use plain language, avoid technical jargon
- Highlight what was accomplished and what business value it delivers
- Note any blockers or incomplete items at the end
- Be concise: aim for 150-300 words total\
"""

_REPORT_USER = """\
Write a progress report for the following dev sessions.

Period: {period}
Language: {language}

Sessions (JSON):
{sessions_json}\
"""

# These env vars cause `claude -p` to hang when run inside a Claude Code session.
_CC_ENV_VARS = frozenset({"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH"})


def _has_claude_cli() -> bool:
    return shutil.which("claude") is not None


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _CC_ENV_VARS}


def _run_claude_cli(prompt: str) -> str:
    env = _clean_env()

    # Prefer passing prompt via stdin to avoid OS argument-length limits.
    try:
        result = subprocess.run(
            ["claude", "--print", "--no-cache"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Fall back: prompt as positional argument (works when stdin is not supported).
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI timed out while generating summary")

    if result.returncode != 0:
        stderr = result.stderr.strip()[:400]
        raise RuntimeError(
            f"claude CLI exited with code {result.returncode}"
            + (f": {stderr}" if stderr else "")
        )

    output = result.stdout.strip()
    if not output:
        raise RuntimeError("claude CLI returned empty output")

    return output


def _parse_json(raw: str) -> dict:
    # Strip common markdown fence wrappers the model might add.
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Model returned invalid JSON: {e}\n\nRaw output (first 500 chars):\n{raw[:500]}"
        ) from e


def summarize(transcript: str, language: str = "en") -> dict:
    lang_hint = "English" if language == "en" else language
    user_content = _USER_TMPL.format(language=lang_hint, transcript=transcript)
    prompt = f"{_SYSTEM}\n\n{user_content}"

    if _has_claude_cli():
        raw = _run_claude_cli(prompt)
    else:
        raw = _call_sdk(prompt)

    data = _parse_json(raw)
    for key in ("title", "problem", "approach", "outcome", "summary"):
        data.setdefault(key, "")
    return data


def generate_report(sessions: list[dict], period: str, language: str = "en") -> str:
    lang_hint = "English" if language == "en" else language
    payload = [
        {
            "project": s.get("project_name"),
            "date": s.get("date"),
            "title": s.get("title"),
            "problem": s.get("problem"),
            "outcome": s.get("outcome"),
            "summary": s.get("summary"),
        }
        for s in sessions
    ]
    prompt = (
        f"{_REPORT_SYSTEM}\n\n"
        + _REPORT_USER.format(
            period=period,
            language=lang_hint,
            sessions_json=json.dumps(payload, ensure_ascii=False, indent=2),
        )
    )

    if _has_claude_cli():
        return _run_claude_cli(prompt)
    return _call_sdk(prompt)


def _call_sdk(prompt: str) -> str:
    import anthropic

    api_key = cfg.get_api_key()
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key configured and claude CLI not found. Run: devbrief setup"
        )

    client = anthropic.Anthropic(api_key=api_key)
    model = cfg.load().get("model", "claude-sonnet-4-6")

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()
