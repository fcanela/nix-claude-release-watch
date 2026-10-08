"""Tests on real data only: the lock fixtures, versions.json and the committed changelog.

The agent is replaced by a function that picks real changelog lines, so no test
calls claude. Each test works in a throwaway copy of this repository.

    python3 -m unittest discover tests
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import rwlib

LOCKS = sorted((REPO / "fixtures" / "locks").glob("*.json"))
MAPPING = json.loads((REPO / "versions.json").read_text())


def lock_side(path):
    rev = json.loads(path.read_text())["nixpkgs-unstable"]["locked"]["rev"]
    return {"version": MAPPING[rev]["version"], "from": f"nixpkgs:{rev}"}


PAIRS = [(lock_side(a), lock_side(b)) for a, b in zip(LOCKS, LOCKS[1:])]


def shown_entries(prompt):
    """[(version, entry)] of the changelog the job put in the prompt."""
    block = prompt.split("<changelog>\n", 1)[1].split("</changelog>", 1)[0]
    return [(v, e) for v, entries in rwlib.changelog_sections(block) for e in entries]


def hook_agent(prompt):
    """Stands in for claude: every shown entry that mentions hooks, copied exactly."""
    return {"findings": [
        {"version": v, "quote": e, "watch": ["hooks"], "why": "Mentions hooks."}
        for v, e in shown_entries(prompt) if "hook" in e.lower()
    ]}


class Case(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="release-watch-test-"))
        self.addCleanup(shutil.rmtree, self.root)
        for name in ["scripts", "changelog", "fixtures", "watchlist.txt", "versions.json", "prompt.md"]:
            src = REPO / name
            (shutil.copytree if src.is_dir() else shutil.copy)(src, self.root / name)
        (self.root / "inputs").mkdir()
        (self.root / "reports").mkdir()
        self.git("init", "-q", "-b", "master")
        for key, value in [("user.name", "test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false")]:
            self.git("config", key, value)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixtures")
        self.notes = []

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True).stdout

    def notify(self, title, body):
        self.notes.append(body)

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def new_input(self, before, after):
        data, digest = rwlib.make_input(self.root, before, after)
        return rwlib.write_input(self.root, data, digest), digest

    def run_job(self, pair, agent=hook_agent):
        path, digest = self.new_input(*pair)
        return rwlib.run_input(self.root, path, agent, self.notify), digest

    def verify(self):
        p = subprocess.run([sys.executable, str(self.root / "scripts" / "verify")], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def rewrite_report(self, digest, change):
        path = self.root / "reports" / f"{digest}.md"
        path.write_text(change(path.read_text()))
        self.git("commit", "-q", "-am", "tamper")


class JobOnFixtures(Case):
    def test_four_real_updates(self):
        self.assertEqual([(b["version"], a["version"]) for b, a in PAIRS],
                         [("2.1.119", "2.1.201"), ("2.1.201", "2.1.224"), ("2.1.224", "2.1.238"), ("2.1.238", "2.1.272")])
        for pair in PAIRS:
            status, digest = self.run_job(pair)
            self.assertEqual(status, "written")
            report = (self.root / "reports" / f"{digest}.md").read_text()
            count = int(re.search(r"^Relevant changes: (\d+)$", report, re.M).group(1))
            self.assertGreater(count, 0)
            self.assertEqual(report.count("\n> "), count)
            self.assertIn(f"{count} relevant change", self.notes[-1])
        self.assertEqual(len(self.notes), 4)
        self.assertEqual(self.git("status", "--porcelain"), "")
        code, out = self.verify()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("OK  "), 4, out)

    def test_second_run_changes_nothing(self):
        status, digest = self.run_job(PAIRS[3])
        report = self.root / "reports" / f"{digest}.md"
        head, text = self.head(), report.read_bytes()

        def no_agent(prompt):
            self.fail("the agent was called for an input that already has a report")

        status, again = self.run_job(PAIRS[3], no_agent)
        self.assertEqual((status, again), ("exists", digest))
        self.assertEqual((self.head(), report.read_bytes(), len(self.notes)), (head, text, 1))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_reworded_quote_writes_nothing(self):
        def rewording_agent(prompt):
            answer = hook_agent(prompt)
            answer["findings"][0]["quote"] = answer["findings"][0]["quote"][:-1]
            return answer

        self.assert_run_fails(PAIRS[3], rewording_agent, "not an entry")

    def test_quote_from_outside_the_range_writes_nothing(self):
        before = PAIRS[3][0]["version"]
        old = dict(rwlib.changelog_sections((self.root / rwlib.CHANGELOG).read_text()))[before][0]

        def out_of_range_agent(prompt):
            return {"findings": [{"version": before, "quote": old, "watch": ["hooks"], "why": "Old."}]}

        self.assert_run_fails(PAIRS[3], out_of_range_agent, "not an entry")

    def test_unusable_answer_writes_nothing(self):
        self.assert_run_fails(PAIRS[3], lambda prompt: {"result": "sorry"}, "no 'findings' list")

    def assert_run_fails(self, pair, agent, message):
        head = self.head()
        with self.assertRaisesRegex(rwlib.JobError, message):
            self.run_job(pair, agent)
        self.assertEqual(self.head(), head)
        self.assertEqual(list((self.root / "reports").iterdir()), [])
        self.assertEqual(self.notes, [])

    def test_same_version_needs_no_agent(self):
        side = PAIRS[3][1]
        status, digest = self.run_job((side, side), lambda prompt: self.fail("agent called"))
        self.assertEqual(status, "written")
        self.assertIn("nothing to update", (self.root / "reports" / f"{digest}.md").read_text())
        self.assertIn("0 relevant changes", self.notes[0])
        self.assertEqual(self.verify()[0], 0)


class VerifyCatches(Case):
    def setUp(self):
        super().setUp()
        _, self.digest = self.run_job(PAIRS[3])
        self.assertEqual(self.verify()[0], 0)

    def assert_invalid(self, word, reason):
        code, out = self.verify()
        self.assertEqual(code, 1, out)
        self.assertIn(word, out)
        self.assertIn(reason, out)

    def test_missing_report(self):
        self.git("rm", "-q", f"reports/{self.digest}.md")
        self.git("commit", "-q", "-m", "drop")
        self.assert_invalid("MISSING", self.digest[:12])

    def test_input_without_report_after_a_failed_run(self):
        path, _ = self.new_input(*PAIRS[2])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "input only")
        self.assert_invalid("MISSING", "2.1.224 -> 2.1.238")

    def test_changed_quote(self):
        self.rewrite_report(self.digest, lambda t: re.sub(r"^> (.)", lambda m: "> " + m.group(1).swapcase(), t, count=1, flags=re.M))
        self.assert_invalid("INVALID", "not an exact changelog line")

    def test_real_line_from_outside_the_range(self):
        before = PAIRS[3][0]["version"]
        old = dict(rwlib.changelog_sections((self.root / rwlib.CHANGELOG).read_text()))[before][0]
        extra = f"## {before}\n\n> {old}\n\nTouches: hooks\n\nWhy: Old.\n"
        self.rewrite_report(self.digest, lambda t: re.sub(
            r"^Relevant changes: (\d+)$", lambda m: f"Relevant changes: {int(m.group(1)) + 1}", t, count=1, flags=re.M) + extra)
        self.assert_invalid("INVALID", "outside the range")

    def test_real_line_under_the_wrong_version(self):
        self.rewrite_report(self.digest, lambda t: re.sub(r"^## .*$", "## 2.1.239", t, count=1, flags=re.M))
        self.assert_invalid("INVALID", "not an exact changelog line")

    def test_wrong_count(self):
        self.rewrite_report(self.digest, lambda t: re.sub(r"^Relevant changes: \d+$", "Relevant changes: 0", t, count=1, flags=re.M))
        self.assert_invalid("INVALID", "relevant changes but quotes")

    def test_report_without_input(self):
        (self.root / "reports" / ("0" * 64 + ".md")).write_text("# stray\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "stray")
        self.assert_invalid("INVALID", "no input has this hash")

    def test_uncommitted_report(self):
        path = self.root / "reports" / f"{self.digest}.md"
        path.write_text(path.read_text() + "edited\n")
        self.assert_invalid("UNCOMMITTED", f"reports/{self.digest}.md")

    def test_changelog_edited_after_the_report(self):
        path = self.root / rwlib.CHANGELOG
        path.write_text(path.read_text().replace("## 2.1.272\n\n- ", "## 2.1.272\n\n- x", 1))
        self.git("commit", "-q", "-am", "edit changelog")
        self.assert_invalid("INVALID", "is not the one this input was made with")


class Weekly(Case):
    """The network is replaced by a function that serves the committed real data."""

    installed_version = "2.1.238"
    head_rev = "b1b875982b17dabde9b4a37f3e229e74913e6db3"  # has 2.1.272

    def setUp(self):
        super().setUp()
        self.changelog = (REPO / rwlib.CHANGELOG).read_text()
        self.offline = False

    def fetch(self, url):
        if self.offline:
            raise OSError("network is down")
        if url == rwlib.NIXPKGS_HEAD_URL:
            return json.dumps({"sha": self.head_rev}).encode()
        if url == MAPPING[self.head_rev]["source"]:
            return json.dumps({"version": MAPPING[self.head_rev]["version"]}).encode()
        if url == rwlib.CHANGELOG_HEAD_URL:
            return json.dumps([{"sha": "0" * 40}]).encode()
        if url == rwlib.CHANGELOG_RAW_URL.format(commit="0" * 40):
            return self.changelog.encode()
        raise OSError(f"404: {url}")

    def weekly(self, agent=hook_agent):
        return rwlib.weekly(self.root, self.fetch, lambda: self.installed_version, agent, self.notify)

    def test_first_week_writes_then_same_input_writes_nothing(self):
        # week 1: the changelog as it was when 2.1.272 was its newest entry
        full = self.changelog
        self.changelog = "# Changelog\n\n" + full[full.index("## 2.1.272\n"):]
        self.assertEqual(self.weekly(), "written")
        self.assertEqual(len(self.notes), 1)
        self.assertEqual(self.verify()[0], 0, self.verify()[1])
        doc = json.loads(next((self.root / "inputs").glob("*.json")).read_text())
        self.assertEqual((doc["before"], doc["after"]), (
            {"version": "2.1.238", "from": "installed"}, {"version": "2.1.272", "from": "nixpkgs-unstable"}))

        # week 2: upstream added newer entries, the two versions did not move
        self.changelog = full
        head, committed = self.head(), (self.root / rwlib.CHANGELOG).read_text()
        self.assertEqual(self.weekly(lambda prompt: self.fail("agent called")), "unchanged")
        self.assertEqual((self.head(), len(self.notes)), (head, 1))
        self.assertEqual((self.root / rwlib.CHANGELOG).read_text(), committed)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_already_on_the_available_version(self):
        self.installed_version = "2.1.272"
        self.assertEqual(self.weekly(lambda prompt: self.fail("agent called")), "written")
        report = next((self.root / "reports").glob("*.md")).read_text()
        self.assertIn("Installed version 2.1.272 is the available one: nothing to update.", report)
        self.assertIn("0 relevant changes", self.notes[0])
        self.assertEqual(self.verify()[0], 0)
        self.assertEqual(self.weekly(), "unchanged")
        self.assertEqual(len(self.notes), 1)

    def test_older_revision_manifest_name(self):
        self.head_rev = "56c02bc00adcf003215cc4bd996d6efaf4cff188"  # only has manifest.json
        self.installed_version = "2.1.224"
        self.assertEqual(self.weekly(), "written")
        self.assertEqual(self.verify()[0], 0)

    def test_network_failure_writes_nothing(self):
        self.offline = True
        head = self.head()
        with self.assertRaisesRegex(rwlib.JobError, "network step failed"):
            self.weekly()
        self.assertEqual((self.head(), self.notes, self.git("status", "--porcelain")), (head, [], ""))

    def test_failed_agent_is_visible_and_retried(self):
        with self.assertRaises(rwlib.JobError):
            self.weekly(lambda prompt: {"findings": [{"version": "2.1.272", "quote": "made up", "watch": ["hooks"], "why": "x"}]})
        self.assertEqual(self.notes, [])
        code, out = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("MISSING", out)
        self.assertEqual(self.weekly(), "written")
        self.assertEqual(len(self.notes), 1)
        self.assertEqual(self.verify()[0], 0)


if __name__ == "__main__":
    unittest.main()
