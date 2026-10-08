# Contract: release-watch

The job tells me which Claude Code changes lie between the version I have
installed and the version a home-manager update would give me today, and which
of them touch my setup, so I can decide to update.

## 1. Trigger

Weekly, from a systemd user timer (`systemd/release-watch.timer`, Mondays 09:30,
catching up after a missed week) that starts `scripts/weekly`. The timer lives
in this repository and is installed by hand; `scripts/weekly` can also be
started by hand at any time.

Durable cloud scheduling (a Claude Code routine) is not used, because the job's
real trigger and its input are both local: the "before" version is whatever is
installed on this machine, and the report is a file in this local repository.

## 2. Input version

An input is the file `inputs/<hash>.json`, where `<hash>` is the sha256 of the
file's bytes. It holds:

- `before`: a version and where it came from (`installed`, or `nixpkgs:<rev>`
  for a fixture from the lock history);
- `after`: a version and where it came from (`nixpkgs-unstable`, the head of the
  `nixos-unstable` branch, or `nixpkgs:<rev>`);
- `changelog_range_sha256`: the hash of the changelog entries in range, that is
  each `## <version>` heading with `before < version <= after` followed by its
  `- ` lines, in file order;
- `watchlist_sha256`: the hash of `watchlist.txt`.

The same two versions, the same changelog text between them and the same
watchlist give the same input. A changelog that only grew above `after`, or a
nixpkgs head that moved without a new claude-code, do not change it.

`scripts/weekly` (a script, not the agent) is the only part that uses the
network. It reads the installed version from `claude --version`, the available
version from nixpkgs at the head of `nixos-unstable`, and the changelog from
`anthropics/claude-code`. For a new input it commits the refreshed
`changelog/CHANGELOG.md`, its upstream commit in `changelog/SOURCE.json`, the
revision in `versions.json` and the input file.

## 3. Permissions

- The agent is `claude -p` with no tools, no MCP servers and user customisations
  off. It receives the prompt, the watchlist and the changelog entries in range
  as text and returns text.
- The job reads only files in this repository. It writes `reports/<hash>.md`
  and, for a new input, the four files named in item 2. It commits only those
  paths, on `master`. It never pushes.
- Nothing reads or writes the configuration repository.

## 4. Output path

`reports/<hash>.md`, one per input, written to a temporary file and linked into
place, then committed together with its input. It lists each relevant change
under its version, quoted word for word, with the watchlist entries it touches.
When the installed version is already the available one, or nothing in range is
relevant, the report says so.

When a run writes a new report it sends one desktop notification with
`notify-send` giving the number of relevant changes. No report, no notification.

## 5. Validation

`scripts/verify`, run by me and never by the job or the agent. It reads only
what is committed and shares no code with the job. For every committed input it
checks that:

- the file name is the hash of its bytes, and a report with that hash exists;
- the committed watchlist and the committed changelog entries in range still
  hash to what the input recorded;
- versions that came from nixpkgs are in `versions.json`;
- every quote is an exact line of the committed changelog under the version it
  is filed under, and that version is after `before` and up to and including
  `after`, compared number by number (2.1.9 < 2.1.10);
- no quote appears twice, each names known watchlist entries, and the stated
  count equals the number of quotes.

It also fails on a report with no input and on uncommitted changes to any file
it checks.

## 6. Owner

Francisco Canela. He installs the timer, edits the watchlist, runs
`scripts/verify` and deletes a report that must be redone.

## 7. Failure behaviour

- If the network step fails, nothing is written.
- If the agent fails, or answers with anything that is not a list of exact
  in-range changelog lines with known watchlist entries, the run exits non-zero,
  names the input and writes no report. The input stays committed without a
  report, which `scripts/verify` shows as `MISSING`. The systemd unit is left in
  the failed state.
- An existing report is never overwritten. A second run on the same input
  writes nothing, commits nothing and sends no notification. A week with the
  same input as the last one does not even touch the changelog.
- A run on an input whose report is missing (after a failure) tries again.
- `scripts/verify` prints one line per input, `OK`, `MISSING` or `INVALID` with
  the reasons, and exits non-zero unless all are `OK`. An invalid report is
  redone by deleting it, committing, and running again.
