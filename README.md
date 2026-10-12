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

## What is here, and where it comes from

- `changelog/CHANGELOG.md` is a copy of `CHANGELOG.md` from `anthropics/claude-code`, refreshed by `scripts/weekly`. `changelog/SOURCE.json` records the source commit. Its licence is that of the source, and I have not checked it. The check needs the copy: it compares every quote with it.
- `fixtures/locks/` holds five past versions of one lock file from my own configuration repository. `watchlist.txt` describes my setup.
- `course/runs/` holds the output of seven runs, saved on 2026-10-08 for my course report. A Claude Code session started runs 1, 2, 3, 5, 6 and 7 at my request. I typed run 4.

## How it was made

Claude Code sessions wrote the contract, the code and the tests after asking me five questions, on 2026-10-08, for a company course on AI-driven development. A second Claude session helped me check that I was applying the course and kept the details for my report.

## Personal data and the history of this repository

This repository is public, so some personal data was redacted before publishing. To do that the history was rewritten once, on 2026-10-12, before the first push.

What was changed, in every commit that contained it:

- the five files of `fixtures/locks/` are named `lock-1` to `lock-5`, and inside them the name of my configuration repository, the path of the file in it, its commits and their dates are replaced by neutral values;
- the folders of my machine named in `CONTRACT.md`, in `systemd/` and in one captured listing are replaced by generic ones;
- the identifiers of this repository's own commits printed in `course/runs/` are the ones the commits have now.

What was not changed: the number of commits and their order, their author, their dates, their messages and every other byte of every file. `scripts/verify` and the tests give the same result as before.

The commits were signed with my SSH key when they were made. The rewritten commits are not signed.
