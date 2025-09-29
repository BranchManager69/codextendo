#!/usr/bin/env python3
"""Codextendo summarization helpers.

This script powers both the ad-hoc `codexsummarize` command and the new
`codextendo refresh` batch workflow. It reads Codex session transcripts,
assembles a token-aware prompt that captures all payload types, and persists
the resulting JSON/Markdown summaries alongside a lightweight cache so we only
re-summarize sessions whose content has changed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from config import get_config_value

try:  # Optional dependency for precise token accounting.
    import tiktoken  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback handled at runtime
    tiktoken = None

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - surfaced in CLI
    print("Missing Python package 'requests'. Install it with 'pip install requests'.", file=sys.stderr)
    raise


def _default_model() -> str:
    return os.environ.get(
        "CODEXTENDO_SUMMARY_MODEL",
        get_config_value("summarizer_model", "gpt-5"),
    )


DEFAULT_MODEL = _default_model()
DEFAULT_MAX_TOKENS = int(os.environ.get("CODEXTENDO_SUMMARY_TOKEN_LIMIT", "200000"))

_TOKEN_WARNING_EMITTED = False


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _ensure_dir(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path: pathlib.Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _parse_timestamp(raw: Optional[str]) -> Optional[_dt.datetime]:
    if not raw:
        return None
    cleaned = raw.replace("Z", "+00:00")
    try:
        ts = _dt.datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return ts


def _derive_session_id(path: pathlib.Path) -> str:
    stem = path.stem
    parts = stem.split("-")
    if len(parts) >= 5:
        tail = parts[-5:]
        if all(tail):
            return "-".join(tail)
    return stem


def _project_hints_from_cwd(cwd_value: Optional[str]) -> Dict[str, Any]:
    hints: Dict[str, Any] = {}
    if not cwd_value:
        return hints

    hints["session_cwd"] = cwd_value

    try:
        path_obj = pathlib.Path(cwd_value).expanduser()
    except Exception:
        return hints

    name = path_obj.name
    if not name and path_obj.parts:
        name = path_obj.parts[-1]
    if name:
        hints["project_hint"] = name
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
        if slug:
            hints["project_slug"] = slug.lower()

    project_path_hint: Optional[str] = None
    try:
        home = pathlib.Path.home()
        relative = path_obj.relative_to(home)
        relative_str = str(relative) if str(relative) else "."
        hints["session_cwd_rel_home"] = relative_str
        project_path_hint = relative_str
    except Exception:
        project_path_hint = None

    hints["project_path_hint"] = project_path_hint or cwd_value

    return hints


def _extract_session_meta(session_path: pathlib.Path) -> Dict[str, Any]:
    try:
        with session_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                payload = data.get("payload") or {}
                if payload.get("type") == "session_meta":
                    return {k: v for k, v in payload.items() if k != "type"}
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    return {}


def _format_json(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _read_label_map(label_file: Optional[pathlib.Path]) -> Dict[str, str]:
    if not label_file:
        return {}
    return _load_json(label_file, default={})


class TokenCounter:
    def __init__(self) -> None:
        self.encoder = None
        if tiktoken is not None:
            for name in ("o200k_base", "cl100k_base"):
                try:
                    self.encoder = tiktoken.get_encoding(name)
                    break
                except Exception:
                    continue

    def count(self, text: str) -> int:
        if self.encoder is not None:
            return len(self.encoder.encode(text))
        # Conservative fallback: assume ~4 chars/token, never return zero.
        return max(1, len(text) // 4)

    @property
    def precise(self) -> bool:
        return self.encoder is not None


def _render_payload(payload: Dict) -> Optional[Tuple[str, str]]:
    ptype = payload.get("type")
    timestamp = payload.get("timestamp")
    prefix = None
    content: Optional[str] = None

    if ptype == "message":
        role = payload.get("role", "unknown").upper()
        text_parts = []
        for chunk in payload.get("content") or []:
            if isinstance(chunk, dict):
                text_parts.append(chunk.get("text", ""))
        content = "".join(text_parts).strip()
        if not content:
            return None
        prefix = role
    elif ptype in {"user_message", "agent_message"}:
        prefix = ptype.upper()
        content = payload.get("message", "").strip()
    elif ptype == "agent_reasoning":
        prefix = "AGENT_REASONING"
        content = payload.get("text", "").strip()
    elif ptype == "reasoning":
        prefix = "REASONING"
        summary = payload.get("summary")
        if isinstance(summary, list):
            content = "\n".join(item.get("text", "") for item in summary if isinstance(item, dict)).strip()
        elif isinstance(summary, dict):
            content = summary.get("text", "").strip()
        if not content:
            enc = payload.get("encrypted_content")
            if enc:
                content = "<encrypted reasoning content>"
    elif ptype == "function_call":
        name = payload.get("name", "")
        prefix = f"FUNCTION_CALL {name}".strip()
        arguments = payload.get("arguments")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
                content = _format_json(parsed)
            except json.JSONDecodeError:
                content = arguments
        else:
            content = _format_json(arguments)
    elif ptype == "function_call_output":
        call_id = payload.get("call_id", "")
        prefix = f"FUNCTION_OUTPUT {call_id}".strip()
        output = payload.get("output")
        if isinstance(output, (dict, list)):
            content = _format_json(output)
        else:
            content = str(output or "").strip()
    elif ptype == "token_count":
        prefix = "TOKEN_COUNT"
        content = _format_json({
            "info": payload.get("info"),
            "rate_limits": payload.get("rate_limits"),
        })
    elif ptype == "turn_aborted":
        prefix = "TURN_ABORTED"
        content = _format_json({k: v for k, v in payload.items() if k != "type"})
    elif ptype == "event_msg":
        prefix = "EVENT"
        content = _format_json({k: v for k, v in payload.items() if k != "type"})
    else:
        # Unknown payloads fall back to JSON so nothing is lost.
        prefix = (ptype or "UNKNOWN").upper()
        content = _format_json({k: v for k, v in payload.items() if k != "type"})

    if content is None:
        return None
    content = content.strip()
    if not content:
        return None
    return prefix, content


def _collect_segments(path: pathlib.Path) -> Tuple[List[Dict], Optional[_dt.datetime], str, Dict[str, Any]]:
    segments: List[Dict] = []
    latest_ts: Optional[_dt.datetime] = None
    digest = hashlib.sha256()
    session_meta: Dict[str, Any] = {}

    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            payload = data.get("payload") or {}
            ptype = payload.get("type")
            if ptype == "session_meta" and not session_meta:
                session_meta = {k: v for k, v in payload.items() if k != "type"}
            ts = _parse_timestamp(payload.get("timestamp") or data.get("timestamp"))
            if ts and (latest_ts is None or ts > latest_ts):
                latest_ts = ts

            rendered = _render_payload(payload)
            if not rendered:
                continue
            header, text = rendered
            combined = f"{header}:\n{text.strip()}"
            digest.update(header.encode("utf-8", errors="ignore"))
            digest.update(b"\0")
            digest.update(text.encode("utf-8", errors="ignore"))
            segments.append({
                "header": header,
                "text": text,
                "combined": combined,
                "payload_type": ptype,
                "timestamp": ts.isoformat() if ts else None,
            })

    return segments, latest_ts, digest.hexdigest(), session_meta


def _trim_segments(segments: List[Dict], max_tokens: int, counter: TokenCounter) -> Tuple[List[Dict], bool, int]:
    if not segments:
        return segments, False, 0

    if max_tokens <= 0:
        return segments, False, sum(counter.count(seg["combined"]) for seg in segments)

    token_counts = [counter.count(seg["combined"]) for seg in segments]
    total_tokens = sum(token_counts)
    if total_tokens <= max_tokens:
        return segments, False, total_tokens

    truncated = True
    start_index = 0
    running = total_tokens
    while start_index < len(segments) - 1 and running > max_tokens:
        running -= token_counts[start_index]
        start_index += 1

    trimmed = segments[start_index:]
    trimmed_tokens = sum(counter.count(seg["combined"]) for seg in trimmed)

    # If we trimmed everything (single huge segment), keep the most recent piece.
    if not trimmed:
        trimmed = [segments[-1]]
        trimmed_tokens = counter.count(trimmed[0]["combined"])

    # Ensure we do not exceed the budget; drop earliest segments while necessary.
    while len(trimmed) > 1 and trimmed_tokens > max_tokens:
        removed = trimmed.pop(0)
        trimmed_tokens -= counter.count(removed["combined"])

    return trimmed, truncated, trimmed_tokens


def _estimate_length(tokens: int) -> Tuple[int, float]:
    if tokens <= 0:
        return 0, 0.0
    words = int(round(tokens * 0.75))
    pages = words / 275 if words else 0.0
    return words, pages


def _format_timedelta(delta: _dt.timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    sign = '-' if total_seconds < 0 else ''
    total_seconds = abs(total_seconds)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours:02}h")
    parts.append(f"{minutes:02}m")
    parts.append(f"{seconds:02}s")
    return sign + ' '.join(parts)


def _build_user_prompt(session_id: str, label: Optional[str], truncated: bool,
                       kept_tokens: int, total_segments: int, kept_segments: int,
                       latest_ts: Optional[_dt.datetime], combined_text: str) -> str:
    lines = [f"Session ID: {session_id}"]
    if label:
        lines.append(f"Label: {label}")
    if latest_ts:
        lines.append(f"Latest message: {latest_ts.isoformat()}")
    if truncated:
        lines.append(
            f"NOTE: Transcript truncated to the most recent {kept_segments} of {total_segments} segments (~{kept_tokens} tokens)."
        )
    lines.append("")
    lines.append("Transcript:")
    lines.append(combined_text)
    return "\n".join(lines)


def _request_summary(
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    prompt_cache_key: Optional[str],
) -> Tuple[Dict, Dict]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY to summarize conversations.")

    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [
                    {"type": "input_text", "text": system_prompt}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_prompt}
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "codextendo_summary",
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Human-friendly session title (<= 10 words)",
                            "maxLength": 160,
                        },
                        "summary": {"type": "string"},
                        "key_actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "description": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["completed", "in_progress", "blocked", "planned"],
                                    },
                                },
                                "required": ["description", "status"],
                                "additionalProperties": False,
                            },
                        },
                        "files_touched": {"type": "array", "items": {"type": "string"}},
                        "concerns": {"type": "array", "items": {"type": "string"}},
                        "follow_up": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "title",
                        "summary",
                        "key_actions",
                        "files_touched",
                        "concerns",
                        "follow_up",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "max_output_tokens": max_tokens,
    }

    if prompt_cache_key:
        payload["prompt_cache_key"] = prompt_cache_key

    response = requests.post(
        os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1") + "/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,
    )

    if response.status_code != 200:
        try:
            detail = response.json()
        except Exception:  # pragma: no cover - best effort formatting
            detail = response.text
        raise RuntimeError(f"OpenAI API error ({response.status_code}): {detail}")

    data = response.json()
    if data.get("status") != "completed":
        details = data.get("incomplete_details") or {}
        raise RuntimeError(
            f"OpenAI summarizer returned status={data.get('status')} (reason={details.get('reason')})."
        )

    usage_info = data.get("usage") or {}

    for block in data.get("output", []):
        for piece in block.get("content", []):
            if piece.get("type") == "output_json":
                return piece.get("json"), usage_info
            if piece.get("type") == "output_text":
                try:
                    return json.loads(piece.get("text", "")), usage_info
                except json.JSONDecodeError:
                    continue

    raise RuntimeError("Failed to parse summary from model response.")


def _write_summary(session_id: str, label: Optional[str], model: str, truncated: bool,
                   kept_tokens: int, original_digest: str, summary_payload: Dict,
                   usage: Dict, summary_dir: pathlib.Path,
                   extra_fields: Optional[Dict[str, Any]] = None) -> Tuple[pathlib.Path, pathlib.Path, Dict]:
    generated_at = _now_utc().isoformat()
    generated_title = (summary_payload.get("title") or "").strip()
    effective_label = label or (generated_title if generated_title else None)

    prompt_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    completion_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    cached_prompt_tokens = None
    cached_completion_tokens = None
    if isinstance(usage, dict):
        input_details = usage.get("input_tokens_details") or {}
        cached_prompt_tokens = input_details.get("cached_tokens")
        output_details = usage.get("output_tokens_details") or {}
        cached_completion_tokens = output_details.get("cached_tokens")

    record = {
        "session_id": session_id,
        "label": effective_label,
        "generated_at": generated_at,
        "model": model,
        "truncated": truncated,
        "kept_tokens": kept_tokens,
        "digest": original_digest,
        **summary_payload,
    }

    if generated_title:
        record["title"] = generated_title
    record["label"] = effective_label

    if isinstance(prompt_tokens, int):
        record["usage_input_tokens"] = prompt_tokens
    if isinstance(completion_tokens, int):
        record["usage_output_tokens"] = completion_tokens
    if isinstance(total_tokens, int):
        record["usage_total_tokens"] = total_tokens
    if isinstance(cached_prompt_tokens, int):
        record["usage_cached_input_tokens"] = cached_prompt_tokens
    if isinstance(cached_completion_tokens, int):
        record["usage_cached_output_tokens"] = cached_completion_tokens

    if extra_fields:
        for key, value in extra_fields.items():
            if value is not None:
                record[key] = value

    summary_dir = _ensure_dir(summary_dir)
    json_path = summary_dir / f"{session_id}.json"
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))

    md_lines = [f"# Summary for {session_id}", f"Generated: {generated_at}"]
    if generated_title:
        md_lines.append(f"Title: {generated_title}")
    if effective_label and effective_label != generated_title:
        md_lines.append(f"Label: {effective_label}")
    token_line_parts = []
    if isinstance(prompt_tokens, int):
        token_line_parts.append(f"Prompt tokens: {prompt_tokens:,}")
    if isinstance(completion_tokens, int):
        token_line_parts.append(f"Completion tokens: {completion_tokens:,}")
    if isinstance(total_tokens, int):
        token_line_parts.append(f"Total tokens: {total_tokens:,}")
    if token_line_parts:
        md_lines.append("Model usage: " + ", ".join(token_line_parts))
    md_lines.append("")

    summary_text = (summary_payload.get("summary") or "").strip()
    if summary_text:
        md_lines.extend(["## TL;DR", summary_text, ""])

    key_actions = summary_payload.get("key_actions") or []
    if key_actions:
        md_lines.append("## Key Actions")
        for action in key_actions:
            description = action.get("description", "").strip()
            status = action.get("status", "unknown")
            md_lines.append(f"- **{status}** – {description}")
        md_lines.append("")

    files_touched = summary_payload.get("files_touched") or []
    if files_touched:
        md_lines.append("## Files Touched")
        for item in files_touched:
            if isinstance(item, dict):
                path_value = item.get("path", "")
                notes = item.get("notes", "")
                if notes:
                    md_lines.append(f"- `{path_value}` – {notes}")
                else:
                    md_lines.append(f"- `{path_value}`")
            else:
                md_lines.append(f"- `{item}`")
        md_lines.append("")

    concerns = summary_payload.get("concerns") or []
    if concerns:
        md_lines.append("## Concerns / Risks")
        for concern in concerns:
            md_lines.append(f"- {concern}")
        md_lines.append("")

    follow_up = summary_payload.get("follow_up") or []
    if follow_up:
        md_lines.append("## Follow-up / TODO")
        for item in follow_up:
            md_lines.append(f"- {item}")
        md_lines.append("")

    if truncated:
        md_lines.append("_Note: Transcript truncated to the most recent portion for summarization._")

    md_path = summary_dir / f"{session_id}.md"
    md_path.write_text("\n".join(md_lines))

    return json_path, md_path, record


def _append_history(summary_dir: pathlib.Path, record: Dict) -> pathlib.Path:
    history_path = summary_dir / f"{record['session_id']}.history.md"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    generated_at = record.get("generated_at", _now_utc().isoformat())
    model = record.get("model", "unknown")
    label = record.get("label") or "—"
    kept_tokens = record.get("kept_tokens")
    truncated = record.get("truncated")
    summary_text = (record.get("summary") or "").strip()

    key_actions = record.get("key_actions") or []
    concerns = record.get("concerns") or []
    follow_up = record.get("follow_up") or []

    lines = [
        "",
        "---",
        f"### {generated_at} · {model}",
        f"Label: {label}",
        f"Tokens kept: {kept_tokens if kept_tokens is not None else 'unknown'}",
        f"Transcript truncated: {'yes' if truncated else 'no'}",
        "",
    ]

    if summary_text:
        lines.extend(["Summary:", summary_text, ""])

    if key_actions:
        lines.append("Key Actions (top):")
        for action in key_actions[:5]:
            if isinstance(action, dict):
                desc = action.get("description", "").strip()
                status = action.get("status", "unknown")
                lines.append(f"- {status}: {desc}")
            else:
                lines.append(f"- {action}")
        lines.append("")

    if concerns:
        lines.append("Concerns:")
        for concern in concerns[:5]:
            lines.append(f"- {concern}")
        lines.append("")

    if follow_up:
        lines.append("Follow-up:")
        for item in follow_up[:5]:
            lines.append(f"- {item}")
        lines.append("")

    with history_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    return history_path


def summarize_session(path: pathlib.Path, label: Optional[str], model: str,
                      max_tokens: int, summary_dir: pathlib.Path,
                      label_map: Dict[str, str]) -> Tuple[pathlib.Path, pathlib.Path, Dict]:
    if not path.exists():
        raise RuntimeError(f"Session file not found: {path}")

    session_id = _derive_session_id(path)
    if not label:
        label = label_map.get(str(path))

    segments, latest_ts, digest, session_meta = _collect_segments(path)
    if not segments:
        raise RuntimeError("No message content found in session.")

    global _TOKEN_WARNING_EMITTED
    counter = TokenCounter()
    if not counter.precise and not _TOKEN_WARNING_EMITTED:
        print(
            "[codextendo] Precise token counting requires the 'tiktoken' package; using an approximate fallback.",
            file=sys.stderr,
        )
        _TOKEN_WARNING_EMITTED = True
    trimmed_segments, truncated, kept_tokens = _trim_segments(segments, max_tokens, counter)
    combined_text = "\n\n".join(seg["combined"] for seg in trimmed_segments)

    system_prompt = (
        "You are an assistant that summarizes Codex CLI sessions. "
        "Produce a concise narrative plus structured key actions, files, concerns, "
        "and concrete follow-ups. Limit key_actions to the top 6 items and files_touched to the top 10 paths. "
        "Also craft a short, human-friendly session title (maximum 10 words) that reflects the latest work. "
        "Always obey the supplied JSON schema, using empty arrays when appropriate."
    )

    user_prompt = _build_user_prompt(
        session_id=session_id,
        label=label,
        truncated=truncated,
        kept_tokens=kept_tokens,
        total_segments=len(segments),
        kept_segments=len(trimmed_segments),
        latest_ts=latest_ts,
        combined_text=combined_text,
    )

    cache_material = f"{digest}|{model}|{max_tokens}"
    cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()

    summary_payload, usage_info = _request_summary(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        prompt_cache_key=cache_key,
    )

    extra_fields: Dict[str, Any] = {
        "session_path": str(path),
    }
    if latest_ts:
        extra_fields["latest_timestamp"] = latest_ts.isoformat()
    if session_meta:
        extra_fields["session_meta"] = session_meta
        extra_fields.update(_project_hints_from_cwd(session_meta.get("cwd")))

    json_path, md_path, record = _write_summary(
        session_id=session_id,
        label=label,
        model=model,
        truncated=truncated,
        kept_tokens=kept_tokens,
        original_digest=digest,
        summary_payload=summary_payload,
        usage=usage_info,
        summary_dir=summary_dir,
        extra_fields=extra_fields,
    )

    if "session_cwd" not in record and session_meta:
        record.update(_project_hints_from_cwd(session_meta.get("cwd")))

    if not counter.precise:
        record["token_counter"] = "approximate"

    history_path = _append_history(summary_dir, record)
    record["history_path"] = str(history_path)

    return json_path, md_path, record


def refresh_summaries(sessions_dir: pathlib.Path, summary_dir: pathlib.Path,
                      index_path: pathlib.Path, model: str, max_tokens: int,
                      limit: Optional[int], force: bool) -> None:
    label_file = pathlib.Path(os.environ.get("CODEX_LABEL_FILE", pathlib.Path.home() / ".codex" / "search_labels.json"))
    label_map = _read_label_map(label_file if label_file.exists() else None)

    index_data = _load_json(index_path, default={})
    if not isinstance(index_data, dict):
        index_data = {}

    sessions = sorted(sessions_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)

    if limit:
        sessions = sessions[-limit:]

    to_process: List[pathlib.Path] = []
    usage_totals = {"input": 0, "output": 0, "total": 0, "cached_input": 0, "cached_output": 0}

    for path in sessions:
        session_id = _derive_session_id(path)
        entry = index_data.get(session_id)
        if force or entry is None:
            to_process.append(path)
            continue

        recorded_digest = entry.get("digest")
        recorded_mtime = entry.get("latest_timestamp")

        segments, latest_ts, digest, _meta = _collect_segments(path)
        if not segments:
            continue

        latest_iso = latest_ts.isoformat() if latest_ts else None
        if digest != recorded_digest or latest_iso != recorded_mtime:
            to_process.append(path)

    if not to_process:
        print("All summaries are up to date.")
        return

    summary_dir = _ensure_dir(summary_dir)

    for path in to_process:
        session_id = _derive_session_id(path)
        try:
            json_path, md_path, record = summarize_session(
                path=path,
                label=None,
                model=model,
                max_tokens=max_tokens,
                summary_dir=summary_dir,
                label_map=label_map,
            )
        except Exception as exc:
            print(f"[WARN] Failed to summarize {path.name}: {exc}", file=sys.stderr)
            continue

        record["summarized_at"] = _now_utc().isoformat()
        index_data[session_id] = record
        history_path = record.get("history_path")
        if history_path:
            print(f"Refreshed summary for {session_id} -> {md_path} (history → {history_path})")
        else:
            print(f"Refreshed summary for {session_id} -> {md_path}")

        usage_input = record.get("usage_input_tokens")
        usage_output = record.get("usage_output_tokens")
        usage_total = record.get("usage_total_tokens")
        cached_input = record.get("usage_cached_input_tokens")
        cached_output = record.get("usage_cached_output_tokens")
        if isinstance(usage_input, int):
            usage_totals["input"] += usage_input
        if isinstance(usage_output, int):
            usage_totals["output"] += usage_output
        if isinstance(usage_total, int):
            usage_totals["total"] += usage_total
        if isinstance(cached_input, int):
            usage_totals["cached_input"] += cached_input
        if isinstance(cached_output, int):
            usage_totals["cached_output"] += cached_output

    _ensure_dir(index_path.parent)
    index_path.write_text(json.dumps(index_data, indent=2, ensure_ascii=False))

    if any(value > 0 for value in usage_totals.values()):
        cached_prompt_note = (
            f", cached prompt {usage_totals['cached_input']:,}" if usage_totals["cached_input"] else ""
        )
        cached_completion_note = (
            f", cached completion {usage_totals['cached_output']:,}" if usage_totals["cached_output"] else ""
        )
        print(
            "Token usage this run → "
            f"prompt {usage_totals['input']:,}{cached_prompt_note}, "
            f"completion {usage_totals['output']:,}{cached_completion_note}, "
            f"total {usage_totals['total']:,}"
        )


def refresh_metadata(
    sessions_dir: pathlib.Path,
    summary_dir: pathlib.Path,
    index_path: pathlib.Path,
    only_sessions: Optional[Iterable[str]],
) -> None:
    sessions_dir = sessions_dir.expanduser()
    summary_dir = summary_dir.expanduser()
    index_path = index_path.expanduser()

    if not summary_dir.exists():
        print(f"Summary directory not found: {summary_dir}", file=sys.stderr)
        return

    index_data = _load_json(index_path, default={})
    if not isinstance(index_data, dict):
        index_data = {}

    session_filter = set(only_sessions) if only_sessions else None

    summary_paths = sorted(summary_dir.glob("*.json"))
    processed = 0
    updated = 0
    missing_sessions = 0
    index_changed = False

    for summary_path in summary_paths:
        if summary_path.name == "index.json":
            continue
        record = _load_json(summary_path, default={})
        if not isinstance(record, dict):
            continue

        session_id = record.get("session_id") or summary_path.stem
        if session_filter and session_id not in session_filter:
            continue

        session_path = _resolve_session_path(session_id, record, sessions_dir)
        if not session_path or not session_path.exists():
            missing_sessions += 1
            continue

        session_meta = _extract_session_meta(session_path)
        hints = _project_hints_from_cwd(session_meta.get("cwd") if session_meta else None)

        payload_updates: Dict[str, Any] = {
            "session_path": str(session_path),
            "session_meta": session_meta or None,
        }
        payload_updates.update({
            "session_cwd": hints.get("session_cwd"),
            "session_cwd_rel_home": hints.get("session_cwd_rel_home"),
            "project_hint": hints.get("project_hint"),
            "project_slug": hints.get("project_slug"),
            "project_path_hint": hints.get("project_path_hint"),
        })

        original_snapshot = json.dumps(record, sort_keys=True, ensure_ascii=False)

        for key, value in payload_updates.items():
            if value is None:
                if key in record:
                    del record[key]
            else:
                if record.get(key) != value:
                    record[key] = value

        new_snapshot = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if new_snapshot != original_snapshot:
            summary_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
            updated += 1

        existing_entry_raw = index_data.get(session_id)
        existing_entry = existing_entry_raw if isinstance(existing_entry_raw, dict) else None
        new_entry = dict(record)
        if existing_entry is None:
            index_data[session_id] = new_entry
            index_changed = True
        else:
            if json.dumps(existing_entry, sort_keys=True, ensure_ascii=False) != json.dumps(new_entry, sort_keys=True, ensure_ascii=False):
                index_data[session_id] = new_entry
                index_changed = True

        processed += 1

    if index_changed:
        _ensure_dir(index_path.parent)
        index_path.write_text(json.dumps(index_data, indent=2, ensure_ascii=False))

    print(
        f"Metadata refresh processed {processed} summaries; "
        f"updated {updated} file(s)."
    )
    if missing_sessions:
        print(
            f"Sessions missing on disk: {missing_sessions} (skipped)",
            file=sys.stderr,
        )
    if index_changed:
        print(f"Index updated -> {index_path}")


def _resolve_session_path(session_id: str, record: Dict[str, Any], sessions_dir: pathlib.Path) -> Optional[pathlib.Path]:
    candidate_raw = record.get("session_path")
    if isinstance(candidate_raw, str):
        candidate = pathlib.Path(candidate_raw)
        if candidate.exists():
            return candidate

    direct = sessions_dir / f"{session_id}.jsonl"
    if direct.exists():
        return direct

    pattern = f"*{session_id}.jsonl"
    try:
        match = next(sessions_dir.rglob(pattern))
        return match
    except StopIteration:
        return None


def _summarise_payload_mix(
    segments: List[Dict],
    sample_size: int,
) -> Dict[str, Any]:
    if not segments:
        return {
            "segments_total": 0,
            "conversation_segments": 0,
            "type_counts": Counter(),
        }

    if sample_size > 0 and len(segments) > sample_size:
        sample = segments[-sample_size:]
    else:
        sample = segments

    total_segments = len(sample)
    type_counts: Counter[str] = Counter(seg.get("payload_type") or "unknown" for seg in sample)
    conversation_types = {"message", "user_message", "agent_message"}
    conversation_segments = sum(type_counts.get(t, 0) for t in conversation_types)

    return {
        "segments_total": total_segments,
        "conversation_segments": conversation_segments,
        "type_counts": type_counts,
    }


def _compute_threshold_reachback(
    segments: List[Dict],
    counter: TokenCounter,
    thresholds: List[int],
) -> Tuple[List[Dict[str, Any]], Optional[str], int]:
    if not segments or not thresholds:
        return [], None, 0

    ordered: List[Tuple[Optional[_dt.datetime], int]] = []
    latest_ts: Optional[_dt.datetime] = None
    total_tokens = 0
    for seg in segments:
        ts = _parse_timestamp(seg.get("timestamp"))
        tokens = counter.count(seg["combined"])
        ordered.append((ts, tokens))
        total_tokens += tokens
        if ts and (latest_ts is None or ts > latest_ts):
            latest_ts = ts

    if not ordered or latest_ts is None:
        return [], latest_ts.isoformat() if latest_ts else None, total_tokens

    reached: Dict[int, Optional[_dt.datetime]] = {}
    running = 0
    for ts, tokens in reversed(ordered):
        running += tokens
        for threshold in thresholds:
            if threshold not in reached and running >= threshold:
                reached[threshold] = ts

    output: List[Dict[str, Any]] = []
    for threshold in thresholds:
        ts = reached.get(threshold)
        if not ts:
            continue
        delta = latest_ts - ts if ts else None
        output.append(
            {
                "tokens": threshold,
                "timestamp": ts.isoformat() if ts else None,
                "delta_seconds": int(delta.total_seconds()) if delta else None,
            }
        )

    return output, latest_ts.isoformat() if latest_ts else None, total_tokens


def _find_session_file(session_id: str, sessions_dir: pathlib.Path) -> Optional[pathlib.Path]:
    direct = sessions_dir / f"{session_id}.jsonl"
    if direct.exists():
        return direct

    try:
        match = next(sessions_dir.rglob(f"*{session_id}.jsonl"))
        return match
    except StopIteration:
        return None


def transcript_chunk(
    session_id: str,
    sessions_dir: pathlib.Path,
    page: int,
    page_size: int,
    order: str,
    include_tokens: bool,
    json_mode: bool,
    pretty_json: bool,
) -> int:
    sessions_dir = sessions_dir.expanduser()
    if not sessions_dir.exists():
        print(f"Sessions directory not found: {sessions_dir}", file=sys.stderr)
        return 1

    session_path = _find_session_file(session_id, sessions_dir)
    if not session_path or not session_path.exists():
        print(f"Session file not found for {session_id}", file=sys.stderr)
        return 1

    segments, latest_ts, digest, _meta = _collect_segments(session_path)
    total_segments = len(segments)
    counter = TokenCounter()

    annotated: List[Dict[str, Any]] = []
    total_tokens = 0
    for index, segment in enumerate(segments):
        combined = segment.get("combined", "")
        tokens = counter.count(combined) if include_tokens else None
        if isinstance(tokens, int):
            total_tokens += tokens
        annotated.append(
            {
                "index": index,
                "timestamp": segment.get("timestamp"),
                "header": segment.get("header"),
                "payload_type": segment.get("payload_type"),
                "text": segment.get("text"),
                "tokens": tokens,
            }
        )

    page_size = max(1, page_size)
    page = max(1, page)

    if order not in {"newest", "oldest"}:
        order = "newest"

    if order == "newest":
        end = total_segments - (page - 1) * page_size
        end = max(0, min(total_segments, end))
        start = max(0, end - page_size)
    else:
        start = (page - 1) * page_size
        end = min(total_segments, start + page_size)

    page_segments = annotated[start:end]
    page_tokens = sum(seg.get("tokens") or 0 for seg in page_segments)

    has_older = start > 0 if order == "newest" else end < total_segments
    has_newer = end < total_segments if order == "newest" else start > 0

    payload = {
        "session_id": session_id,
        "total_segments": total_segments,
        "total_tokens": total_tokens if include_tokens else None,
        "page": page,
        "page_size": page_size,
        "order": order,
        "range": {"start": start, "end": max(start, end) - 1},
        "has_older": has_older,
        "has_newer": has_newer,
        "latest_timestamp": latest_ts.isoformat() if latest_ts else None,
        "segments": page_segments,
        "page_tokens": page_tokens if include_tokens else None,
    }

    if json_mode:
        print(json.dumps(payload, indent=2 if pretty_json else None, ensure_ascii=False))
        return 0

    print(
        f"Session {session_id}: segments {start}-{end - 1 if end else 0} / {total_segments}"
    )
    print(
        f"Page tokens: {page_tokens if include_tokens else 'n/a'} | Total tokens: {total_tokens if include_tokens else 'n/a'}"
    )
    for entry in page_segments:
        ts = entry.get("timestamp") or "?"
        header = entry.get("header") or entry.get("payload_type") or "?"
        tokens_value = entry.get("tokens")
        token_str = f" [{tokens_value} tok]" if include_tokens and isinstance(tokens_value, int) else ""
        text = entry.get("text") or ""
        preview = text[:200].replace("\n", " ")
        print(f"- {ts} {header}{token_str}: {preview}")

    return 0


def session_sizes_report(
    summary_dir: pathlib.Path,
    sessions_dir: pathlib.Path,
    limit: Optional[int],
    ascending: bool,
    include_details: bool,
    minimum_tokens: int,
    sample_size: int,
    only_sessions: Optional[Iterable[str]],
    include_thresholds: bool,
    thresholds: Optional[List[int]],
    json_mode: bool,
    pretty_json: bool,
) -> int:
    if not summary_dir.exists():
        print(f"Summary directory not found: {summary_dir}", file=sys.stderr)
        return 1

    entries: List[Dict[str, Any]] = []
    only_ids = set(only_sessions) if only_sessions else None
    counter = TokenCounter()

    for summary_path in summary_dir.glob("*.json"):
        if summary_path.name == "index.json":
            continue
        record = _load_json(summary_path, default={})
        if not isinstance(record, dict):
            continue

        session_id = record.get("session_id") or summary_path.stem
        if only_ids and session_id not in only_ids:
            continue
        kept_tokens = record.get("kept_tokens")
        if not isinstance(kept_tokens, int):
            kept_tokens = 0

        if kept_tokens < minimum_tokens:
            continue

        words, pages = _estimate_length(kept_tokens)

        title = record.get("title") or record.get("label") or ""
        truncated = bool(record.get("truncated"))
        model = record.get("model", "unknown")
        prompt_tokens = record.get("usage_input_tokens")
        completion_tokens = record.get("usage_output_tokens")
        payload_sample: Optional[Dict[str, Any]] = None
        thresholds_data: Optional[List[Dict[str, Any]]] = None
        latest_timestamp: Optional[str] = None

        entry: Dict[str, Any] = {
            "session_id": session_id,
            "tokens": kept_tokens,
            "words": words,
            "pages": pages,
            "truncated": truncated,
            "model": model,
            "title": title,
            "prompt_tokens": prompt_tokens if isinstance(prompt_tokens, int) else None,
            "completion_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
        }

        if include_details or include_thresholds:
            session_path = _resolve_session_path(session_id, record, sessions_dir)
            segments: Optional[List[Dict[str, Any]]] = None
            if session_path and session_path.exists():
                segments, _latest_ts, _digest, _meta = _collect_segments(session_path)
            if include_details:
                if segments:
                    payload_sample = _summarise_payload_mix(segments, sample_size)
                else:
                    payload_sample = {
                        "segments_total": 0,
                        "conversation_segments": 0,
                        "type_counts": Counter(),
                    }
            if include_thresholds:
                if segments:
                    thresholds_data, latest_timestamp, transcript_tokens = _compute_threshold_reachback(
                        segments,
                        counter,
                        thresholds or [
                            2_000,
                            5_000,
                            10_000,
                            20_000,
                            40_000,
                            80_000,
                            120_000,
                            160_000,
                            200_000,
                            400_000,
                            800_000,
                            1_200_000,
                            1_600_000,
                        ],
                    )
                else:
                    thresholds_data = []
                    latest_timestamp = None
                    transcript_tokens = 0
            else:
                transcript_tokens = None
        if payload_sample:
            entry["payload_sample"] = payload_sample
        if thresholds_data is not None:
            entry["thresholds"] = thresholds_data
        if latest_timestamp:
            entry["latest_timestamp"] = latest_timestamp
        if include_thresholds and transcript_tokens is not None:
            entry["transcript_tokens"] = transcript_tokens
            transcript_words, transcript_pages = _estimate_length(transcript_tokens)
            entry["transcript_words"] = transcript_words
            entry["transcript_pages"] = transcript_pages

        entries.append(entry)

    if not entries:
        print("No summaries found.")
        return 0

    entries.sort(key=lambda item: item["tokens"], reverse=not ascending)
    if limit is not None:
        entries = entries[:limit]

    display_tokens = sum(entry["tokens"] for entry in entries)
    display_words = sum(entry["words"] for entry in entries)
    total_pages = display_words / 275 if display_words else 0.0

    if json_mode:
        def _serialize_entry(item: Dict[str, Any]) -> Dict[str, Any]:
            payload = dict(item)
            sample = payload.pop("payload_sample", None)
            if isinstance(sample, dict):
                segments_total = sample.get("segments_total") or 0
                type_counts = sample.get("type_counts") or Counter()
                conversation_segments = sample.get("conversation_segments") or 0
                conversation_ratio = (
                    (conversation_segments / segments_total) * 100 if segments_total else 0
                )
                payload["payload_sample"] = {
                    "segments_total": segments_total,
                    "conversation_segments": conversation_segments,
                    "conversation_ratio": conversation_ratio,
                    "top_types": [
                        {
                            "type": label,
                            "count": count,
                            "percentage": (count / segments_total) * 100 if segments_total else 0,
                        }
                        for label, count in type_counts.most_common()
                    ],
                }
            thresholds_data = payload.get("thresholds")
            if thresholds_data:
                payload["thresholds"] = thresholds_data
            return payload

        json_payload = {
            "entries": [_serialize_entry(entry) for entry in entries],
            "totals": {
                "tokens": display_tokens,
                "words": display_words,
                "pages": total_pages,
            },
        }
        dump = json.dumps(json_payload, indent=2 if pretty_json else None)
        print(dump)
        return 0

    header = f"{'Session':<36} {'Tokens':>12} {'Words':>12} {'Pages':>7} {'Trunc':>6} {'Model':>10} Title"
    print(header)
    print("-" * len(header))

    for entry in entries:
        session_label = entry["session_id"]
        if len(session_label) > 35:
            session_label = session_label[:32] + "…"
        title = entry["title"] or "—"
        if len(title) > 48:
            title = title[:45] + "…"

        print(
            f"{session_label:<36} "
            f"{entry['tokens']:>12,} "
            f"{entry['words']:>12,} "
            f"{entry['pages']:>7.1f} "
            f"{('yes' if entry['truncated'] else 'no'):>6} "
            f"{entry['model'][:10]:>10} "
            f"{title}"
        )

        if include_details:
            sample = entry.get("payload_sample") or {}
            segments_total = sample.get("segments_total") or 0
            if segments_total:
                conversation_segments = sample.get("conversation_segments") or 0
                conversation_pct = (conversation_segments / segments_total) * 100
                other_pct = 100 - conversation_pct
                print(
                    f"    Sampled segments: {segments_total} · conversation {conversation_pct:.0f}% / other {other_pct:.0f}%"
                )
                type_counts = sample.get("type_counts") or Counter()
                if isinstance(type_counts, Counter) and type_counts:
                    top_types = type_counts.most_common(3)
                    formatted = ", ".join(
                        f"{payload or 'unknown'} {count / segments_total * 100:.0f}%"
                        for payload, count in top_types
                    )
                    print(f"    Payload mix (sample): {formatted}")

            prompt_tokens = entry.get("prompt_tokens")
            completion_tokens = entry.get("completion_tokens")
            if isinstance(prompt_tokens, int) or isinstance(completion_tokens, int):
                prompt_str = f"prompt {prompt_tokens:,}" if isinstance(prompt_tokens, int) else "prompt n/a"
                completion_str = (
                    f"completion {completion_tokens:,}"
                    if isinstance(completion_tokens, int)
                    else "completion n/a"
                )
                print(f"    Recorded usage: {prompt_str}, {completion_str}")

        if include_thresholds and entry.get("thresholds"):
            latest_ts_iso = entry.get("latest_timestamp")
            latest_ts = _parse_timestamp(latest_ts_iso) if latest_ts_iso else None
            transcript_total = entry.get("transcript_tokens")
            transcript_pages = entry.get("transcript_pages")
            if isinstance(transcript_total, int) and transcript_total > 0:
                print(
                    f"    Transcript total: {transcript_total:,} tokens (~{transcript_pages:.1f} pages)"
                )
            print("    Reachback:")
            for item in entry["thresholds"]:
                tokens = item.get("tokens")
                ts_iso = item.get("timestamp")
                ts = _parse_timestamp(ts_iso) if ts_iso else None
                delta_seconds = item.get("delta_seconds")
                delta_str = "?"
                if delta_seconds is not None:
                    delta_str = _format_timedelta(_dt.timedelta(seconds=delta_seconds))
                elif ts and latest_ts:
                    delta_str = _format_timedelta(latest_ts - ts)
                print(f"      {tokens:>7} tokens -> {ts_iso} (Δ {delta_str})")

    print("\nTotals across listed sessions:")
    print(f"  Tokens ≈ {display_tokens:,} · Words ≈ {display_words:,} · Pages ≈ {total_pages:.1f}")

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Codextendo summarization helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summarize_parser = subparsers.add_parser("summarize", help="Summarize a single Codex session")
    summarize_parser.add_argument("--path", required=True, type=pathlib.Path)
    summarize_parser.add_argument("--label", required=False)
    summarize_parser.add_argument("--model", default=DEFAULT_MODEL)
    summarize_parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    summarize_parser.add_argument(
        "--summary-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codextendo" / "summaries",
    )
    summarize_parser.add_argument(
        "--index",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codextendo" / "summaries" / "index.json",
    )
    summarize_parser.add_argument(
        "--label-file",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("CODEX_LABEL_FILE", pathlib.Path.home() / ".codex" / "search_labels.json")),
    )

    refresh_parser = subparsers.add_parser("refresh", help="Refresh summaries for all sessions")
    refresh_parser.add_argument(
        "--sessions-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codex" / "sessions",
    )
    refresh_parser.add_argument(
        "--summary-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codextendo" / "summaries",
    )
    refresh_parser.add_argument(
        "--index",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codextendo" / "summaries" / "index.json",
    )
    refresh_parser.add_argument("--model", default=DEFAULT_MODEL)
    refresh_parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    refresh_parser.add_argument("--limit", type=int, default=None, help="Only process the newest N sessions")
    refresh_parser.add_argument("--force", action="store_true", help="Rebuild all summaries regardless of cache state")

    sizes_parser = subparsers.add_parser(
        "session-sizes",
        help="Report token, word, and page estimates for existing summaries",
    )
    sizes_parser.add_argument(
        "--summary-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codextendo" / "summaries",
    )
    sizes_parser.add_argument(
        "--sessions-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codex" / "sessions",
    )
    sizes_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only show the top N sessions by token count",
    )
    sizes_parser.add_argument(
        "--ascending",
        action="store_true",
        help="Sort from smallest to largest (default largest first)",
    )
    sizes_parser.add_argument(
        "--min-tokens",
        type=int,
        default=0,
        help="Only include sessions with at least this many kept tokens",
    )
    sizes_parser.add_argument(
        "--details",
        action="store_true",
        help="Include payload breakdown (scans transcript files; slower)",
    )
    sizes_parser.add_argument(
        "--sample-size",
        type=int,
        default=250,
        help="Number of latest segments to sample when computing payload mix",
    )
    sizes_parser.add_argument(
        "--session",
        dest="sessions",
        action="append",
        help="Limit output to a specific session ID (may be repeated)",
    )
    sizes_parser.add_argument(
        "--thresholds",
        action="store_true",
        help="Compute reachback timestamps for predefined token thresholds",
    )
    sizes_parser.add_argument(
        "--threshold",
        dest="threshold_values",
        action="append",
        type=int,
        help="Custom threshold value (tokens). Repeat for multiple thresholds.",
    )
    sizes_parser.add_argument(
        "--json",
        action="store_true",
        help="Output data as JSON",
    )
    sizes_parser.add_argument(
        "--pretty-json",
        action="store_true",
        help="Pretty-print JSON output",
    )

    transcript_parser = subparsers.add_parser(
        "transcript",
        help="Inspect raw transcript segments"
    )
    transcript_parser.add_argument(
        "--session",
        dest="session_id",
        required=True,
        help="Session identifier",
    )
    transcript_parser.add_argument(
        "--sessions-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codex" / "sessions",
    )
    transcript_parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="Page number (1 = newest page when order=newest)",
    )
    transcript_parser.add_argument(
        "--page-size",
        type=int,
        default=200,
        help="Segments per page",
    )
    transcript_parser.add_argument(
        "--order",
        choices=["newest", "oldest"],
        default="newest",
        help="Page ordering",
    )
    transcript_parser.add_argument(
        "--no-tokens",
        action="store_true",
        help="Skip per-segment token counting",
    )
    transcript_parser.add_argument(
        "--json",
        action="store_true",
        help="Return JSON output",
    )
    transcript_parser.add_argument(
        "--pretty-json",
        action="store_true",
        help="Pretty-print JSON output",
    )

    metadata_parser = subparsers.add_parser(
        "metadata",
        help="Backfill session cwd metadata without calling the API",
    )
    metadata_parser.add_argument(
        "--summary-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codextendo" / "summaries",
    )
    metadata_parser.add_argument(
        "--sessions-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codex" / "sessions",
    )
    metadata_parser.add_argument(
        "--index",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codextendo" / "summaries" / "index.json",
    )
    metadata_parser.add_argument(
        "--session",
        dest="sessions",
        action="append",
        help="Limit to a specific session ID (may be repeated)",
    )

    args = parser.parse_args(argv)

    if args.command == "summarize":
        label_map = _read_label_map(args.label_file)
        try:
            json_path, md_path, record = summarize_session(
                path=args.path,
                label=args.label,
                model=args.model,
                max_tokens=args.max_tokens,
                summary_dir=args.summary_dir,
                label_map=label_map,
            )
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        print(f"Summary saved -> {json_path}")
        print(f"Markdown saved -> {md_path}")
        if record.get("truncated"):
            print("(Transcript truncated to stay within the token budget.)")
        history_path = record.get("history_path")
        if history_path:
            print(f"History updated -> {history_path}")
        usage_input = record.get("usage_input_tokens")
        usage_output = record.get("usage_output_tokens")
        usage_total = record.get("usage_total_tokens")
        cached_input = record.get("usage_cached_input_tokens")
        cached_output = record.get("usage_cached_output_tokens")
        if any(isinstance(value, int) for value in (usage_input, usage_output, usage_total, cached_input, cached_output)):
            prompt_segment = "prompt " + (
                f"{usage_input:,}" if isinstance(usage_input, int) else "n/a"
            )
            if isinstance(cached_input, int) and cached_input:
                prompt_segment += f" (cached {cached_input:,})"

            completion_segment = "completion " + (
                f"{usage_output:,}" if isinstance(usage_output, int) else "n/a"
            )
            if isinstance(cached_output, int) and cached_output:
                completion_segment += f" (cached {cached_output:,})"

            total_segment = "total " + (
                f"{usage_total:,}" if isinstance(usage_total, int) else "n/a"
            )

            print("Tokens used -> " + ", ".join([prompt_segment, completion_segment, total_segment]))
        index_path: pathlib.Path = args.index
        index_payload = _load_json(index_path, default={})
        if not isinstance(index_payload, dict):
            index_payload = {}
        index_payload[record["session_id"]] = record
        _ensure_dir(index_path.parent)
        index_path.write_text(json.dumps(index_payload, indent=2, ensure_ascii=False))
        print(f"Index updated -> {index_path}")
        return 0

    if args.command == "refresh":
        try:
            refresh_summaries(
                sessions_dir=args.sessions_dir,
                summary_dir=args.summary_dir,
                index_path=args.index,
                model=args.model,
                max_tokens=args.max_tokens,
                limit=args.limit,
                force=args.force,
            )
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "session-sizes":
        return session_sizes_report(
            summary_dir=args.summary_dir,
            sessions_dir=args.sessions_dir,
            limit=args.limit,
            ascending=args.ascending,
            include_details=args.details,
            minimum_tokens=args.min_tokens,
            sample_size=args.sample_size,
            only_sessions=args.sessions,
            include_thresholds=args.thresholds,
            thresholds=args.threshold_values,
            json_mode=args.json or args.pretty_json,
            pretty_json=args.pretty_json,
        )

    if args.command == "transcript":
        return transcript_chunk(
            session_id=args.session_id,
            sessions_dir=args.sessions_dir,
            page=args.page,
            page_size=args.page_size,
            order=args.order,
            include_tokens=not args.no_tokens,
            json_mode=args.json or args.pretty_json,
            pretty_json=args.pretty_json,
        )

    if args.command == "metadata":
        refresh_metadata(
            sessions_dir=args.sessions_dir,
            summary_dir=args.summary_dir,
            index_path=args.index,
            only_sessions=args.sessions,
        )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
