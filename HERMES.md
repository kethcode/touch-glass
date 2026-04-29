# Hermes Handoff

This repo uses a local wrapper script as the stable interface between Hermes and
Touch Glass:

```bash
/home/lain/touch-glass/scripts/hermes_event_briefing.sh
```

The script:

- loads `.env` from the repo root
- runs `enrichment.events detect`
- prints exactly `[SILENT]` when no event crosses threshold
- otherwise prints `prompts/hermes_commentary.md` followed by the detector output

Relevant `.env` values:

```bash
TOUCH_GLASS_TOPICS_CONFIG=topics.json
TOUCH_GLASS_HERMES_PROMPT=prompts/hermes_commentary.md
TOUCH_GLASS_HERMES_MARK_DELIVERED=true
```

Set `TOUCH_GLASS_HERMES_MARK_DELIVERED=false` while tuning if you want repeated
manual runs to show the same candidate events.

Manual test:

```bash
cd /home/lain/touch-glass
scripts/hermes_event_briefing.sh
```

Expected no-signal output:

```text
[SILENT]
```

Hermes cron shape for later record keeping:

```text
/cron add "every 15m" "Run `/home/lain/touch-glass/scripts/hermes_event_briefing.sh`. If the output is [SILENT], respond exactly [SILENT]. Otherwise follow the instructions in the output and send the briefing to Telegram."
```

Do not add the cron until `topics.json` has been tuned and manual script output
looks useful.
