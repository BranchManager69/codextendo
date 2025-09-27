# Codextendo

Codextendo is a set of Bash helpers that make it painless to find, resume, and summarise OpenAI Codex CLI sessions. It layers a smart picker on top of `codex resume`, turns conversations into structured JSON/Markdown summaries, and keeps an append-only history so you never lose context.

## Features
- **One-liner resume:** `codextendo <query>` searches your Codex transcripts, lets you pick a result, and re-opens it in the Codex CLI with an automatic Past/Present/Future catch-up prompt (so the assistant reminds you what you were doing).
- **Rich summaries:** `codextendo --summary` (and `--summarize`) writes JSON + Markdown summaries, appends a Markdown history timeline, and stores everything in `~/.codextendo/summaries/`.
- **Batch refresh:** `codextendo refresh` rebuilds summaries for every session, skipping unchanged files by using a local hash cache.
- **Supporting helpers:** `codexsearch`, `codexlabel`, `codexopen`, `codex-list`, and `codex-last` cover fuzzy search, labelling, exporting, and quick listing.
- **Optional UI:** the companion Next.js dashboard under `~/websites/codextendo-dashboard` reads the same summaries for a browser view.

## Requirements
- Bash (or another shell that can source Bash functions)
- Python 3.9+
- [`jq`](https://stedolan.github.io/jq/) for transcript processing
- [`requests`](https://pypi.org/project/requests/) Python package (used by the summariser)
- [`tiktoken`](https://pypi.org/project/tiktoken/) _(optional, but recommended for precise token counting)_
- `OPENAI_API_KEY` environment variable (needed for summaries)

## Installation
```bash
# clone the repo somewhere convenient
cd ~/tools
git clone https://github.com/BranchManager69/codextendo.git
cd codextendo

# run the installer
./install.sh

# then reload your shell
source ~/.bashrc   # or open a new terminal
```

The installer copies `codextendo.sh` (and the Python summariser) to `~/.codextendo/` and appends a sourcing snippet to `~/.bashrc`, `~/.zshrc`, and `~/.bash_aliases` if needed.

## Usage
```bash
# Full flow: search and immediately resume the picked session
codextendo "optionally set enable transcript logs"

# Generate a structured summary (JSON + Markdown) without resuming
codextendo --summary "optionally set enable transcript logs"

# Resume + summarize in one shot
codextendo --summarize "optionally set enable transcript logs"

# Refresh summaries for every session (use --force to rebuild everything)
codextendo refresh

# Other helpers
codexsearch "optionally set enable transcript logs"
codexlabel 1 "Transcript Logging"
codexlabel --clear 1
codexopen 1 --dest ~/codex-replay
codex-last
codex-list 5
```

### Resume catch-up prompts
Every resume sends a first-turn message so the assistant can catch you up:

```
Hey, we got disconnected and my memory is foggy. Please give me a quick, easy-to-digest catch-up:
- Past: what we just did or decided.
- Present: where things stand right now and any blockers.
- Future: what we planned or should tackle next.
```

The helper automatically appends the search query, the last transcript snippet, and (if present) the session label. Customise or disable it any time:

- Override once: `codextendo --resume-prompt "quick recap please" <query>`
- Skip once: `codextendo --no-resume-prompt <query>`
- Change the default globally: `export CODEXTENDO_RESUME_PROMPT="..."`
- Disable globally: `export CODEXTENDO_RESUME_PROMPT_DISABLED=1`

### Summaries, history, and storage
- JSON and Markdown summaries live in `~/.codextendo/summaries/<session-id>.{json,md}`.
- Each summarise run also appends `~/.codextendo/summaries/<session-id>.history.md` with a timestamped Past/Present/Future log.
- `codextendo refresh` writes an index file (`index.json`) alongside the summaries so the dashboard and CLI can load metadata quickly.

### Configuration
Use CLI flags or environment variables to tune behaviour:

- `CODEXTENDO_SUMMARY_MODEL` – default model for summaries (defaults to `gpt-5`).
- `CODEXTENDO_SUMMARY_TOKEN_LIMIT` – max tokens passed to the summariser (default `200000`).
- `CODEX_LABEL_FILE` – alternate path for stored labels (defaults to `~/.codex/search_labels.json`).
- `CODEXTENDO_RESUME_PROMPT` / `CODEXTENDO_RESUME_PROMPT_DISABLED` – tweak or turn off the automatic recap.
- `--summary-dir` / `--sessions-dir` flags (on the Python helper) let you redirect where summaries and source transcripts live.

Labels are stored in `~/.codex/search_labels.json`. They accept any characters; exports sanitise labels for filenames automatically.

### Optional web dashboard
If you prefer a UI, the `~/websites/codextendo-dashboard` Next.js app surfaces the same data. After `npm install` in that directory you can run:

```bash
npm run dev             # local development on http://localhost:3000
npm run build && npm run start
```

The dashboard honours `CODEXTENDO_SUMMARY_DIR` (or defaults to `~/.codextendo/summaries`).

## Uninstall
Remove the sourcing snippet from your shell rc files and delete `~/.codextendo`. For example:
```bash
rm -rf ~/.codextendo
sed -i '/Codextendo helpers/d' ~/.bashrc ~/.bash_aliases ~/.zshrc 2>/dev/null
```

## License
MIT
