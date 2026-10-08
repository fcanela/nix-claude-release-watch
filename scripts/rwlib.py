"""The job: shared by scripts/run, scripts/weekly and scripts/fixture-input.

scripts/verify deliberately does not import this file.
"""
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = "changelog/CHANGELOG.md"
CHANGELOG_SOURCE = "changelog/SOURCE.json"
WATCHLIST = "watchlist.txt"
VERSIONS = "versions.json"
PROMPT = "prompt.md"

# The home-manager flake follows this branch for its nixpkgs-unstable input.
NIXPKGS_HEAD_URL = "https://api.github.com/repos/NixOS/nixpkgs/commits/nixos-unstable"
NIXPKGS_MANIFEST_URLS = [
    "https://raw.githubusercontent.com/NixOS/nixpkgs/{rev}/pkgs/by-name/cl/claude-code/manifest.zst.json",
    "https://raw.githubusercontent.com/NixOS/nixpkgs/{rev}/pkgs/by-name/cl/claude-code/manifest.json",
]
CHANGELOG_HEAD_URL = "https://api.github.com/repos/anthropics/claude-code/commits?path=CHANGELOG.md&per_page=1"
CHANGELOG_RAW_URL = "https://raw.githubusercontent.com/anthropics/claude-code/{commit}/CHANGELOG.md"

FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "version": {"type": "string"},
                    "quote": {"type": "string"},
                    "watch": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                },
                "required": ["version", "quote", "watch", "why"],
            },
        }
    },
    "required": ["findings"],
}


class JobError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def vkey(version):
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        raise JobError(f"not a version: {version!r}")
    return tuple(int(p) for p in version.split("."))


def changelog_sections(text):
    """[(version, [entry, ...])] in file order; an entry is a "- " line without the "- "."""
    sections = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif line.startswith("- ") and sections:
            sections[-1][1].append(line[2:])
    return sections


def in_range(sections, before, after):
    lo, hi = vkey(before), vkey(after)
    return [(v, entries) for v, entries in sections if lo < vkey(v) <= hi]


def range_text(sections):
    return "".join(f"## {v}\n" + "".join(f"- {e}\n" for e in entries) for v, entries in sections)


def watchlist_ids(text):
    ids = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([a-z0-9-]+): \S", line)
        if not m:
            raise JobError(f"watchlist line is not 'id: description': {line!r}")
        ids.append(m.group(1))
    return ids


def git(root, *args):
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise JobError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


@contextmanager
def job_lock(root):
    with open(Path(root) / ".git" / "release-watch.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JobError("another run is in progress")
        yield


# --- input -----------------------------------------------------------------

def make_input(root, before, after, changelog_text=None):
    """(bytes, hash) of the input for before -> after; before/after are {"version", "from"}."""
    root = Path(root)
    if changelog_text is None:
        changelog_text = (root / CHANGELOG).read_text()
    sections = in_range(changelog_sections(changelog_text), before["version"], after["version"])
    doc = {
        "schema": 1,
        "before": before,
        "after": after,
        "changelog_range_sha256": sha256(range_text(sections).encode()),
        "watchlist_sha256": sha256((root / WATCHLIST).read_bytes()),
    }
    data = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
    return data, sha256(data)


def write_input(root, data, digest):
    path = Path(root) / "inputs" / f"{digest}.json"
    if not path.exists():
        path.write_bytes(data)
    return path


# --- run -------------------------------------------------------------------

def run_input(root, input_path, agent, notify):
    with job_lock(root):
        return _run_input(Path(root), Path(input_path), agent, notify)


def _run_input(root, input_path, agent, notify):
    """Write and commit reports/<hash>.md for one input. Returns "written" or "exists"."""
    data = input_path.read_bytes()
    digest = sha256(data)
    name = f"input {digest[:12]}"
    if input_path.resolve() != (root / "inputs" / f"{digest}.json").resolve():
        raise JobError(f"{input_path}: an input must be inputs/<sha256 of its bytes>.json")
    report_rel = f"reports/{digest}.md"
    report = root / report_rel
    if report.exists():
        return "exists"

    doc = json.loads(data)
    before, after = doc["before"]["version"], doc["after"]["version"]
    watch_text = (root / WATCHLIST).read_text()
    sections = in_range(changelog_sections((root / CHANGELOG).read_text()), before, after)
    if sha256(watch_text.encode()) != doc["watchlist_sha256"]:
        raise JobError(f"{name}: the watchlist changed since this input was written")
    if sha256(range_text(sections).encode()) != doc["changelog_range_sha256"]:
        raise JobError(f"{name}: the changelog for {before}..{after} changed since this input was written")

    if vkey(before) == vkey(after):
        findings, note = [], f"Installed version {before} is the available one: nothing to update."
    elif vkey(before) > vkey(after):
        findings, note = [], f"Installed version {before} is newer than the available {after}: nothing to update."
    elif not sections:
        findings, note = [], f"The changelog has no entries between {before} and {after}."
    else:
        prompt = (
            (root / PROMPT).read_text()
            + f"\n<watchlist>\n{watch_text}</watchlist>\n\n<changelog>\n{range_text(sections)}</changelog>\n"
        )
        try:
            findings = check_findings(agent(prompt), sections, watchlist_ids(watch_text))
        except JobError as e:
            raise JobError(f"{name} ({before} -> {after}): {e}; no report written")
        note = "No change between these versions touches the watchlist."

    text = render_report(digest, doc, findings, note)
    fd, tmp = tempfile.mkstemp(dir=report.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.link(tmp, report)  # fails rather than overwrite
    finally:
        os.unlink(tmp)

    input_rel = f"inputs/{digest}.json"
    n = len(findings)
    try:
        git(root, "add", "--", input_rel, report_rel)
        git(root, "commit", "-q", "-m", f"report: {before} -> {after}, {n} relevant", "--", input_rel, report_rel)
    except JobError:
        git(root, "reset", "-q", "--", report_rel)
        report.unlink()
        raise
    changes = "1 relevant change" if n == 1 else f"{n} relevant changes"
    notify("Claude Code release watch", f"{before} → {after}: {changes}\n{report_rel}")
    return "written"


def check_findings(raw, sections, watch_ids):
    """Shape check of the agent's answer; every quote must be an entry it was shown."""
    if not isinstance(raw, dict) or not isinstance(raw.get("findings"), list):
        raise JobError("agent answer has no 'findings' list")
    by_version = dict(sections)
    merged = {}
    for f in raw["findings"]:
        if not isinstance(f, dict):
            raise JobError("agent finding is not an object")
        version, quote, watch, why = f.get("version"), f.get("quote"), f.get("watch"), f.get("why")
        if not (isinstance(version, str) and isinstance(quote, str) and isinstance(why, str)
                and isinstance(watch, list) and watch and all(isinstance(w, str) for w in watch)):
            raise JobError(f"agent finding has the wrong shape: {json.dumps(f)[:200]}")
        if quote.startswith("- "):
            quote = quote[2:]
        if quote not in by_version.get(version, ()):
            raise JobError(f"agent quote is not an entry of {version} in range: {quote[:120]!r}")
        unknown = [w for w in watch if w not in watch_ids]
        if unknown:
            raise JobError(f"agent used unknown watchlist ids: {', '.join(unknown)}")
        why = " ".join(why.split())
        if not why:
            raise JobError(f"agent gave no reason for: {quote[:120]!r}")
        entry = merged.setdefault((version, quote), {"watch": [], "why": why})
        entry["watch"] += [w for w in watch if w not in entry["watch"]]
    # newest version first, changelog order inside a version
    return [
        {"version": v, "quote": e, **merged[(v, e)]}
        for v, entries in sorted(sections, key=lambda s: vkey(s[0]), reverse=True)
        for e in entries if (v, e) in merged
    ]


def render_report(digest, doc, findings, note):
    b, a = doc["before"], doc["after"]
    out = [
        f"# Claude Code {b['version']} → {a['version']}",
        "",
        f"Input: {digest}",
        f"Before: {b['version']} ({b['from']})",
        f"After: {a['version']} ({a['from']})",
        f"Relevant changes: {len(findings)}",
        "",
    ]
    if not findings:
        out += [note, ""]
    version = None
    for f in findings:
        if f["version"] != version:
            version = f["version"]
            out += [f"## {version}", ""]
        out += [f"> {f['quote']}", "", f"Touches: {', '.join(f['watch'])}", "", f"Why: {f['why']}", ""]
    return "\n".join(out)


# --- the real agent and notifier ---------------------------------------------

def claude_agent(prompt):
    """claude -p with no tools and no user customisations; the prompt goes in on stdin."""
    cmd = [
        os.environ.get("RELEASE_WATCH_CLAUDE", "claude"), "-p",
        "--tools", "",
        "--safe-mode", "--strict-mcp-config", "--no-session-persistence",
        "--output-format", "json",
        "--json-schema", json.dumps(FINDINGS_SCHEMA),
    ]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise JobError(f"agent did not run: {e}")
    if p.returncode != 0:
        raise JobError(f"agent exited {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
    try:
        out = json.loads(p.stdout)
    except ValueError:
        raise JobError(f"agent output is not JSON: {p.stdout[:200]!r}")
    if not isinstance(out, dict) or out.get("is_error"):
        raise JobError(f"agent reported an error: {str(out)[:300]}")
    if isinstance(out.get("structured_output"), dict):
        return out["structured_output"]
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(out.get("result", "")).strip())
    try:
        return json.loads(text)
    except ValueError:
        raise JobError(f"agent answer is not JSON: {text[:200]!r}")


def desktop_notify(title, body):
    try:
        subprocess.run(["notify-send", "--app-name=release-watch", title, body], check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"release-watch: report written, but notify-send failed: {e}", flush=True)


# --- weekly ------------------------------------------------------------------

def http_fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "release-watch"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def installed_claude_version():
    cmd = os.environ.get("RELEASE_WATCH_CLAUDE", "claude")
    try:
        out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise JobError(f"cannot read the installed version: {e}")
    m = re.match(r"(\d+(?:\.\d+)+)\b", out.strip())
    if not m:
        raise JobError(f"cannot read the installed version from {out!r}")
    return m.group(1)


def available_version(fetch):
    """(nixpkgs revision at the head of nixos-unstable, its claude-code version, source url)."""
    rev = json.loads(fetch(NIXPKGS_HEAD_URL))["sha"]
    for template in NIXPKGS_MANIFEST_URLS:
        url = template.format(rev=rev)
        try:
            version = json.loads(fetch(url))["version"]
        except (OSError, ValueError, KeyError):
            continue
        vkey(version)
        return rev, version, url
    raise JobError(f"no claude-code manifest found in nixpkgs at {rev}")


def weekly(root, fetch, installed, agent, notify):
    """Build this week's input from the network, then run the job on it.

    Returns "unchanged" (nothing written), "written" or "exists".
    """
    root = Path(root)
    with job_lock(root):
        try:
            before = installed()
            rev, after, manifest_url = available_version(fetch)
            commit = json.loads(fetch(CHANGELOG_HEAD_URL))[0]["sha"]
            changelog_url = CHANGELOG_RAW_URL.format(commit=commit)
            changelog = fetch(changelog_url).decode()
        except (OSError, ValueError, KeyError, IndexError) as e:
            raise JobError(f"network step failed: {e}")
        versions = [vkey(v) for v, _ in changelog_sections(changelog)]
        if not versions or max(versions) < vkey(after):
            raise JobError(f"the fetched changelog does not reach {after}")

        data, digest = make_input(
            root,
            {"version": before, "from": "installed"},
            {"version": after, "from": "nixpkgs-unstable"},
            changelog,
        )
        input_path = root / "inputs" / f"{digest}.json"
        if input_path.exists():
            if (root / "reports" / f"{digest}.md").exists():
                return "unchanged"
            # same input as a run that failed before its report: try again
            return _run_input(root, input_path, agent, notify)

        mapping = json.loads((root / VERSIONS).read_text())
        mapping[rev] = {"version": after, "source": manifest_url}
        source = {"repo": "anthropics/claude-code", "path": "CHANGELOG.md", "commit": commit, "url": changelog_url}
        (root / CHANGELOG).write_text(changelog)
        (root / CHANGELOG_SOURCE).write_text(json.dumps(source, indent=2) + "\n")
        (root / VERSIONS).write_text(json.dumps(mapping, indent=2) + "\n")
        write_input(root, data, digest)
        paths = [CHANGELOG, CHANGELOG_SOURCE, VERSIONS, f"inputs/{digest}.json"]
        git(root, "add", "--", *paths)
        git(root, "commit", "-q", "-m", f"input: {before} -> {after}", "--", *paths)
        return _run_input(root, input_path, agent, notify)
