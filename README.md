<div align="center">

# Codextendo

_Resume Codex CLI conversations in one keystroke, with summaries and history baked in._

[![shell](https://img.shields.io/badge/shell-bash-4EAA25.svg)](#requirements)
[![python](https://img.shields.io/badge/python-3.9%2B-3776AB.svg)](#requirements)
[![license](https://img.shields.io/github/license/BranchManager69/codextendo.svg?color=blue)](./LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/BranchManager69/codextendo/pulls)

</div>

---

## Quick Start
1. **Install:**
   ```bash
   cd ~/tools
   git clone https://github.com/BranchManager69/codextendo.git
   cd codextendo
   ./install.sh
   ```
2. **Reload your shell:** `source ~/.bashrc` (or open a new terminal).
3. **Jump back into any session:**
   ```bash
   codextendo "missing greetings"
   ```
   Pick a result, type the number, and Codextendo will resume the session while asking the assistant for a Past/Present/Future recap.

### Demo (terminal excerpt)
```bash
$ codextendo "branch bot"


1. Sep 27 12:44 AM ET (3m ago)
   ... Added history logging to the CLI summariser so <session>.history.md keeps a past/present/future timeline.

SELECT RESULT # Type the number to open (press Enter to keep results listed without opening): 1
Resuming Codex session 0199855d-e8fa-7902-bce0-c73feb0efddb
```

## Table of Contents
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [Resume catch-up prompts](#resume-catch-up-prompts)
  - [Summaries, history, and storage](#summaries-history-and-storage)
  - [Configuration](#configuration)
- [Optional web dashboard](#optional-web-dashboard)
- [Contributing](#contributing)
- [Uninstall](#uninstall)
- [License](#license)

## Features
- **One-liner resume:** `codextendo <query>` searches Codex transcripts, lets you pick the right conversation, and re-opens it with an automatic Past/Present/Future catch-up prompt.
- **Rich summaries:** `codextendo --summary` (and `--summarize`) writes JSON + Markdown summaries, appends a Markdown history timeline, and stores everything in `~/.codextendo/summaries/`.
- **Batch refresh:** `codextendo refresh` rebuilds summaries for every session while skipping unchanged transcripts via a local hash cache.
- **Supporting helpers:** `codexsearch`, `codexlabel`, `codexopen`, `codex-list`, and `codex-last` cover fuzzy search, labelling, exporting, and quick listing.
- **Optional UI:** the companion Next.js dashboard (`~/websites/codextendo-dashboard`) reads the same summaries for a browser view.

## Requirements
- Bash (or another shell that can source Bash functions)
- Python 3.9+
- [`jq`](https://stedolan.github.io/jq/) for JSON processing
- [`requests`](https://pypi.org/project/requests/) (used by the summariser)
- [`tiktoken`](https://pypi.org/project/tiktoken/) _(optional, recommended for precise token counts)_
- `OPENAI_API_KEY` environment variable (needed for summaries)

## Installation
```bash
cd ~/tools
git clone https://github.com/BranchManager69/codextendo.git
cd codextendo
./install.sh
source ~/.bashrc   # or open a new terminal
```

The installer copies `codextendo.sh` and the Python summariser into `~/.codextendo/` and appends a sourcing snippet to `~/.bashrc`, `~/.zshrc`, and `~/.bash_aliases` if needed.

## Usage
```bash
# Full flow: search and immediately resume the picked session
codextendo "optionally set enable transcript logs"

# Generate a structured summary (JSON + Markdown) without resuming
codextendo --summary "optionally set enable transcript logs"

# Resume + summarise in one shot
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
Each resume sends a first-turn message so the assistant can catch you up:
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
Tune behaviour via CLI flags or environment variables:
- `CODEXTENDO_SUMMARY_MODEL` – default model for summaries (default `gpt-5`).
- `CODEXTENDO_SUMMARY_TOKEN_LIMIT` – max tokens passed to the summariser (default `200000`).
- `CODEX_LABEL_FILE` – alternate path for stored labels (default `~/.codex/search_labels.json`).
- `CODEXTENDO_RESUME_PROMPT` / `CODEXTENDO_RESUME_PROMPT_DISABLED` – customise or turn off the automatic recap.
- `--summary-dir` / `--sessions-dir` flags (Python helper) redirect where summaries and source transcripts live.

Labels are stored in `~/.codex/search_labels.json`. They accept any characters; exports sanitise labels for filenames automatically.

## Optional web dashboard
If you prefer a UI, the `~/websites/codextendo-dashboard` Next.js app surfaces the same data. After `npm install` in that directory run:
```bash
npm run dev             # local development on http://localhost:3000
npm run build && npm run start
```
The dashboard honours `CODEXTENDO_SUMMARY_DIR` (default `~/.codextendo/summaries`).

## Contributing
Contributions are welcome! Feel free to open an issue or submit a pull request if you have ideas for new workflows, bug fixes, or documentation improvements.

## Uninstall
Remove the sourcing snippet from your shell rc files and delete `~/.codextendo`:
```bash
rm -rf ~/.codextendo
sed -i '/Codextendo helpers/d' ~/.bashrc ~/.bash_aliases ~/.zshrc 2>/dev/null
```

## License
MIT
