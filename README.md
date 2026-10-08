# release-watch

Which Claude Code changes lie between the version installed here and the one
nixpkgs-unstable has today, and which of them touch my setup. The rules are in
[CONTRACT.md](CONTRACT.md).

| Command | What it does |
|---|---|
| `scripts/weekly` | Builds this week's input from the network and runs the job on it. |
| `scripts/run inputs/<hash>.json` | Runs the job on one existing input. |
| `scripts/fixture-input <lock> <lock>` | Writes the input for a real past update from `fixtures/locks/`. |
| `scripts/verify` | The independent check of the committed reports. Run it yourself. |
| `python3 -m unittest discover tests` | Tests on the four real lock updates. They never call claude. |

- `watchlist.txt` is the list of features that matter; `prompt.md` is what the agent is told.
- `versions.json` maps a nixpkgs revision to its claude-code version.
- `systemd/` holds the weekly timer. It is not installed; the steps are at the top of
  `systemd/release-watch.service`.
