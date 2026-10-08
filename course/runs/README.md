# Outputs of the test runs

Saved on 2026-10-08, for the report of the AI course. Times are UTC. The contract these runs test is `CONTRACT.md`.

| File | What it shows |
| --- | --- |
| `run1-input.txt`, `run1.txt` | First run on a real input from my lock history, 2.1.224 to 2.1.238. One report written and committed (`54ab816`). |
| `run2-same-input.txt` | Second run on the same input. Nothing written: same HEAD and same report hash before and after. |
| `run3-changed-input.txt` | Third run on a changed input, 2.1.238 to 2.1.272. A second report (`cfae5d0`), then `scripts/verify` says OK for both. |
| `run4-check-and-repeat-run-by-hand.txt` | `scripts/verify` and the repeat run typed by me in a terminal. |
| `run5-invalid-and-missing-output.txt` | In a throwaway clone: one quote damaged and committed gives INVALID, a report deleted and committed gives MISSING. Both exit 1. |
| `run6-weekly-for-real.txt` | `scripts/weekly` run once for real: installed 2.1.285, available 2.1.289, one report (`f507d9c`). |

Runs 1, 2, 3, 5 and 6 were started for me by a Claude Code session at my request. Run 4 I typed myself.
