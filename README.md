# Codextendo

Convenience commands for searching and exporting Codex CLI transcripts from the shell.

## Features
- `codextendo <query>` – run the full flow: search, pick a result, and automatically resume the session in the Codex CLI.
- `codextendo --summary` – generate structured JSON + Markdown summaries (key actions, files, concerns, follow-ups) for any session and append a history entry for future reference. (`--summarize` still works and also resumes the session.)
- `codextendo refresh` – batch refresh summaries for every Codex session (skips unchanged conversations using a local cache).
- `codexsearch <query>` – fuzzy-ish search across `~/.codex/sessions`, shows timestamps, relative ages, labels, and highlighted snippets.
- Interactive picker with colored prompt and `Enter` to skip; remembers last results for quick openings.
- `codexlabel <result #> "Title"` – assign readable names to conversations, auto-disambiguates duplicates, and `--clear` removes a label.
- `codexopen <result #>` – exports the selected session to plain text, automatically appending any label to the filename.
- Shortcuts `codex-last` (export most recent session) and `codex-list` (list recent sessions).

## Requirements
- Bash (or compatible shell sourcing Bash functions)
- Python 3.9+
- `jq`
- `requests` Python package (for summaries)
- `OPENAI_API_KEY` (for summaries)

## Installation
```bash
# clone the repo somewhere convenient
cd ~/tools
git clone <your-fork-url> codextendo
cd codextendo

# run the installer
./install.sh

# then reload your shell
source ~/.bashrc   # or open a new terminal
```

The installer copies `codextendo.sh` to `~/.codextendo/` and appends a sourcing snippet to `~/.bashrc`, `~/.zshrc`, and `~/.bash_aliases` if needed.

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

Resuming via `codextendo` also drops a catch-up message so the assistant reminds you what you were doing. By default it asks for a Past/Present/Future recap and includes the search query plus the last transcript snippet for context. Customise it any time:

- Override for a single run: `codextendo --resume-prompt "short reminder" <query>`
- Skip it once: `codextendo --no-resume-prompt <query>`
- Change the default for all shells: `export CODEXTENDO_RESUME_PROMPT="..."`
- Disable globally: `export CODEXTENDO_RESUME_PROMPT_DISABLED=1`

Labels are stored in `~/.codex/search_labels.json`. They accept any characters; exports sanitize labels for filenames automatically.

Summaries are written to `~/.codextendo/summaries/<session-id>.json` (plus a companion Markdown file).

Each run also appends an entry to `~/.codextendo/summaries/<session-id>.history.md`, capturing the model used, token budget, and the condensed summary timeline.

### Optional web dashboard

If you prefer a UI, the `~/websites/codextendo-dashboard` Next.js app surfaces the same data. After `npm install` in that directory you can run `npm run dev` locally, or `npm run build && npm run start` under PM2.

## Uninstall
Remove the sourcing snippet from your shell rc files and delete `~/.codextendo`. For example:
```bash
rm -rf ~/.codextendo
sed -i '/Codextendo helpers/d' ~/.bashrc ~/.bash_aliases ~/.zshrc 2>/dev/null
```

## License
MIT
