# Hermes Handoff

This repo uses a local wrapper script as the stable interface between Hermes and
Touch Glass:

```bash
/home/lain/touch-glass/scripts/hermes_event_briefing.sh
```

The script:

- loads `.env` from the repo root
- treats explicit environment variables on the command as overrides for `.env`
- runs `enrichment.events detect`
- prints exactly `[SILENT]` when no event crosses threshold
- otherwise prints the consolidation prompt, commentary prompt, and detector output

Relevant `.env` values:

```bash
TOUCH_GLASS_TOPICS_CONFIG=topics/index.json
TOUCH_GLASS_HERMES_CONSOLIDATION_PROMPT=prompts/hermes_consolidation.md
TOUCH_GLASS_HERMES_COMMENTARY_PROMPT=prompts/hermes_commentary.md
TOUCH_GLASS_HERMES_MARK_DELIVERED=true
TOUCH_GLASS_HERMES_LIMIT=25
TOUCH_GLASS_HERMES_WINDOW_MINUTES=120
# TOUCH_GLASS_HERMES_MIN_PRIORITY=7
# TOUCH_GLASS_HERMES_MIN_SCORE=20
```

Set `TOUCH_GLASS_HERMES_MARK_DELIVERED=false` while tuning if you want repeated
manual runs to show the same candidate events.

`TOUCH_GLASS_HERMES_PROMPT` is still accepted as a compatibility alias for the
commentary prompt, but new installs should use `TOUCH_GLASS_HERMES_COMMENTARY_PROMPT`.

If the deployment is managed through `uv` instead of a `.venv`, set:

```bash
TOUCH_GLASS_PYTHON_CMD="uv run python"
```

The wrapper defaults to a 120-minute detector window so routine runs do not
inherit long per-topic windows meant for low-cadence watch topics. For one-off
backfills while tuning, set `TOUCH_GLASS_HERMES_WINDOW_MINUTES` higher for that
run.

Optional handoff gates can suppress lower-signal runs before they reach Hermes.
`TOUCH_GLASS_HERMES_MIN_PRIORITY` is usually the better first lever because broad
topics can score high by volume. `TOUCH_GLASS_HERMES_MIN_SCORE` is useful as a
second pass after topic priorities feel right.

Manual test:

```bash
cd /home/lain/touch-glass
scripts/hermes_event_briefing.sh
```

One-off wider backfill:

```bash
cd /home/lain/touch-glass
TOUCH_GLASS_HERMES_MARK_DELIVERED=false TOUCH_GLASS_HERMES_WINDOW_MINUTES=1440 scripts/hermes_event_briefing.sh
```

Expected no-signal output:

```text
[SILENT]
```

Hermes cron shape for later record keeping:

```text
/cron add "every 15m" "Run `/home/lain/touch-glass/scripts/hermes_event_briefing.sh`. If the output is [SILENT], respond exactly [SILENT]. Otherwise follow the instructions in the output and send the briefing to Telegram."
```

Do not add the cron until `topics/index.json` and imported topic files have been tuned and manual script output
looks useful.
