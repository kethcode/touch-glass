# Touch Glass Consolidation Prompt

This is the mechanical first pass for a Touch Glass briefing. Candidate events
from the detector are noisy and intentionally overlapping. Treat them as
evidence, not as final briefing sections.

If this prompt and the commentary prompt are executed in one model call, perform
this consolidation internally first, then follow the commentary prompt and
output only the final briefing.

## Task

Build a private set of canonical stories from the candidate events that follow.
Do not preserve detector topic boundaries when they duplicate the same story.

For each canonical story, determine:

- short neutral title
- priority: `critical`, `high`, `medium`, or `low`
- confidence: `high`, `medium`, or `low`
- matched detector topics
- primary sources or URLs
- representative evidence posts
- why it matters
- what to watch next, if anything

Use that private story set as the input to the commentary pass.

## Deduplication Rules

- Merge events that refer to the same incident, release, actor, protocol,
  company, exploit, legal development, product change, post, URL, or primary
  source.
- Treat exact duplicate URLs, repeated tweet URLs, same author plus near-identical
  text, and multiple posts quoting the same primary link as one story.
- If a story matches several topics, keep the strongest framing and retain the
  other topic IDs only as context.
- Do not repeat one tweet, link, or source in multiple final stories.
- Prefer dropping a marginal duplicate over padding the briefing.

## Ranking Rules

Rank source diversity and operational relevance above raw post count.

Do not let a hard cap hide critical operational stories. If there are more
canonical stories than the commentary prompt should report, keep all critical
stories in the final candidate set and let lower-priority stories fall below
the visible line.

Boost stories with:

- direct Wildcat, Indexed Finance, Andean Medjedovic, DPRK, exploit, sanctions,
  legal, or security-advisory relevance
- official announcements, advisories, releases, repos, filings, or credible
  primary-source links
- multiple independent authors or domains converging on the same fact
- clear implications for crypto security, smart-contract work, AI tooling,
  local inference, hardware planning, or current project operations

Downrank or omit:

- single-author pile-ons with no primary source
- meme-only or vibes-only clusters
- engagement bait without new information
- generic discourse that only matches broad keywords
- duplicates that add no new source, fact, or interpretation

## Overflow Rules

- The detector handoff can include more candidate events than the final briefing
  should print. This is intentional.
- If more than 5 canonical stories remain after consolidation, the final
  briefing should report the best 5 and include an overflow note.
- The overflow note should list the number of omitted canonical stories and a
  compact label or topic ID for the most important omitted categories.
- Never omit `critical` stories from the final briefing. Replace a lower-priority
  story instead.

## Confidence Rules

- Mark a story `high` confidence when the core fact is from an official source,
  public filing, advisory, release note, repository, or multiple independent
  sources.
- Mark a story `medium` confidence when the fact is plausible but mostly
  surfaced by informed commentary or one strong secondary source.
- Mark a story `low` confidence when it is rumor, attribution, speculation, or
  weak early signal. Low confidence can still be high priority if operationally
  sensitive.
- Clearly separate confirmed facts from inference. Do not upgrade speculation
  into fact.

## Output Contract

The final user-visible answer should come from the commentary prompt, not this
consolidation prompt. Unless explicitly asked for the intermediate story set,
do not print the canonical-story schema. Output only the final briefing.

Candidate events follow after both prompt sections.
