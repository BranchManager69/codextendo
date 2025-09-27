#!/usr/bin/env bash

# 📓 Codex conversation helpers
CODEXTENDO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEXTENDO_SUMMARY_SCRIPT="$CODEXTENDO_DIR/summaries.py"
codexgrab() {
  local dest=${1:-$HOME/codex-replay}
  mkdir -p "$dest"
  local latest
  latest=$(python3 - <<'PY'
import pathlib
root = pathlib.Path.home() / ".codex" / "sessions"
if not root.exists():
    raise SystemExit()
files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
if not files:
    raise SystemExit()
print(files[0], end="")
PY
)
  if [[ -z "$latest" ]]; then
    echo "No Codex sessions found." >&2
    return 1
  fi
  local base
  base=$(basename "$latest" .jsonl)
  jq -r 'select(.payload.type=="message")
         | .payload.timestamp + " " +
           .payload.role + ": " +
           (reduce .payload.content[]? as $c (""; . + ($c.text // "")))' \
        "$latest" > "$dest/$base.txt"
  echo "Saved -> $dest/$base.txt"
  tail -n 20 "$dest/$base.txt"
}

codexlist() {
  local limit=${1:-10}
  python3 - "$limit" <<'PY'
import pathlib, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

limit = int(sys.argv[1])
root = pathlib.Path.home() / ".codex" / "sessions"

if not root.exists():
    print("No Codex sessions found.")
    raise SystemExit(1)

files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
if not files:
    print("No Codex sessions found.")
    raise SystemExit(1)

for path in files[:limit]:
    ts = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {path}")
PY
}

alias codex-last='codexgrab'
alias codex-list='codexlist'

LABEL_FILE="$HOME/.codex/search_labels.json"

codexsearch() {
  local plain_flag=0
  local auto_open=0
  local open_index=1
  local open_viewer=""
  local open_dest=""
  local open_no_view=0
  local pick_mode=1
  local pick_start=""
  local args=()
  local open_mode="resume"
  local summary_after=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --plain|--no-color)
        plain_flag=1
        shift
        ;;
      --open)
        auto_open=1
        open_index=1
        shift
        ;;
      --open=*)
        auto_open=1
        open_index="${1#*=}"
        shift
        ;;
      --viewer=*|--open-viewer=*)
        open_viewer="${1#*=}"
        shift
        ;;
      --dest=*|--open-dest=*)
        open_dest="${1#*=}"
        shift
        ;;
      --no-view|--open-no-view)
        open_no_view=1
        shift
        ;;
      --summary|--summarize)
        summary_after=1
        shift
        ;;
      --resume|--resume-only)
        open_mode="resume"
        shift
        ;;
      --export-only)
        open_mode="export"
        shift
        ;;
      --open-mode=*)
        open_mode="${1#*=}"
        shift
        ;;
      --open-both|--export)
        open_mode="both"
        shift
        ;;
      --no-pick)
        pick_mode=0
        shift
        ;;
      --pick)
        pick_mode=1
        pick_start=""
        shift
        ;;
      --pick=*)
        pick_mode=1
        pick_start="${1#*=}"
        shift
        ;;
      --help|-h)
        echo "Usage: codexsearch [--plain] [--no-pick] [--pick[=index]] [--open[=index]] [--viewer=<viewer>] [--dest=<dir>] [--no-view] [--summary] <pattern> [limit]" >&2
        return 0
        ;;
      --)
        shift
        while [[ $# -gt 0 ]]; do
          args+=("$1")
          shift
        done
        break
        ;;
      *)
        args+=("$1")
        shift
        ;;
    esac
  done

  if [[ ${#args[@]} -lt 1 ]]; then
    echo "Usage: codexsearch [--plain] [--no-pick] [--pick[=index]] [--open[=index]] [--viewer=<viewer>] [--dest=<dir>] [--no-view] [--summary] <pattern> [limit]" >&2
    return 1
  fi

  local pattern="${args[0]}"
  local limit="${args[1]:-5}"

  CODEX_LABEL_FILE="$LABEL_FILE" python3 - "$pattern" "$limit" "$plain_flag" <<'PY'
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

pattern = sys.argv[1]
limit = int(sys.argv[2])
plain_flag = sys.argv[3] == "1"

if limit < 1:
    print("Limit must be at least 1.")
    raise SystemExit(1)

def normalize(text: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return " ".join(tokens)

needle = normalize(pattern)

if not needle:
    print("Pattern is too short.")
    raise SystemExit(1)

root = pathlib.Path.home() / ".codex" / "sessions"

if not root.exists():
    print("No Codex sessions found.")
    raise SystemExit(1)

files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

if not files:
    print("No Codex sessions found.")
    raise SystemExit(1)

prompt_markers = (
    "branchmanager@",
    "codex-search",
    "source ~/.bash_aliases",
)

use_color = (
    not plain_flag
    and sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
    and os.environ.get("TERM") not in (None, "dumb")
)

def paint(text: str, code: str) -> str:
    if not use_color:
        return text
    return f"[{code}m{text}[0m"

def highlight_snippet(text: str) -> str:
    if not use_color:
        return text
    try:
        exact = re.compile(re.escape(pattern), re.IGNORECASE)
    except re.error:
        exact = None
    if exact and exact.search(text):
        return exact.sub(lambda m: paint(m.group(0), "1;33"), text)
    for word in pattern.split():
        try:
            finder = re.compile(re.escape(word), re.IGNORECASE)
        except re.error:
            continue
        text = finder.sub(lambda m: paint(m.group(0), "1;33"), text)
    return text

ET = ZoneInfo("America/New_York")

def format_relative(delta):
    seconds = int(delta.total_seconds())
    if seconds == 0:
        return "just now"
    future = seconds < 0
    seconds = abs(seconds)
    units = []
    for length, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        value, seconds = divmod(seconds, length)
        if value:
            units.append(f"{value}{suffix}")
        if len(units) == 2:
            break
    if not units:
        units.append(f"{seconds}s")
    phrase = " ".join(units)
    return f"in {phrase}" if future else f"{phrase} ago"

def format_timestamp(iso_stamp):
    when = datetime.fromisoformat(iso_stamp)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when_et = when.astimezone(ET)
    now_et = datetime.now(tz=ET)
    rel = format_relative(now_et - when_et)
    hour = when_et.hour % 12 or 12
    minute = when_et.minute
    ampm = "AM" if when_et.hour < 12 else "PM"
    display = f"{when_et.strftime('%b')} {when_et.day} {hour}:{minute:02d} {ampm} ET"
    return display, rel



def load_labels():
    label_path = os.environ.get("CODEX_LABEL_FILE")
    if not label_path:
        label_path = str(pathlib.Path.home() / ".codex" / "search_labels.json")
    try:
        with open(label_path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

label_map = load_labels()

matches = []

for path in files:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = data.get("payload", {})
                if payload.get("type") != "message":
                    continue
                chunks = payload.get("content") or []
                text = "".join(chunk.get("text", "") for chunk in chunks if isinstance(chunk, dict))
                if not text:
                    continue
                role = payload.get("role", "unknown")
                lower_text = text.lower()
                if role == "user" and any(marker in lower_text for marker in prompt_markers):
                    continue
                norm = normalize(text)
                if needle not in norm:
                    continue
                raw_ts = payload.get("timestamp")
                stamp = None
                if raw_ts:
                    try:
                        stamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=timezone.utc)
                    except Exception:
                        stamp = None
                if stamp is None:
                    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                idx = lower_text.find(pattern.lower())
                if idx == -1:
                    idx = next((lower_text.find(word) for word in pattern.lower().split() if word in lower_text), -1)
                if idx == -1:
                    idx = 0
                start = max(0, idx - 180)
                end = min(len(text), idx + len(pattern) + 180)
                snippet_raw = text[start:end].strip()
                snippet = " ".join(snippet_raw.split())
                matches.append({
                    "timestamp": stamp.isoformat(),
                    "role": role,
                    "path": str(path),
                    "snippet": snippet,
                    "label": label_map.get(str(path)),
                })
                break
    except (UnicodeDecodeError, OSError):
        continue
    if len(matches) >= limit:
        break

if not matches:
    print("No Codex sessions found.")
    raise SystemExit(1)

sys.stdout.write("\n" * 3)

cache_dir = pathlib.Path.home() / ".codex"
cache_dir.mkdir(parents=True, exist_ok=True)
cache_file = cache_dir / "search_last.json"
cache_file.write_text(json.dumps(matches, indent=2))

for idx, entry in enumerate(matches, start=1):
    if idx > 1:
        print()
    index_str = paint(f"{idx}.", "1;32")
    timestamp_display, relative_display = format_timestamp(entry["timestamp"])
    if relative_display and relative_display != "just now":
        stamp_text = f"{timestamp_display} ({relative_display})"
    else:
        stamp_text = timestamp_display
    ts_str = paint(stamp_text, "36")
    snippet_str = highlight_snippet(entry["snippet"])
    prefix = paint('...', '90') if use_color else '...'
    label = entry.get("label")
    if label:
        label_display = paint(f"[{label}]", "1;34") if use_color else f"[{label}]"
        print(f"{index_str} {ts_str} {label_display}")
    else:
        print(f"{index_str} {ts_str}")
    print(f"   {prefix} {snippet_str}")

print()

PY
  local status=$?
  if (( status != 0 )); then
    return $status
  fi

  printf '
'

  if (( auto_open )); then
    local open_status=0
    if [[ "$open_mode" == "export" || "$open_mode" == "both" ]]; then
      local open_cmd=(codexopen "$open_index")
      [[ -n "$open_dest" ]] && open_cmd+=("--dest=$open_dest")
      [[ -n "$open_viewer" ]] && open_cmd+=("--viewer=$open_viewer")
      (( open_no_view )) && open_cmd+=("--no-view")
      "${open_cmd[@]}" || open_status=$?
    fi
    if [[ "$open_mode" == "resume" || "$open_mode" == "both" ]]; then
      codexresume "$open_index" || open_status=$?
    fi
    if (( summary_after )); then
      codexsummarize "$open_index" || open_status=$?
    fi
    return $open_status
  fi

  if (( pick_mode )); then
    local tty_device="${SSH_TTY:-/dev/tty}"
    if [[ ! -t 1 && ! -t 0 && ! -e "$tty_device" ]]; then
      printf 'Interactive picker unavailable (no usable TTY). Showing results only.
' >&2
      return 0
    fi

    local selection=""
    local prompt_label
    if (( plain_flag )); then
      prompt_label=$'\nSelect result # (press Enter to keep results listed without opening): '
    else
      prompt_label=$'\n\033[1m\033[48;5;63m\033[38;5;231m SELECT RESULT # \033[0m Type the number to open (press Enter to keep results listed without opening): '
    fi
    if IFS= read -r -p "$prompt_label" selection < "$tty_device"; then
      if [[ -n "$selection" ]]; then
        if [[ "$selection" =~ ^[0-9]+$ ]]; then
          open_index=$selection
          auto_open=1
        else
          printf 'Invalid selection: %s
' "$selection" >&2
          return 1
        fi
      fi
    fi

    if (( auto_open )); then
      local open_status=0
      if [[ "$open_mode" == "export" || "$open_mode" == "both" ]]; then
        local open_cmd=(codexopen "$open_index")
        [[ -n "$open_dest" ]] && open_cmd+=("--dest=$open_dest")
        [[ -n "$open_viewer" ]] && open_cmd+=("--viewer=$open_viewer")
        (( open_no_view )) && open_cmd+=("--no-view")
        "${open_cmd[@]}" || open_status=$?
      fi
      if [[ "$open_mode" == "resume" || "$open_mode" == "both" ]]; then
        codexresume "$open_index" || open_status=$?
      fi
      if (( summary_after )); then
        codexsummarize "$open_index" || open_status=$?
      fi
      return $open_status
    fi
  fi

  return 0
}

alias codex-search='codexsearch'

codexlabel() {
  local usage='Usage: codexlabel [--clear] <result #> "title"'
  local clear_flag=0
  local index=""
  local title=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --clear)
        clear_flag=1
        shift
        ;;
      --help|-h)
        echo "$usage"
        return 0
        ;;
      --)
        shift
        break
        ;;
      -* )
        echo "Unknown option: $1" >&2
        echo "$usage" >&2
        return 1
        ;;
      *)
        break
        ;;
    esac
  done

  if (( clear_flag )); then
    if [[ $# -lt 1 ]]; then
      echo "$usage" >&2
      return 1
    fi
    index="$1"
    shift
    title="$*"
  else
    if [[ $# -lt 2 ]]; then
      echo "$usage" >&2
      return 1
    fi
    index="$1"
    shift
    title="$*"
    if [[ -z "$title" ]]; then
      echo 'Title cannot be blank.' >&2
      return 1
    fi
  fi

  if [[ ! $index =~ ^[0-9]+$ ]]; then
    echo 'Result # must be a positive integer.' >&2
    return 1
  fi

  CODEX_LABEL_FILE="$LABEL_FILE" python3 - "$index" "$title" "$clear_flag" <<'PY'
import json
import os
import pathlib
import sys

try:
    idx = int(sys.argv[1])
except ValueError:
    print('Result # must be a number.', file=sys.stderr)
    raise SystemExit(1)

raw_title = sys.argv[2] if len(sys.argv) > 2 else ''
clear_mode = len(sys.argv) > 3 and sys.argv[3] == '1'

if clear_mode:
    title = ''
else:
    title = raw_title.strip()
    if not title:
        print('Title cannot be blank.', file=sys.stderr)
        raise SystemExit(1)

cache_file = pathlib.Path.home() / '.codex' / 'search_last.json'
if not cache_file.exists():
    print('No cached search results. Run codexsearch first.', file=sys.stderr)
    raise SystemExit(1)

try:
    matches = json.loads(cache_file.read_text())
except json.JSONDecodeError:
    print('Cached search results are corrupted. Run codexsearch again.', file=sys.stderr)
    raise SystemExit(1)

if not matches:
    print('No cached search results.', file=sys.stderr)
    raise SystemExit(1)

if idx < 1 or idx > len(matches):
    print(f'Index out of range (1-{len(matches)}).', file=sys.stderr)
    raise SystemExit(1)

entry = matches[idx - 1]
path_str = entry.get('path')
if not path_str:
    print('Selected result has no path information.', file=sys.stderr)
    raise SystemExit(1)

label_path = os.environ.get('CODEX_LABEL_FILE')
if not label_path:
    label_path = str(pathlib.Path.home() / '.codex' / 'search_labels.json')
label_file = pathlib.Path(label_path)
label_file.parent.mkdir(parents=True, exist_ok=True)

try:
    labels = json.loads(label_file.read_text()) if label_file.exists() else {}
except json.JSONDecodeError:
    labels = {}

current_label = labels.get(path_str)

if clear_mode:
    removed = labels.pop(path_str, None)
    label_file.write_text(json.dumps(labels, indent=2, ensure_ascii=False))
    entry.pop('label', None)
    entry['label'] = None
    cache_file.write_text(json.dumps(matches, indent=2, ensure_ascii=False))
    if removed:
        print(f"Cleared label '{removed}' for {path_str}")
    else:
        print(f"No label to clear for {path_str} (none was set)")
    raise SystemExit(0)

others = {p: v for p, v in labels.items() if p != path_str and v}
base_title = title
note = ''
if current_label == base_title:
    final_title = current_label
else:
    if base_title in others.values():
        counter = 2
        existing = set(others.values())
        while True:
            candidate = f"{base_title} ({counter})"
            if candidate not in existing:
                final_title = candidate
                note = f" (renamed to '{candidate}' because '{base_title}' was already used)"
                break
            counter += 1
    else:
        final_title = base_title

labels[path_str] = final_title
label_file.write_text(json.dumps(labels, indent=2, ensure_ascii=False))

entry['label'] = final_title
cache_file.write_text(json.dumps(matches, indent=2, ensure_ascii=False))

print(f"Saved label '{final_title}' for {path_str}{note}")
PY
}

alias codex-label='codexlabel'

codexsummarize() {
  local usage='Usage: codexsummarize [--path <session.jsonl>] [--model MODEL] [--max-tokens N] [result #]'
  local session_path=""
  local model=""
  local max_tokens=""
  local explicit_label=""
  local index=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --path=*)
        session_path="${1#*=}"
        shift
        ;;
      --path)
        session_path="$2"
        shift 2
        ;;
      --label=*)
        explicit_label="${1#*=}"
        shift
        ;;
      --label)
        explicit_label="$2"
        shift 2
        ;;
      --model=*)
        model="${1#*=}"
        shift
        ;;
      --model)
        model="$2"
        shift 2
        ;;
      --max-tokens=*)
        max_tokens="${1#*=}"
        shift
        ;;
      --max-tokens)
        max_tokens="$2"
        shift 2
        ;;
      --help|-h)
        echo "$usage"
        return 0
        ;;
      --)
        shift
        break
        ;;
      -* )
        echo "$usage" >&2
        return 1
        ;;
      *)
        if [[ -z "$index" ]]; then
          index="$1"
        else
          echo "$usage" >&2
          return 1
        fi
        shift
        ;;
    esac
  done

  if [[ -n "$session_path" && ! -e "$session_path" ]]; then
    echo "Session file not found: $session_path" >&2
    return 1
  fi

  if [[ -z "$session_path" ]]; then
    if [[ -z "$index" ]]; then
      index=1
    fi
    local tmpfile
    tmpfile=$(mktemp)
    CODEX_LABEL_FILE="$LABEL_FILE" python3 - "$index" <<'PY' > "$tmpfile"
import json
import os
import pathlib
import sys

try:
    idx = int(sys.argv[1])
except (ValueError, IndexError):
    print('Result # must be a number.', file=sys.stderr)
    raise SystemExit(1)

cache_file = pathlib.Path.home() / '.codex' / 'search_last.json'
if not cache_file.exists():
    print('No cached search results. Run codexsearch first.', file=sys.stderr)
    raise SystemExit(1)

try:
    matches = json.loads(cache_file.read_text())
except json.JSONDecodeError:
    print('Cached search results are corrupted. Run codexsearch again.', file=sys.stderr)
    raise SystemExit(1)

if not matches:
    print('No cached search results.', file=sys.stderr)
    raise SystemExit(1)

if idx < 1 or idx > len(matches):
    print(f"Index out of range (1-{len(matches)}).", file=sys.stderr)
    raise SystemExit(1)

entry = matches[idx - 1]
path_str = entry.get('path')
if not path_str:
    print('Selected result has no path information.', file=sys.stderr)
    raise SystemExit(1)

label = entry.get('label') or ''
print(path_str)
print(label)
PY
    local status=$?
    if (( status != 0 )); then
      rm -f "$tmpfile"
      return $status
    fi
    mapfile -t _summary_meta < "$tmpfile"
    rm -f "$tmpfile"
    session_path="${_summary_meta[0]}"
    if [[ -z "$explicit_label" && -n "${_summary_meta[1]}" ]]; then
      explicit_label="${_summary_meta[1]}"
    fi
  fi

  if [[ -z "$session_path" ]]; then
    echo "Unable to determine session path." >&2
    return 1
  fi

  local summary_script="${CODEXTENDO_SUMMARY_SCRIPT:-}"
  if [[ -z "$summary_script" || ! -f "$summary_script" ]]; then
    echo "Summary helper script not found (expected $CODEXTENDO_SUMMARY_SCRIPT)." >&2
    return 1
  fi

  local cmd=(python3 "$summary_script" summarize --path "$session_path")
  if [[ -n "$explicit_label" ]]; then
    cmd+=(--label "$explicit_label")
  fi
  if [[ -n "$model" ]]; then
    cmd+=(--model "$model")
  fi
  if [[ -n "$max_tokens" ]]; then
    cmd+=(--max-tokens "$max_tokens")
  fi
  if [[ -n "$LABEL_FILE" ]]; then
    cmd+=(--label-file "$LABEL_FILE")
  fi

  "${cmd[@]}"
}

codextendo_refresh() {
  local usage='Usage: codextendo refresh [--limit N] [--force] [--model MODEL] [--max-tokens N]'
  local limit=""
  local force=0
  local model=""
  local max_tokens=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --limit=*)
        limit="${1#*=}"
        shift
        ;;
      --limit)
        limit="$2"
        shift 2
        ;;
      --model=*)
        model="${1#*=}"
        shift
        ;;
      --model)
        model="$2"
        shift 2
        ;;
      --max-tokens=*)
        max_tokens="${1#*=}"
        shift
        ;;
      --max-tokens)
        max_tokens="$2"
        shift 2
        ;;
      --force)
        force=1
        shift
        ;;
      --help|-h)
        echo "$usage"
        return 0
        ;;
      --)
        shift
        break
        ;;
      -* )
        echo "$usage" >&2
        return 1
        ;;
      *)
        echo "$usage" >&2
        return 1
        ;;
    esac
  done

  local summary_script="${CODEXTENDO_SUMMARY_SCRIPT:-}"
  if [[ -z "$summary_script" || ! -f "$summary_script" ]]; then
    echo "Summary helper script not found (expected $CODEXTENDO_SUMMARY_SCRIPT)." >&2
    return 1
  fi

  local cmd=(python3 "$summary_script" refresh)
  if [[ -n "$limit" ]]; then
    cmd+=(--limit "$limit")
  fi
  if (( force )); then
    cmd+=(--force)
  fi
  if [[ -n "$model" ]]; then
    cmd+=(--model "$model")
  fi
  if [[ -n "$max_tokens" ]]; then
    cmd+=(--max-tokens "$max_tokens")
  fi

  "${cmd[@]}"
}

codexresume() {
  local index=${1:-1}
  local resume_snippet=""
  local resume_label=""

  local _resume_meta
  mapfile -t _resume_meta < <(python3 - "$index" <<'PY'
import json
import pathlib
import sys

try:
    idx = int(sys.argv[1])
except ValueError:
    print('Index must be a number.', file=sys.stderr)
    raise SystemExit(1)

cache_file = pathlib.Path.home() / '.codex' / 'search_last.json'
if not cache_file.exists():
    print('No cached search results. Run codexsearch first.', file=sys.stderr)
    raise SystemExit(1)

try:
    matches = json.loads(cache_file.read_text())
except json.JSONDecodeError:
    print('Cached search results are corrupted. Run codexsearch again.', file=sys.stderr)
    raise SystemExit(1)

if not matches:
    print('No cached search results.', file=sys.stderr)
    raise SystemExit(1)

if idx < 1 or idx > len(matches):
    print(f"Index out of range (1-{len(matches)}).", file=sys.stderr)
    raise SystemExit(1)

entry = matches[idx - 1]
path_str = entry.get('path')
if not path_str:
    print('Selected result has no path information.', file=sys.stderr)
    raise SystemExit(1)

stem = pathlib.Path(path_str).stem
parts = stem.split('-')
uuid_parts = parts[-5:]
if len(uuid_parts) != 5 or any(not part for part in uuid_parts):
    print(f'Cannot extract session id from {stem}', file=sys.stderr)
    raise SystemExit(1)

session_id = '-'.join(uuid_parts)
snippet = entry.get('snippet') or ''
label = entry.get('label') or ''

print(session_id)
print(snippet)
print(label)
PY
) || return 1

  local session_id="${_resume_meta[0]//$'\r'/}"
  session_id=${session_id//$'\n'/}
  if [[ -z "$session_id" ]]; then
    echo "Unable to derive session id." >&2
    return 1
  fi

  resume_snippet="${_resume_meta[1]-}"
  resume_snippet=${resume_snippet//$'\r'/}
  resume_snippet="${resume_snippet//$'\n'/ }"
  resume_label="${_resume_meta[2]-}"
  resume_label=${resume_label//$'\r'/}
  resume_label=${resume_label//$'\n'/}

  if ! command -v codex >/dev/null 2>&1; then
    echo "codex CLI not found; skipping resume." >&2
    return 1
  fi

  local resume_prompt_disabled=${CODEXTENDO_RESUME_PROMPT_DISABLED:-0}
  local resume_prompt=""
  local resume_query="${CODEXTENDO_RESUME_QUERY:-}"

  if (( ! resume_prompt_disabled )); then
    if [[ -n "${CODEXTENDO_RESUME_PROMPT_OVERRIDE:-}" ]]; then
      resume_prompt=${CODEXTENDO_RESUME_PROMPT_OVERRIDE}
    elif [[ -n "${CODEXTENDO_RESUME_PROMPT:-}" ]]; then
      resume_prompt=${CODEXTENDO_RESUME_PROMPT}
    else
      resume_prompt=$'Hey, we got disconnected and my memory is foggy. Please give me a quick, easy-to-digest catch-up:\n- Past: what we just did or decided.\n- Present: where things stand right now and any blockers.\n- Future: what we planned or should tackle next.\n\nHighlight any files, commands, or links I should reopen.'
    fi

    if [[ -n "$resume_prompt" && -z "${CODEXTENDO_RESUME_PROMPT_OVERRIDE:-}" ]]; then
      local context=""
      if [[ -n "$resume_snippet" ]]; then
        context="\n\nContext I last saw: \"$resume_snippet\""
      fi
      if [[ -n "$resume_label" ]]; then
        resume_prompt+=$'\n\n'
        resume_prompt+="(Resumed from labeled session: '$resume_label')"
      fi
      if [[ -n "$resume_query" ]]; then
        resume_prompt+=$'\n\n'
        resume_prompt+="(Search query: \"$resume_query\")"
      fi
      resume_prompt+=$context
    fi
  fi

  printf 'Resuming Codex session %s\n' "$session_id"
  if (( resume_prompt_disabled )) || [[ -z "$resume_prompt" ]]; then
    codex resume "$session_id"
  else
    codex resume "$session_id" "$resume_prompt"
  fi
}

codexopen() {
  local index=${1:-1}
  shift

  local dest="$HOME/codex-replay"
  local viewer="less"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-view)
        viewer="none"
        shift
        ;;
      --viewer=*)
        viewer="${1#*=}"
        shift
        ;;
      --dest=*)
        dest="${1#*=}"
        shift
        ;;
      *)
        dest="$1"
        shift
        ;;
    esac
  done

  local outfile
  outfile=$(CODEX_LABEL_FILE="$LABEL_FILE" python3 - "$index" "$dest" <<'PY'
import json
import os
import pathlib
import re
import sys

try:
    idx = int(sys.argv[1])
except ValueError:
    print("Index must be a number.", file=sys.stderr)
    raise SystemExit(1)

dest = pathlib.Path(sys.argv[2]).expanduser()
cache_file = pathlib.Path.home() / ".codex" / "search_last.json"

if not cache_file.exists():
    print("No cached search results. Run codexsearch first.", file=sys.stderr)
    raise SystemExit(1)

try:
    matches = json.loads(cache_file.read_text())
except json.JSONDecodeError:
    print("Cached search results are corrupted. Run codexsearch again.", file=sys.stderr)
    raise SystemExit(1)

if not matches:
    print("No cached search results.", file=sys.stderr)
    raise SystemExit(1)

if idx < 1 or idx > len(matches):
    print(f"Index out of range (1-{len(matches)}).", file=sys.stderr)
    raise SystemExit(1)

entry = matches[idx - 1]
path = pathlib.Path(entry["path"])

if not path.exists():
    print(f"Session file not found: {path}", file=sys.stderr)
    raise SystemExit(1)

dest.mkdir(parents=True, exist_ok=True)

label_path = os.environ.get("CODEX_LABEL_FILE")
if not label_path:
    label_path = str(pathlib.Path.home() / ".codex" / "search_labels.json")
label_file = pathlib.Path(label_path)
try:
    label_map = json.loads(label_file.read_text()) if label_file.exists() else {}
except json.JSONDecodeError:
    label_map = {}

label = label_map.get(str(path))

base_name = path.stem
if label:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_")
    if safe:
        base_name = f"{base_name}-{safe}"

output = dest / f"{base_name}.txt"

counter = 2
while output.exists():
    output = dest / f"{base_name}_{counter}.txt"
    counter += 1

turns = []
with path.open("r", encoding="utf-8") as fh:
    for raw in fh:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        payload = data.get("payload", {})
        if payload.get("type") != "message":
            continue
        chunks = payload.get("content") or []
        text = "".join(chunk.get("text", "") for chunk in chunks if isinstance(chunk, dict))
        timestamp = payload.get("timestamp", "")
        role = payload.get("role", "unknown")
        body = text.strip()
        if body:
            formatted = f"{timestamp} {role}:\n{body}"
        else:
            formatted = f"{timestamp} {role}".strip()
        if formatted:
            turns.append(formatted)

if not turns:
    print("No message content found in session.", file=sys.stderr)
    raise SystemExit(1)

output.write_text("\n\n".join(turns) + "\n")

print(output)
PY
  ) || return 1

  outfile=${outfile//$'\n'/}
  if [[ -z "$outfile" ]]; then
    echo "Export failed." >&2
    return 1
  fi

  echo "Saved -> $outfile"

  if [[ "$viewer" == "none" ]]; then
    return 0
  fi

  if command -v "$viewer" >/dev/null 2>&1; then
    "$viewer" "$outfile"
  else
    less -R "$outfile"
  fi
}

alias codex-open='codexopen'

codextendo() {
  local open_mode="resume"
  local limit_override=""
  local pattern=""
  local positional_limit=""
  local search_opts=()
  local summary_flag=0
  local resume_prompt_override=""
  local resume_prompt_override_set=0
  local resume_prompt_disabled=0
  local resume_prompt_disabled_set=0

  if [[ $# -gt 0 && "$1" == "refresh" ]]; then
    shift
    codextendo_refresh "$@"
    return $?
  fi

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --export|--both)
        open_mode="both"
        shift
        ;;
      --resume-only)
        open_mode="resume"
        shift
        ;;
      --export-only)
        open_mode="export"
        shift
        ;;
      --summarize)
        summary_flag=1
        shift
        ;;
      --summary|--summary-only)
        summary_flag=1
        open_mode="none"
        shift
        ;;
      --summarize-only)
        summary_flag=1
        open_mode="none"
        shift
        ;;
      --limit=*)
        limit_override="${1#*=}"
        shift
        ;;
      --limit)
        limit_override="$2"
        shift 2
        ;;
      --plain|--no-color)
        search_opts+=("$1")
        shift
        ;;
      --resume-prompt)
        if [[ $# -lt 2 ]]; then
          echo "--resume-prompt requires a value" >&2
          return 1
        fi
        resume_prompt_override_set=1
        resume_prompt_override="$2"
        resume_prompt_disabled=0
        resume_prompt_disabled_set=1
        shift 2
        ;;
      --resume-prompt=*)
        resume_prompt_override_set=1
        resume_prompt_override="${1#*=}"
        resume_prompt_disabled=0
        resume_prompt_disabled_set=1
        shift
        ;;
      --no-resume-prompt)
        resume_prompt_disabled=1
        resume_prompt_disabled_set=1
        resume_prompt_override=""
        resume_prompt_override_set=1
        shift
        ;;
      --help|-h)
        cat <<'USAGE'
Usage: codextendo [options] <query> [limit]

Options:
  --export           Also open the transcript export viewer after resuming.
  --export-only      Open the export viewer without resuming the session.
  --resume-only      Resume only (default).
  --summarize        Generate a structured summary in addition to the chosen open mode.
  --summarize-only   Generate a summary without resuming or exporting the session.
  --resume-prompt X  Send X as the first message after resuming (overrides default).
  --no-resume-prompt Skip sending the automatic catch-up request when resuming.
  --limit N          Limit results to N entries (can also pass as positional argument).
  --plain            Disable colors in output (passthrough to codexsearch).
USAGE
        return 0
        ;;
      --*)
        search_opts+=("$1")
        shift
        ;;
      *)
        if [[ -z "$pattern" ]]; then
          pattern="$1"
        elif [[ -z "$positional_limit" ]]; then
          positional_limit="$1"
        else
          search_opts+=("$1")
        fi
        shift
        ;;
    esac
  done

  if [[ -z "$pattern" ]]; then
    echo "Usage: codextendo [options] <query> [limit]" >&2
    return 1
  fi

  local limit_arg="$limit_override"
  if [[ -z "$limit_arg" && -n "$positional_limit" ]]; then
    limit_arg="$positional_limit"
  fi

  local search_args=("--open-mode=$open_mode")
  if (( summary_flag )); then
    search_args+=("--summary")
  fi
  search_args+=("${search_opts[@]}")
  search_args+=("$pattern")
  if [[ -n "$limit_arg" ]]; then
    search_args+=("$limit_arg")
  fi

  local _prev_prompt_override_set=0
  local _prev_prompt_override_value=""
  if [[ -v CODEXTENDO_RESUME_PROMPT_OVERRIDE ]]; then
    _prev_prompt_override_set=1
    _prev_prompt_override_value="$CODEXTENDO_RESUME_PROMPT_OVERRIDE"
  fi

  local _prev_prompt_disabled_set=0
  local _prev_prompt_disabled_value=""
  if [[ -v CODEXTENDO_RESUME_PROMPT_DISABLED ]]; then
    _prev_prompt_disabled_set=1
    _prev_prompt_disabled_value="$CODEXTENDO_RESUME_PROMPT_DISABLED"
  fi

  local _prev_resume_query_set=0
  local _prev_resume_query_value=""
  if [[ -v CODEXTENDO_RESUME_QUERY ]]; then
    _prev_resume_query_set=1
    _prev_resume_query_value="$CODEXTENDO_RESUME_QUERY"
  fi

  local _modified_override=0
  if (( resume_prompt_override_set )); then
    CODEXTENDO_RESUME_PROMPT_OVERRIDE="$resume_prompt_override"
    _modified_override=1
  fi

  local _modified_disabled=0
  if (( resume_prompt_disabled_set )); then
    if (( resume_prompt_disabled )); then
      CODEXTENDO_RESUME_PROMPT_DISABLED=1
    else
      unset CODEXTENDO_RESUME_PROMPT_DISABLED
    fi
    _modified_disabled=1
  fi

  CODEXTENDO_RESUME_QUERY="$pattern"

  codexsearch "${search_args[@]}"
  local search_status=$?

  if (( _modified_override )); then
    if (( _prev_prompt_override_set )); then
      CODEXTENDO_RESUME_PROMPT_OVERRIDE="$_prev_prompt_override_value"
    else
      unset CODEXTENDO_RESUME_PROMPT_OVERRIDE
    fi
  fi

  if (( _modified_disabled )); then
    if (( _prev_prompt_disabled_set )); then
      CODEXTENDO_RESUME_PROMPT_DISABLED="$_prev_prompt_disabled_value"
    else
      unset CODEXTENDO_RESUME_PROMPT_DISABLED
    fi
  fi

  if (( _prev_resume_query_set )); then
    CODEXTENDO_RESUME_QUERY="$_prev_resume_query_value"
  else
    unset CODEXTENDO_RESUME_QUERY
  fi

  return $search_status
}
