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
import datetime as _dt
import hashlib
import json
import os
import pathlib
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # Optional dependency for precise token accounting.
    import tiktoken  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback handled at runtime
    tiktoken = None

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - surfaced in CLI
    print("Missing Python package 'requests'. Install it with 'pip install requests'.", file=sys.stderr)
    raise


DEFAULT_MODEL = os.environ.get("CODEXTENDO_SUMMARY_MODEL", "gpt-5")
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


def _collect_segments(path: pathlib.Path) -> Tuple[List[Dict], Optional[_dt.datetime], str]:
    segments: List[Dict] = []
    latest_ts: Optional[_dt.datetime] = None
    digest = hashlib.sha256()

    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            payload = data.get("payload") or {}
            ptype = payload.get("type")
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

    return segments, latest_ts, digest.hexdigest()


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


def _request_summary(model: str, system_prompt: str, user_prompt: str) -> Dict:
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
        "max_output_tokens": 2048,
    }

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

    for block in data.get("output", []):
        for piece in block.get("content", []):
            if piece.get("type") == "output_json":
                return piece.get("json")
            if piece.get("type") == "output_text":
                try:
                    return json.loads(piece.get("text", ""))
                except json.JSONDecodeError:
                    continue

    raise RuntimeError("Failed to parse summary from model response.")


def _write_summary(session_id: str, label: Optional[str], model: str, truncated: bool,
                   kept_tokens: int, original_digest: str, summary_payload: Dict,
                   summary_dir: pathlib.Path) -> Tuple[pathlib.Path, pathlib.Path, Dict]:
    generated_at = _now_utc().isoformat()
    record = {
        "session_id": session_id,
        "label": label,
        "generated_at": generated_at,
        "model": model,
        "truncated": truncated,
        "kept_tokens": kept_tokens,
        "digest": original_digest,
        **summary_payload,
    }

    summary_dir = _ensure_dir(summary_dir)
    json_path = summary_dir / f"{session_id}.json"
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))

    md_lines = [f"# Summary for {session_id}", f"Generated: {generated_at}"]
    if label:
        md_lines.append(f"Label: {label}")
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

    segments, latest_ts, digest = _collect_segments(path)
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

    summary_payload = _request_summary(model=model, system_prompt=system_prompt, user_prompt=user_prompt)

    json_path, md_path, record = _write_summary(
        session_id=session_id,
        label=label,
        model=model,
        truncated=truncated,
        kept_tokens=kept_tokens,
        original_digest=digest,
        summary_payload=summary_payload,
        summary_dir=summary_dir,
    )

    record.update(
        {
            "session_path": str(path),
            "latest_timestamp": latest_ts.isoformat() if latest_ts else None,
        }
    )

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

    for path in sessions:
        session_id = _derive_session_id(path)
        entry = index_data.get(session_id)
        if force or entry is None:
            to_process.append(path)
            continue

        recorded_digest = entry.get("digest")
        recorded_mtime = entry.get("latest_timestamp")

        segments, latest_ts, digest = _collect_segments(path)
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

    _ensure_dir(index_path.parent)
    index_path.write_text(json.dumps(index_data, indent=2, ensure_ascii=False))


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

    return 0


if __name__ == "__main__":
    sys.exit(main())
