# OpenAI Pricing Schema

Codextendo ships a copy of OpenAI's public pricing catalogue in
`resources/pricing/openai_api_model_pricing.json`. The installer copies the same
file to `~/.codextendo/resources/pricing/openai_api_model_pricing.json` so that
local tooling can read it without depending on the repository layout.

The file is a JSON document with these conventions:

- **Top level categories** – sections such as `text`, `audio_tokens`,
  `image_generation`, or `built_in_tools`. Each category contains the pricing
  data relevant to that feature family.
- **`unit`** – human-readable unit describing every numeric value within the
  category (e.g. `usd_per_million_tokens`).
- **`tiers`** – for token-based products, pricing is grouped by service tier
  (`standard`, `flex`, `priority`, `batch`). Every tier enumerates the available
  models and their costs.
- **Model entries** – summarised as:
  ```json
  {
    "cached_input": 0.075,
    "input": 0.15,
    "output": 0.6
  }
  ```
  - `input`: price per million prompt tokens
  - `output`: price per million completion tokens
  - `cached_input`: discounted price for prompt tokens served from OpenAI's
    prompt cache (may be `null` if the model does not advertise a cache rate)
- **Non-token entries** – some sections use alternative structures, e.g.
  transcription prices (`usd_per_minute`) or tool call charges (`usd_per_1k_calls`).
  Refer to the surrounding `unit` value when interpreting them.
- **`notes`** – optional array of strings with clarifications lifted from OpenAI's
  pricing page. Keep these intact so downstream tooling can display or log the
  original context.

When consuming the pricing data:

1. Select the relevant category and tier. For Codextendo summaries, use
   `text.tiers.standard` by default.
2. Read `input`, `output`, and (optionally) `cached_input` to compute
   per-million token costs.
3. Apply your token counts and divide by one million to calculate dollar costs.

Updates to the file should preserve this shape so downstream components can
continue to rely on the schema.
