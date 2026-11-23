"""#119 (issue #6 part 3): the opener puts each new ticket in an open group or a new group at the
end of the chain. Expected values come from the ticket text, never from the code."""

import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import LOOP, LoopCase  # noqa: E402

STAMP = "2026-11-23T00:00:00Z"


def events(obj):
    return [h.get("event") for h in (obj or {}).get("history") or []]


class T119Acceptance(LoopCase):

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)

    # ------------------------------------------------------------ helpers

    def group(self, gid, state, members):
        """One opening-shaped group, written raw."""
        def change(tx):
            tx.put_group({"id": gid, "branch": "int/" + gid, "state": state, "members": list(members),
                          "fix_round": 0, "job": None, "created": STAMP,
                          "history": [{"at": STAMP, "event": "opened by the opener for "
                                       + ", ".join("#" + m for m in members)}]})
        self.pool.update(change, "test: group %s" % gid)

    def open_with(self, action=None):
        if action:
            self.plan(creator={"-": [action]})
        return self.run_py("ticket_creator.py", "--request", "x")

    def out(self, res):
        return res.stdout[-2000:] + res.stderr[-2000:]

    def page_lines(self):
        pages = [p for p in self.snap().inbox.values()
                 if p.get("kind") == "decision" and p.get("ref") == "request"]
        self.assertEqual(len(pages), 1, list(self.snap().inbox.values()))
        return pages[0].get("text", "").split("\n")

    def prompt(self, attempt):
        paths = glob.glob(os.path.join(self.tmp, "wt", "runs", "creator", "*-a%d" % attempt, "prompt.md"))
        self.assertEqual(len(paths), 1, paths)
        with open(paths[0]) as fh:
            return fh.read()

    def refused(self, res, line):
        self.assertEqual(res.returncode, 1, self.out(res))
        lines = self.page_lines()
        self.assertIn("the opener could not produce valid tickets:", lines)
        self.assertIn(line, lines)

    # ------------------------------------------------------------ acceptance 1

    def test_placeholders_make_new_groups_in_first_appearance_order(self):
        self.group("g1", "landed", [])
        res = self.open_with("three_new")
        self.assertEqual(res.returncode, 0, self.out(res))
        snap = self.snap()
        self.assertEqual(set(snap.groups), {"g1", "g2", "g3"},
                         {g: v.get("members") for g, v in snap.groups.items()})
        self.assertEqual(snap.groups["g2"]["members"], ["1", "3"])
        self.assertEqual(events(snap.groups["g2"])[:1], ["opened by the opener for #1, #3"])
        self.assertEqual(snap.groups["g3"]["members"], ["2"])
        self.assertEqual(events(snap.groups["g3"])[:1], ["opened by the opener for #2"])
        for g in ("g2", "g3"):
            self.assertEqual(snap.groups[g]["state"], "open")
        self.assertEqual({i: snap.ticket(i).get("group") for i in ("1", "2", "3")},
                         {"1": "g2", "2": "g3", "3": "g2"})

    # ------------------------------------------------------------ acceptance 2

    def test_a_ticket_joins_an_existing_open_group(self):
        self.add(1, group="g1", state="ready")
        self.group("g1", "open", ["1"])
        res = self.open_with("into_g1")
        self.assertEqual(res.returncode, 0, self.out(res))
        snap = self.snap()
        self.assertEqual(set(snap.groups), {"g1"})
        self.assertEqual(snap.groups["g1"]["members"], ["1", "2"])
        self.assertEqual(events(snap.groups["g1"])[-1], "member #2 added by the opener")
        self.assertEqual(snap.ticket("2").get("group"), "g1")

    # ------------------------------------------------------------ acceptance 3

    def test_a_group_that_is_not_open_is_refused_twice_and_nothing_written(self):
        self.add(1, group="g1", state="merged")
        self.group("g1", "pr", ["1"])
        res = self.open_with("into_g1")
        self.refused(res, "- new-1: group g1 is pr and takes no new tickets")
        snap = self.snap()
        self.assertNotIn("2", snap.tickets)
        self.assertEqual(set(snap.groups), {"g1"})
        self.assertEqual(snap.groups["g1"]["members"], ["1"])
        second = self.prompt(2)
        self.assertIn("## Your previous hand-in was rejected", second)
        self.assertIn("new-1: group g1 is pr and takes no new tickets", second)

    def test_a_group_before_the_group_of_a_dependency_is_refused(self):
        self.add(1, group="g1", state="ready")
        self.add(2, group="g2", state="ready")
        self.group("g1", "open", ["1"])
        self.group("g2", "open", ["2"])
        res = self.open_with("into_g1_after_2")
        self.refused(res, "- new-1: group g1 comes before g2, the group of #2 it depends on")
        snap = self.snap()
        self.assertNotIn("3", snap.tickets)
        self.assertEqual(set(snap.groups), {"g1", "g2"})
        self.assertEqual(snap.groups["g1"]["members"], ["1"])

    def test_a_dag_entry_without_group_is_refused(self):
        res = self.open_with("no_group")
        self.refused(res, "- new-1: \"group\" must be an open group's id or a new group like \"new-g1\"")
        snap = self.snap()
        self.assertEqual(dict(snap.tickets), {})
        self.assertEqual(dict(snap.groups), {})

    def test_a_group_that_does_not_exist_is_refused(self):
        res = self.open_with("into_g1")
        self.refused(res, "- new-1: group g1 does not exist")
        snap = self.snap()
        self.assertEqual(dict(snap.tickets), {})
        self.assertEqual(dict(snap.groups), {})

    # ------------------------------------------------------------ acceptance 5

    def groups_section(self, text):
        """The non-empty lines between '## Groups' and the next '## ' heading; asserts the section
        comes right after '## Current DAG'."""
        lines = text.split("\n")
        self.assertIn("## Groups", lines, "no '## Groups' section in the prompt")
        heads = [ln for ln in lines if ln.startswith("## ")]
        at = heads.index("## Groups")
        self.assertEqual(heads[at - 1], "## Current DAG", heads)
        start = lines.index("## Groups") + 1
        body = []
        for ln in lines[start:]:
            if ln.startswith("## "):
                break
            if ln.strip():
                body.append(ln)
        return body

    def test_prompt_lists_the_groups_that_are_not_landed_or_cancelled(self):
        self.add(1, group="g1", state="ready")
        self.add(2, group="g2", state="merged")
        self.add(3, group="g3", state="landed")
        self.group("g1", "open", ["1"])
        self.group("g2", "pr", ["2"])
        self.group("g3", "landed", ["3"])
        self.open_with()
        self.assertEqual(self.groups_section(self.prompt(1)), [
            "The chain, oldest first; a new group goes at the end.",
            "- g1 (open, takes new tickets): #1 [ready] ticket 1",
            "- g2 (pr, takes no new tickets): #2 [merged] ticket 2"])

    def test_prompt_on_an_empty_pool_says_every_ticket_goes_into_a_new_group(self):
        self.open_with()
        self.assertEqual(self.groups_section(self.prompt(1)), [
            "The chain, oldest first; a new group goes at the end.",
            "- none: every ticket goes into a new group"])

    # ------------------------------------------------------------ acceptance 6

    def test_prompt_chain_asks_for_a_group_and_no_verify(self):
        sys.path.insert(0, LOOP)
        import ticket_creator
        self.assertTrue(hasattr(ticket_creator, "chain"))
        text = ticket_creator.chain()
        self.assertIn('"group"', text)
        self.assertIn("new-g1", text)
        self.assertNotIn('"verify"', text)
        self.assertNotIn("why_alone", text)
        prompts = os.path.join(LOOP, "prompts", "creator")
        with open(os.path.join(prompts, "03-handin.md")) as fh:
            handin = fh.read()
        self.assertRegex(handin, r'\{\s*"depends_on":\s*\[\],\s*"group":\s*"new-g1"\s*\}')
        with open(os.path.join(prompts, "02-rules.md")) as fh:
            rules = fh.read()
        m = re.search(r"^# Rules for the DAG\n(.*?)(?=^# |\Z)", rules, re.S | re.M)
        self.assertTrue(m, "02-rules.md has no 'Rules for the DAG' section")
        section = m.group(1)
        for word in ("`group`", "new-g1", "new-g2", "`notes`"):
            self.assertIn(word, section)
