# Topic Config

`topics/index.json` is the active entry point. It defines global defaults and
imports grouped topic files.

High-priority operational topics live under:

```text
topics/priority/
```

Short-duration one-offs should go under:

```text
topics/watch/
```

Add one-off files beside `topics/watch/index.json`, then import them from
`topics/watch/index.json`. When the watch item is no longer useful, remove the
import line instead of deleting the file immediately.

The detector also still supports the older monolithic format. The root
`topics.json` file can be kept as a local compatibility wrapper:

```json
{
  "imports": ["topics/index.json"]
}
```
