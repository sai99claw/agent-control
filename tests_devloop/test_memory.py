"""memory: folders, lessons, git writer, keeper, consolidation, memory docs"""

import calendar
import glob
import html
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import AGENT, HERE, HOME, LOOP, ROLES, TESTS, GitIsolatedCase, LoopCase, git, own_origin  # noqa: E402,F401
from core import dag as dagmod  # noqa: E402
from core import osx, pages  # noqa: E402
from core import pool as pool_mod  # noqa: E402
from core.pool import Pool, shown_state, TICKET_STATES  # noqa: E402


class UnlimitedTokens(LoopCase):
    def test_memory_is_consolidated_by_two_models_with_the_discussion_kept(self):
        self.config(unlimited_tokens=True)
        self.run_py("tickets.py", "init")
        self.add(1)
        self.plan(worker={"1": ["lessons"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        snap = self.snap()
        mem = self.pool_memory()
        talks = [k for k in mem if k.startswith("memory/discussions/")]
        self.assertEqual(len(talks), 1)
        self.assertIn("merge lines 2 and 3", mem[talks[0]])
        state = snap.memory_state["runs"][-1]
        self.assertEqual([c["role"] for c in state["cost"]], ["consolidator", "consolidator_peer", "consolidator"])


class LessonsHelpers(unittest.TestCase):
    def test_bullets_parse_and_limits(self):
        from core import memory
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "n.md")
            with open(f, "w") as fh:
                fh.write("# h\n- a\n  * b\nplain\n- \n-" + "x" * 1300 + "\n")
            got = memory.bullets(f)
            self.assertEqual(got[:2], ["a", "b"])
            self.assertEqual(len(got), 3)
            self.assertEqual(len(got[2]), memory.MAX_TEXT)
            with self.assertRaises(OSError):
                memory.bullets(os.path.join(d, "missing"))

    def test_import_keys_are_unique_per_import(self):
        from core import memory

        class Tx:
            def __init__(self): self.rows = {}
            def write(self, k, v): self.rows[k] = v
        tx = Tx()
        memory.import_lessons(tx, ["a"], "role", "worker", "/x/f.md")
        memory.import_lessons(tx, ["b"], "role", "worker", "/x/f.md")
        self.assertEqual(len(tx.rows), 2)


class ReadersHelpers(unittest.TestCase):
    class Cfg:
        chains = {"creator": ["c"], "worker": ["a", "b"], "worker_simple": ["s", "a"],
                  "verifier": ["v"], "reviewer": ["r"], "fixer": ["f"]}

        def agent_chain(self, role):
            return [["x", "--model", m] for m in self.chains[role]]

    def test_readers_by_layer(self):
        from core import memory
        cfg = self.Cfg()
        self.assertEqual(memory.readers(cfg, "role", "worker"), ["a", "b", "s"])
        self.assertEqual(memory.readers(cfg, "role", "consolidator"), [])
        self.assertEqual(memory.readers(cfg, "model", "m"), ["m"])
        self.assertEqual(memory.readers(cfg, "project", "project"), ["c", "a", "b", "s", "v", "r", "f"])
        text = memory.readers_section(cfg, "role", "consolidator")
        self.assertIn("- no agent's prompt includes this file", text)


class Memory(LoopCase):
    def test_init_seeds_every_role_and_starts_model_memory_from_agent_control(self):
        res = self.run_py("tickets.py", "init")
        self.assertEqual(res.returncode, 0, res.stderr)
        mem = self.pool_memory()
        for role in ("creator", "worker", "verifier", "reviewer", "fixer", "consolidator"):
            self.assertIn("memory/role/%s.md" % role, mem)
        self.assertIn("memory/project.md", mem)
        self.assertIn("memory/model/python3.md", mem)   # the fake agent's command has no --model

    def test_lessons_are_collected_and_five_get_consolidated_by_the_keeper(self):
        self.run_py("tickets.py", "init")
        self.add(1)
        self.plan(worker={"1": ["lessons"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        snap = self.snap()
        self.assertEqual(snap.ticket("1")["state"], "landed")
        self.assertEqual(snap.lessons, {}, "the five lessons were consumed")
        self.assertIn("merged 5 lessons", self.pool_memory("memory/role/worker.md"))
        self.assertIn("memory/role/worker.md", snap.memory_state["last"])
        self.assertIsNone(snap.memory_state.get("job"))
        with open(os.path.join(self.tmp, "wt", "runs", "1", "r1-worker", "prompt.md")) as fh:
            self.assertIn("Role memory: worker", fh.read(), "agents start with their role memory")

    def test_fewer_than_five_fresh_lessons_wait(self):
        self.run_py("tickets.py", "init")

        def change(tx):
            tx.write("memory/inbox/a.json", {"layer": "role", "name": "worker", "text": "x", "at": osx.now()})
        self.pool.update(change, "test: one lesson")
        from core import memory
        self.assertEqual(memory.due(self.snap()), [])


class KeeperInterrupt(LoopCase):
    """memory_keeper stops its running agent on SIGTERM and records the attempt (#23 unit test)."""

    def test_sigterm_records_interrupted_run_and_keeps_lessons(self):
        import glob
        import time
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        self.run_py("tickets.py", "init")

        def change(tx):
            for i in range(5):
                tx.write("memory/inbox/k%d.json" % i, {"layer": "role", "name": "worker", "text": "l%d" % i,
                                                       "at": osx.now()})
        self.pool.update(change, "test: five lessons")
        self.plan(consolidator={"*": ["sleep"]})
        proc = subprocess.Popen([sys.executable, os.path.join(LOOP, "memory_keeper.py"), "--layer", "role",
                                 "--name", "worker"], cwd=self.proj, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        end = time.time() + 30
        while time.time() < end and not glob.glob(os.path.join(self.tmp, "wt", "runs", "memory", "*", "agent-1.log")):
            time.sleep(0.2)
        time.sleep(1)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 4)
        snap = self.snap()
        run = snap.memory_state["runs"][-1]
        self.assertTrue(run["interrupted"])
        self.assertFalse(run["ok"])
        self.assertEqual([c["outcome"] for c in run["cost"]], ["interrupted"])
        self.assertEqual(len(snap.lessons), 5)
        self.assertNotIn("memory/role/worker.md", snap.memory_state.get("last", {}))


class T156Acceptance(LoopCase):
    """Ticket #156: the consolidator (and its peer) is told which models read the file it rewrites."""

    HEADING = "## Who reads this file"
    NOBODY = "- no agent's prompt includes this file"
    SIZE = "Size and phrase the file for these readers; there is no length cap."
    OLD = "Every agent that starts in this role or on this model reads this file, so every line costs every session."
    LAYERS_SECTION = (
        "## What belongs in each layer (the one text of this contract)\n\n"
        "- **role**: principles for doing this role well, true on any project (\"assert a symbol exists\n"
        "  before asserting its value\"). Not how this project works.\n"
        "- **model**: how this model tends to go wrong and what helps, true on any project.\n"
        "- **project**: this project's design reasons and invariants - why things are the way they are and\n"
        "  what must stay true. Not commands, paths, line numbers or flags (those go stale; the code is\n"
        "  where to look them up).\n"
        "- Nowhere: the story of an incident (tickets hold that), concrete examples, anything that only\n"
        "  restates the rules already in the agent's prompt, and routing or staffing policy (\"this model\n"
        "  does not open tickets\") - that is configuration.\n"
        "- **Stale**: a lesson about a tool, script, step or rule the current loop does not have (the loop\n"
        "  is described in {loop_readme}) - drop it, even if it was true once. Lessons imported from\n"
        "  before devloop are often like that.\n\n"
        "Write principles, ways of thinking and rules of conduct, not cases. A ticket number may be cited\n"
        "as the source of a principle.\n")
    WORKER = ["- m-w1", "- m-w2", "- m-ws"]

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)

    @staticmethod
    def cmd(model):
        return ["python3", AGENT[1], "--model", model]

    def agents(self, worker_simple=True):
        c = self.cmd
        out = {"creator": [c("m-creator")], "worker": [c("m-w1"), c("m-w2")],
               "verifier": [c("m-v")], "reviewer": [c("m-r")], "fixer": [c("m-f")],
               "consolidator": [c("m-c")], "consolidator_peer": [c("m-c")]}
        if worker_simple:
            out["worker_simple"] = [c("m-ws"), c("m-w1")]
        return out

    def queue(self, layer, name):
        def change(tx):
            tx.write("memory/inbox/t156-%s-%s.json" % (layer, name),
                     {"layer": layer, "name": name, "text": "a lesson for #156", "source": "test",
                      "at": osx.now()})
        self.pool.update(change, "test: a lesson for %s/%s" % (layer, name))

    def keep(self, layer, name):
        self.queue(layer, name)
        res = self.run_py("memory_keeper.py", "--layer", layer, "--name", name)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        found = sorted(glob.glob(os.path.join(self.tmp, "wt", "runs", "memory", "%s-%s-*" % (layer, name))))
        self.assertTrue(found, "no run folder for %s/%s" % (layer, name))
        return found[-1]

    @staticmethod
    def text(path):
        with open(path) as fh:
            return fh.read()

    def section(self, prompt):
        lines = prompt.splitlines()
        self.assertTrue(self.HEADING in lines, "the prompt has a line exactly %r" % self.HEADING)
        start = lines.index(self.HEADING) + 1
        body = []
        for line in lines[start:]:
            if line.startswith("## "):
                break
            body.append(line)
        return "\n".join(body), [line for line in body if line.startswith("- ")]

    # ------------------------------------------------------------ item 1

    def test_item1_readers_from_the_config(self):
        from core import memory
        from core.config import Config
        self.assertTrue(hasattr(memory, "readers"), "core.memory has readers()")
        self.config(agents=self.agents())
        cfg = Config(self.proj)
        expect = {
            ("role", "creator"): ["m-creator"],
            ("role", "worker"): ["m-w1", "m-w2", "m-ws"],
            ("role", "verifier"): ["m-v"],
            ("role", "reviewer"): ["m-r"],
            ("role", "fixer"): ["m-f"],
            ("role", "consolidator"): [],
            ("role", "worker_simple"): [],
            ("role", "nosuchrole"): [],
            ("model", "m-v"): ["m-v"],
            ("model", "anything"): ["anything"],
            ("project", "project"): ["m-creator", "m-w1", "m-w2", "m-ws", "m-v", "m-r", "m-f"],
        }
        for (layer, name), want in expect.items():
            with self.subTest(layer=layer, name=name):
                self.assertEqual(list(memory.readers(cfg, layer, name)), want)
        # worker_simple unset: cfg.agent_chain falls back to worker
        self.config(agents=self.agents(worker_simple=False))
        cfg = Config(self.proj)
        self.assertEqual(list(memory.readers(cfg, "role", "worker")), ["m-w1", "m-w2"])
        self.assertEqual(list(memory.readers(cfg, "project", "project")),
                         ["m-creator", "m-w1", "m-w2", "m-v", "m-r", "m-f"])

    # ------------------------------------------------------------ item 2

    def test_item2_consolidator_prompt_lists_the_readers(self):
        self.config(agents=self.agents())
        cases = [("role", "worker", self.WORKER), ("role", "creator", ["- m-creator"]),
                 ("model", "m-v", ["- m-v"]),
                 ("project", "project", ["- m-creator", "- m-w1", "- m-w2", "- m-ws", "- m-v", "- m-r", "- m-f"]),
                 ("role", "consolidator", [self.NOBODY])]
        for layer, name, want in cases:
            with self.subTest(layer=layer, name=name):
                prompt = self.text(os.path.join(self.keep(layer, name), "prompt.md"))
                self.assertEqual(self.section(prompt)[1], want)

    # ------------------------------------------------------------ items 3 and 4

    def test_item3_item4_section_wording_and_old_sentence_gone(self):
        self.config(agents=self.agents())
        for layer, name, words in (("role", "worker", ("whole file", "every prompt")),
                                   ("model", "m-v", ("whole file", "every prompt")),
                                   ("project", "project", ("touches", "path"))):
            with self.subTest(layer=layer):
                prompt = self.text(os.path.join(self.keep(layer, name), "prompt.md"))
                body, _ = self.section(prompt)
                flat = " ".join(body.split())
                self.assertIn(self.SIZE, flat)
                for w in words:
                    self.assertIn(w, flat)
                self.assertNotIn(self.OLD, " ".join(prompt.split()))
        with open(os.path.join(LOOP, "prompts", "consolidator.md")) as fh:
            template = fh.read()
        self.assertNotIn(self.OLD, " ".join(template.split()))
        self.assertIn(self.LAYERS_SECTION, template, "'## What belongs in each layer' is unchanged")

    # ------------------------------------------------------------ item 5

    def test_item5_peer_and_revised_prompts_have_the_same_section(self):
        self.config(agents=self.agents(), unlimited_tokens=True)
        run = self.keep("role", "worker")
        for sub in ("peer", "revised"):
            with self.subTest(prompt=sub):
                path = os.path.join(run, sub, "prompt.md")
                self.assertTrue(os.path.isfile(path), "%s/prompt.md written" % sub)
                body, bullets = self.section(self.text(path))
                self.assertEqual(bullets, self.WORKER)
                self.assertIn(self.SIZE, " ".join(body.split()))


class MemoryFolderHelpers(LoopCase):
    """core.memory's folder helpers (devloop.memory, D-088)."""

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def test_file_path_maps_keys_into_the_folder(self):
        from core import memory
        f = os.path.join(self.tmp, "mem")
        self.config(memory=f)
        cfg = self.cfg()
        self.assertEqual(memory.file_path(cfg, "memory/role/worker.md"), os.path.join(f, "role", "worker.md"))
        self.assertEqual(memory.project_path(cfg), os.path.join(f, "project.md"))
        with self.assertRaises(ValueError):
            memory.file_path(cfg, "tickets/1.json")

    def test_files_lists_role_model_and_project_but_not_other_folders(self):
        from core import memory
        f = os.path.join(self.tmp, "mem")
        for rel in ("role/a.md", "model/b.md", "project.md", "discussions/c.md", "role/notes.txt"):
            os.makedirs(os.path.dirname(os.path.join(f, rel)), exist_ok=True)
            with open(os.path.join(f, rel), "w") as fh:
                fh.write("x")
        self.config(memory=f)
        self.assertEqual(sorted(memory.files(self.cfg())),
                         ["memory/model/b.md", "memory/project.md", "memory/role/a.md"])
        self.assertIsNone(memory.read(self.cfg(), "memory/role/none.md"))

    def test_write_files_outside_the_pool_makes_no_pool_commit(self):
        from core import memory
        self.run_py("tickets.py", "init")
        f = os.path.join(self.tmp, "mem")
        self.config(memory=f)
        cfg = self.cfg()
        self.assertFalse(memory.in_pool(cfg, self.pool))
        commits = git(self.pool_path, "rev-list", "--count", "HEAD")
        memory.write_files(cfg, self.pool, {"memory/role/x.md": "hello\n"}, "test")
        self.assertEqual(memory.read(cfg, "memory/role/x.md"), "hello\n")
        self.assertEqual(git(self.pool_path, "rev-list", "--count", "HEAD"), commits)

    def test_write_files_in_the_pool_is_one_commit(self):
        from core import memory
        self.run_py("tickets.py", "init")
        cfg = self.cfg()
        self.assertTrue(memory.in_pool(cfg, self.pool))
        commits = int(git(self.pool_path, "rev-list", "--count", "HEAD"))
        memory.write_files(cfg, self.pool, {"memory/role/x.md": "a\n", "memory/model/y.md": "b\n"}, "test")
        self.assertEqual(int(git(self.pool_path, "rev-list", "--count", "HEAD")), commits + 1)
        self.assertEqual(self.pool_memory("memory/model/y.md"), "b\n")

    def test_seed_never_overwrites_a_file_in_the_folder(self):
        from core import memory
        self.run_py("tickets.py", "init")
        f = os.path.join(self.tmp, "mem")
        os.makedirs(os.path.join(f, "role"))
        with open(os.path.join(f, "role", "worker.md"), "w") as fh:
            fh.write("mine\n")
        self.config(memory=f)
        memory.seed(self.cfg(), self.pool, ["python3"])
        self.assertEqual(memory.read(self.cfg(), "memory/role/worker.md"), "mine\n")
        self.assertIsNotNone(memory.read(self.cfg(), "memory/role/fixer.md"))

    def test_config_memory_dir_forms(self):
        self.assertEqual(self.cfg().memory_dir, os.path.join(self.pool_path, "memory"))
        self.config(memory="a/../m")
        self.assertEqual(self.cfg().memory_dir, os.path.join(self.proj, "m"))
        for bad in (5, True, [], ""):
            self.config(memory=bad)
            with self.assertRaises(osx.OSFailure):
                self.cfg()


class MemoryGitWriter(LoopCase):
    """core.memory.commit_into: memory written into a git checkout is one fast-forward commit (#16)."""

    def setUp(self):
        super().setUp()
        self.config(memory="memory")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "config")

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def text(self, rel):
        try:
            with open(os.path.join(self.proj, rel)) as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def test_commit_holds_only_the_files_and_leaves_other_edits(self):
        from core import memory
        with open(os.path.join(self.proj, "README.md"), "a") as fh:
            fh.write("local\n")
        before = git(self.proj, "rev-parse", "main")
        new = memory.write_files(self.cfg(), self.pool, {"memory/role/a.md": "A\n"}, "memory: test")
        self.assertIsNone(new)   # write_files returns nothing; the commit is on main
        self.assertEqual(git(self.proj, "rev-parse", "main^"), before)
        self.assertEqual(git(self.proj, "show", "--name-only", "--format=", "main"), "memory/role/a.md")
        self.assertEqual(git(self.proj, "log", "-1", "--format=%an <%ae>", "main"), "devloop <devloop@localhost>")
        self.assertEqual(self.text("memory/role/a.md"), "A\n")
        self.assertEqual(git(self.proj, "status", "--porcelain"), "M README.md")
        # the same text again changes nothing: no commit
        memory.write_files(self.cfg(), self.pool, {"memory/role/a.md": "A\n"}, "memory: test")
        self.assertEqual(git(self.proj, "rev-parse", "main^"), before)

    def test_lost_move_is_read_again_and_written_on_the_new_tip(self):
        from core import memory
        real, calls = memory._move, []

        def racing(repo, branch, new, old):
            calls.append(old)
            if len(calls) == 1:   # someone commits on main between reading it and moving it
                with open(os.path.join(self.proj, "other.txt"), "w") as fh:
                    fh.write("x\n")
                git(self.proj, "add", "other.txt")
                git(self.proj, "commit", "-q", "-m", "other")
            return real(repo, branch, new, old)
        memory._move = racing
        self.addCleanup(setattr, memory, "_move", real)
        memory.write_files(self.cfg(), self.pool, {"memory/role/a.md": "A\n"}, "memory: test")
        self.assertEqual(len(calls), 2)
        self.assertEqual(git(self.proj, "log", "-1", "--format=%s", "main^"), "other")
        self.assertEqual(git(self.proj, "show", "--name-only", "--format=", "main"), "memory/role/a.md")
        self.assertEqual(self.text("memory/role/a.md"), "A\n")
        self.assertEqual(self.text("other.txt"), "x\n")

    def test_a_move_lost_every_time_gives_up_after_eight_tries(self):
        from core import memory, gitx
        real, calls = memory._move, []
        memory._move = lambda *a: (calls.append(1), ("lost", "moved"))[1]
        self.addCleanup(setattr, memory, "_move", real)
        before = git(self.proj, "rev-parse", "main")
        with self.assertRaises(gitx.GitError):
            memory.write_files(self.cfg(), self.pool, {"memory/role/a.md": "A\n"}, "memory: test")
        self.assertEqual(len(calls), 8)
        self.assertEqual(git(self.proj, "rev-parse", "main"), before)
        self.assertIsNone(self.text("memory/role/a.md"))

    def test_detached_head_fails_and_writes_nothing(self):
        from core import memory, osx as core_osx
        git(self.proj, "checkout", "-q", "--detach")
        before = git(self.proj, "rev-parse", "main")
        with self.assertRaises(core_osx.OSFailure):
            memory.write_files(self.cfg(), self.pool, {"memory/role/a.md": "A\n"}, "memory: test")
        self.assertEqual(git(self.proj, "rev-parse", "main"), before)
        self.assertEqual(git(self.proj, "rev-parse", "HEAD"), before)
        self.assertIsNone(self.text("memory/role/a.md"))

    def test_local_edit_of_the_memory_file_refuses_and_keeps_it(self):
        from core import memory, gitx
        memory.write_files(self.cfg(), self.pool, {"memory/role/a.md": "A\n"}, "memory: test")
        with open(os.path.join(self.proj, "memory", "role", "a.md"), "w") as fh:
            fh.write("MINE\n")
        before = git(self.proj, "rev-parse", "main")
        with self.assertRaises(gitx.GitError):
            memory.write_files(self.cfg(), self.pool, {"memory/role/a.md": "B\n"}, "memory: test")
        self.assertEqual(git(self.proj, "rev-parse", "main"), before)
        self.assertEqual(self.text("memory/role/a.md"), "MINE\n")

    def test_diverged_remote_fails(self):
        from core import memory, gitx
        bare = os.path.join(self.tmp, "bare.git")
        git(self.tmp, "clone", "-q", "--bare", self.proj, bare)
        git(self.proj, "remote", "add", "origin", bare)
        other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", bare, other)
        git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "theirs")
        git(other, "push", "-q", "origin", "main")
        git(self.proj, "commit", "-q", "--allow-empty", "-m", "ours")
        self.config(remote="origin")
        before = git(self.proj, "rev-parse", "main")
        with self.assertRaises(gitx.GitError):
            memory.write_files(self.cfg(), self.pool, {"memory/role/a.md": "A\n"}, "memory: test")
        self.assertEqual(git(self.proj, "rev-parse", "main"), before)


class T7Acceptance(LoopCase):
    """Ticket #7: tickets.py lessons imports a project's own old memory as lessons."""

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def lessons(self, *args):
        return self.run_py("tickets.py", "lessons", *args)

    def rows(self, layer, name):
        return [r for r in self.snap().lessons.values() if r.get("layer") == layer and r.get("name") == name]

    def pool_commits(self):
        return int(git(self.pool_path, "rev-list", "--count", "HEAD"))

    def test_item1_every_bullet_line_is_queued_once_with_its_text(self):
        self.run_py("tickets.py", "init")
        path = self.write("old-worker.md", "# Old worker notes\n\nA plain line, not a bullet.\n"
                                           "- dash lesson\n* star lesson\n  - indented lesson  \n")
        res = self.lessons("--layer", "role", "--name", "worker", "--file", path)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("3", res.stdout)
        self.assertIn("memory/role/worker.md", res.stdout)
        rows = self.rows("role", "worker")
        self.assertEqual(len(self.snap().lessons), 3, "only the three bullets are queued")
        self.assertEqual(sorted(r["text"] for r in rows), ["dash lesson", "indented lesson", "star lesson"])
        for r in rows:
            self.assertIn(path, r.get("source", ""))
            self.assertTrue(r.get("at"))

    def test_item2_one_pool_commit_and_the_memory_file_is_untouched(self):
        self.run_py("tickets.py", "init")
        before = self.pool_memory("memory/role/worker.md")
        commits = self.pool_commits()
        path = self.write("old.md", "- one\n- two\n")
        res = self.lessons("--layer", "role", "--name", "worker", "--file", path)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(len(self.rows("role", "worker")), 2)
        self.assertEqual(self.pool_commits(), commits + 1, "one pool commit")
        self.assertEqual(self.pool_memory("memory/role/worker.md"), before)

    def test_item3_two_files_for_the_same_memory_both_stay_queued(self):
        self.run_py("tickets.py", "init")
        a = self.write("a.md", "- a one\n- a two\n")
        b = self.write("b.md", "- b one\n- b two\n- b three\n")
        for path in (a, b):
            res = self.lessons("--layer", "role", "--name", "worker", "--file", path)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        rows = self.rows("role", "worker")
        self.assertEqual(len(rows), 5)
        self.assertEqual(sorted(r["text"] for r in rows),
                         ["a one", "a two", "b one", "b three", "b two"])

    def test_item4_five_imported_lessons_are_consolidated_by_the_keeper(self):
        self.run_py("tickets.py", "init")
        path = self.write("five.md", "".join("- lesson %d from #imp\n" % i for i in range(1, 6)))
        res = self.lessons("--layer", "role", "--name", "worker", "--file", path)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(len(self.rows("role", "worker")), 5)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        snap = self.snap()
        self.assertIn("merged 5 lessons", self.pool_memory("memory/role/worker.md"))
        self.assertEqual(snap.lessons, {}, "the imported lessons were consumed")

    def test_item5_project_layer_without_name_uses_name_project(self):
        self.run_py("tickets.py", "init")
        path = self.write("project-old.md", "- a project reason\n- another invariant\n")
        res = self.lessons("--layer", "project", "--file", path)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        rows = list(self.snap().lessons.values())
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["layer"] == "project" and r["name"] == "project" for r in rows), rows)


class T15Acceptance(LoopCase):
    """Ticket #15: memory files live in the folder devloop.memory names."""

    def setUp(self):
        super().setUp()
        # a fake agent keys its plan on DEVLOOP_TICKET / DEVLOOP_GROUP: inherited ones must not reach it
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        self.F = os.path.join(self.tmp, "memfolder")

    # ------------------------------------------------------------ helpers

    def put(self, rel, text):
        path = os.path.join(self.F, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    @staticmethod
    def read(path):
        try:
            with open(path, "rb") as fh:
                return fh.read().decode("utf-8")
        except FileNotFoundError:
            return None

    def pool_put(self, rel, text):
        self.pool.update(lambda tx: tx.write_text(rel, text), "test: %s" % rel)

    def queue(self, n):
        def change(tx):
            for i in range(1, n + 1):
                tx.write("memory/inbox/t15-%d.json" % i, {"layer": "role", "name": "worker",
                                                          "text": "lesson %d from #9" % i,
                                                          "source": "test", "at": osx.now()})
        self.pool.update(change, "test: %d lessons" % n)

    def files_under(self, root):
        out = set()
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                out.add(os.path.relpath(os.path.join(dirpath, name), root))
        return out

    def stamped_prompt(self, pattern):
        found = sorted(glob.glob(os.path.join(self.tmp, "wt", "runs", pattern, "prompt.md")))
        self.assertTrue(found, "no prompt saved at %s" % pattern)
        return self.read(found[-1])

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    # ------------------------------------------------------------ item 1

    def test_item1_memory_dir_resolves_absent_null_relative_and_absolute(self):
        cfg = self.cfg()
        self.assertEqual(getattr(cfg, "memory_dir", None), os.path.join(cfg.pool_path, "memory"))
        self.config(memory=None)
        cfg = self.cfg()
        self.assertEqual(getattr(cfg, "memory_dir", None), os.path.join(cfg.pool_path, "memory"))
        self.config(memory="m/x")
        self.assertEqual(getattr(self.cfg(), "memory_dir", None),
                         os.path.normpath(os.path.join(self.proj, "m/x")))
        odd = os.path.join(self.tmp, "a", "..", "b", ".", "mem")
        self.config(memory=odd)
        self.assertEqual(getattr(self.cfg(), "memory_dir", None), os.path.normpath(odd))

    # ------------------------------------------------------------ item 2

    def test_item2_any_other_memory_value_is_refused(self):
        for bad in (5, True, [], ""):
            with self.subTest(value=bad):
                self.config(memory=bad)
                with self.assertRaises(osx.OSFailure) as ctx:
                    self.cfg()
                self.assertIn("devloop.memory", str(ctx.exception))

    # ------------------------------------------------------------ item 3

    def test_item3_worker_and_verifier_prompts_read_the_folder(self):
        self.put("role/worker.md", "# Role memory: worker\n\n- T15-F-WORKER-MARK\n")
        self.put("role/verifier.md", "# Role memory: verifier\n\n- T15-F-VERIFIER-MARK\n")
        self.put("model/python3.md", "# Model memory: python3\n\n- T15-F-PYTHON3-MARK\n")
        self.pool_put("memory/role/worker.md", "# Role memory: worker\n\n- T15-POOL-WORKER-MARK\n")
        self.config(memory=self.F)
        self.add(1, needs_verifier=True)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        project = os.path.join(self.F, "project.md")
        worker = self.read(os.path.join(self.tmp, "wt", "runs", "1", "r1-worker", "prompt.md")) or ""
        self.assertIn("T15-F-WORKER-MARK", worker)
        self.assertIn("T15-F-PYTHON3-MARK", worker)
        self.assertNotIn("T15-POOL-WORKER-MARK", worker)
        self.assertIn(project, worker)
        verifier = self.read(os.path.join(self.tmp, "wt", "runs", "1", "r1-verifier", "prompt.md")) or ""
        self.assertIn("T15-F-VERIFIER-MARK", verifier)
        self.assertIn(project, verifier)

    # ------------------------------------------------------------ item 4

    def test_item4_creator_prompt_reads_the_folder(self):
        self.put("role/creator.md", "# Role memory: creator\n\n- T15-F-CREATOR-MARK\n")
        self.config(memory=self.F)
        self.run_py("ticket_creator.py", "--request", "x")
        prompt = self.stamped_prompt(os.path.join("creator", "*-a1"))
        self.assertIn("T15-F-CREATOR-MARK", prompt)
        self.assertIn(os.path.join(self.F, "project.md"), prompt)

    # ------------------------------------------------------------ items 5 and 6

    MERGED = "# Role memory: worker\n\n- merged 5 lessons\n"

    def keeper_fixture(self):
        self.put("role/worker.md", "# Role memory: worker\n\n- T15-F-KEEPER-MARK\n")
        self.pool_put("memory/role/worker.md", "# Role memory: worker\n\n- T15-POOL-KEEPER-MARK\n")
        self.queue(5)
        return self.read(os.path.join(self.pool_path, "memory", "role", "worker.md"))

    def test_item5_keeper_consolidates_into_the_folder(self):
        pool_before = self.keeper_fixture()
        self.config(memory=self.F)
        res = self.run_py("memory_keeper.py", "--layer", "role", "--name", "worker")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("T15-F-KEEPER-MARK", self.stamped_prompt(os.path.join("memory", "role-worker-*")))
        self.assertEqual(self.read(os.path.join(self.F, "role", "worker.md")), self.MERGED)
        self.assertEqual(self.read(os.path.join(self.pool_path, "memory", "role", "worker.md")), pool_before)
        snap = self.snap()
        left = [r for r in snap.lessons.values() if str(r.get("text", "")).endswith("from #9")]
        self.assertEqual(left, [], "the five lessons were consumed")
        self.assertIn("memory/role/worker.md", (snap.memory_state or {}).get("last") or {})

    def test_item6_unlimited_keeper_keeps_the_discussion_in_the_pool(self):
        self.put("role/worker.md", "# Role memory: worker\n\n- T15-F-KEEPER-MARK\n")
        self.queue(5)
        self.config(memory=self.F, unlimited_tokens=True)
        res = self.run_py("memory_keeper.py", "--layer", "role", "--name", "worker")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        talks = glob.glob(os.path.join(self.pool_path, "memory", "discussions", "*"))
        self.assertEqual(len(talks), 1, "one discussion file under the pool's memory/discussions/")
        self.assertEqual(self.files_under(self.F), {os.path.join("role", "worker.md")})
        self.assertEqual(self.read(os.path.join(self.F, "role", "worker.md")), self.MERGED)

    # ------------------------------------------------------------ item 7

    def memory_rows(self, page):
        import html as htmlmod
        import re
        start = page.find("<h2>記憶</h2>")
        self.assertGreaterEqual(start, 0, "the page has a memory section")
        table = page[start:page.find("</table>", start)]
        rows = [[htmlmod.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
                for tr in re.findall(r"<tr>(.*?)</tr>", table, re.S)]
        return rows[0], {r[0]: r for r in rows[1:] if r}

    def test_item7_board_lists_the_folder_files_not_the_pool_ones(self):
        for rel in ("role/consolidator.md", "role/worker.md", "model/folder-only.md", "project.md"):
            self.put(rel, "# %s\n\n- some text\n" % rel)
        self.pool_put("memory/model/pool-only.md", "# Model memory: pool-only\n")
        self.pool_put("memory/discussions/talk.md", "a talk\n")
        self.queue(2)
        self.config(memory=self.F)
        res = self.run_py("board.py", "html", "--query", "tab=tickets")
        self.assertEqual(res.returncode, 0, res.stderr)
        page = res.stdout
        header, rows = self.memory_rows(page)
        for label in ("memory/role/consolidator.md", "memory/role/worker.md", "memory/model/folder-only.md",
                      "memory/project.md"):
            self.assertIn(label, rows)
        self.assertIn("暫存區", header)
        self.assertEqual(rows["memory/role/worker.md"][header.index("暫存區")], "2")
        self.assertNotIn("pool-only", page)
        self.assertNotIn("discussions/talk", page)

    # ------------------------------------------------------------ item 8

    def test_item8_init_seeds_the_folder_not_the_pool(self):
        self.assertFalse(os.path.exists(self.F))
        self.config(memory=self.F)
        res = self.run_py("tickets.py", "init")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        for role in ("creator", "worker", "verifier", "reviewer", "fixer", "consolidator"):
            self.assertTrue(os.path.isfile(os.path.join(self.F, "role", "%s.md" % role)), role)
        self.assertTrue(os.path.isfile(os.path.join(self.F, "model", "python3.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.F, "project.md")))
        mem = os.path.join(self.pool_path, "memory")
        self.assertFalse(os.path.exists(os.path.join(mem, "role")))
        self.assertFalse(os.path.exists(os.path.join(mem, "model")))
        self.assertFalse(os.path.exists(os.path.join(mem, "project.md")))


class T17Acceptance(LoopCase):
    """Ticket #17: a project's missing role or model memory starts as agent-control's file."""

    ROLE_HEADING = ("# Role memory: {}\n\nPrinciples learned while doing this role. Rules for the job "
                    "are in the prompt; this holds what experience added.\n")
    MODEL_HEADING = "# Model memory: {}\n\nHow this model tends to go wrong, and what helps.\n"
    PROJECT_HEADING = ("# Project memory\n\nThis project's design reasons and invariants: why things "
                       "are the way they are, and what must stay true.\n")
    OPUS = ["claude", "-p", "--model", "opus"]

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        self.F = os.path.join(self.tmp, "memfolder")   # absolute; self.tmp is not a git repository

    @staticmethod
    def read(path):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def home(self, rel):
        return self.read(os.path.join(HOME, "memory", rel))

    def opus_worker(self):
        chains = {r: AGENT for r in ROLES}
        chains["worker"] = self.OPUS
        self.config(agents=chains, memory=self.F)

    def init(self):
        res = self.run_py("tickets.py", "init")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    # ------------------------------------------------------------ item 1

    def test_item1_init_copies_agent_control_files_and_heads_the_rest(self):
        self.assertFalse(os.path.exists(self.F))
        self.opus_worker()
        self.init()
        for role in ("creator", "worker", "verifier", "reviewer", "fixer", "consolidator"):
            with self.subTest(role=role):
                expected = self.home("role/%s.md" % role)
                if expected is None:
                    expected = self.ROLE_HEADING.format(role).encode()
                self.assertEqual(self.read(os.path.join(self.F, "role", role + ".md")), expected)
        opus = self.home("model/opus.md")
        self.assertIsNotNone(opus, "HOME/memory/model/opus.md exists (ticket premise)")
        self.assertEqual(self.read(os.path.join(self.F, "model", "opus.md")), opus)
        self.assertEqual(self.read(os.path.join(self.F, "model", "python3.md")),
                         self.MODEL_HEADING.format("python3").encode())
        self.assertEqual(self.read(os.path.join(self.F, "project.md")), self.PROJECT_HEADING.encode())

    # ------------------------------------------------------------ item 2

    def test_item2_init_never_overwrites(self):
        os.makedirs(os.path.join(self.F, "role"))
        with open(os.path.join(self.F, "role", "worker.md"), "w") as fh:
            fh.write("mine\n")
        self.opus_worker()
        self.init()
        self.assertEqual(self.read(os.path.join(self.F, "role", "worker.md")), b"mine\n")
        # the files init did create next to it start as agent-control's (item 1)
        verifier = self.home("role/verifier.md")
        self.assertIsNotNone(verifier, "HOME/memory/role/verifier.md exists (ticket premise)")
        self.assertEqual(self.read(os.path.join(self.F, "role", "verifier.md")), verifier)

    # ------------------------------------------------------------ item 3

    def test_item3_init_queues_no_lessons(self):
        self.opus_worker()
        self.init()
        inbox = glob.glob(os.path.join(self.pool_path, "memory", "inbox", "*.json"))
        self.assertEqual(sorted(os.path.basename(p) for p in inbox), [])

    # ------------------------------------------------------------ item 4

    def test_item4_keeper_starts_an_unseeded_file_from_agent_control(self):
        opus = self.home("model/opus.md")
        self.assertIsNotNone(opus, "HOME/memory/model/opus.md exists (ticket premise)")

        def change(tx):
            for i in range(1, 6):
                tx.write("memory/inbox/t17-%d.json" % i, {"layer": "model", "name": "opus",
                                                          "text": "lesson %d from #9" % i,
                                                          "source": "test", "at": osx.now()})
        self.pool.update(change, "test: 5 opus lessons")
        self.config(memory=self.F)
        self.assertFalse(os.path.exists(os.path.join(self.F, "model", "opus.md")))
        self.run_py("memory_keeper.py", "--layer", "model", "--name", "opus")
        found = sorted(glob.glob(os.path.join(self.tmp, "wt", "runs", "memory", "model-opus-*", "prompt.md")))
        self.assertTrue(found, "no consolidator prompt saved")
        prompt = self.read(found[-1]).decode("utf-8")
        self.assertIn(opus.decode("utf-8").strip(), prompt)

    # ------------------------------------------------------------ item 5

    def test_item5_reading_uses_agent_control_text_without_copying(self):
        verifier = self.home("role/verifier.md")
        self.assertIsNotNone(verifier, "HOME/memory/role/verifier.md exists (ticket premise)")
        os.makedirs(self.F)
        with open(os.path.join(self.F, "project.md"), "w") as fh:
            fh.write(self.PROJECT_HEADING)
        self.config(memory=self.F)
        self.add(1, needs_verifier=True)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        prompt = self.read(os.path.join(self.tmp, "wt", "runs", "1", "r1-verifier", "prompt.md"))
        self.assertIsNotNone(prompt, "verifier prompt saved")
        self.assertIn(verifier.decode("utf-8").strip(), prompt.decode("utf-8"))
        self.assertIsNone(self.read(os.path.join(self.F, "role", "verifier.md")))


class T16Acceptance(LoopCase):
    """Ticket #16: memory written into a git repository is one fast-forward commit on its branch."""

    MERGED = "# Role memory: worker\n\n- merged 5 lessons\n"
    OLD = "# Role memory: worker\n\n- before t16\n"
    REL = "memory/role/worker.md"

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)

    # ------------------------------------------------------------ helpers

    @staticmethod
    def read(path):
        try:
            with open(path, "rb") as fh:
                return fh.read().decode("utf-8")
        except FileNotFoundError:
            return None

    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    @staticmethod
    def raw(cwd, *args):
        """git's stdout unstripped, or None when git fails."""
        res = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
        return res.stdout if res.returncode == 0 else None

    def rev(self, cwd, ref):
        out = self.raw(cwd, "rev-parse", "--verify", "-q", ref)
        return out.strip() if out else None

    def queue(self):
        def change(tx):
            for i in range(1, 6):
                tx.write("memory/inbox/t16-%d.json" % i, {"layer": "role", "name": "worker",
                                                          "text": "lesson %d from #9" % i,
                                                          "source": "test", "at": osx.now()})
        self.pool.update(change, "test: five lessons")

    def left(self):
        return [r for r in self.snap().lessons.values() if str(r.get("text", "")).endswith("from #9")]

    def commit_memory_file(self, text=None):
        self.write(os.path.join(self.proj, self.REL), text or self.OLD)
        git(self.proj, "add", self.REL)
        git(self.proj, "commit", "-q", "-m", "seed worker memory")

    def keeper(self):
        return self.run_py("memory_keeper.py", "--layer", "role", "--name", "worker")

    def with_origin(self):
        bare = os.path.join(self.tmp, "origin.git")
        git(self.tmp, "clone", "-q", "--bare", self.proj, bare)
        git(self.proj, "remote", "add", "origin", bare)
        git(self.proj, "fetch", "-q", "origin")
        self.config(memory="memory", remote="origin")
        return bare

    def assert_one_memory_commit(self, cwd, branch, parent):
        tip = self.rev(cwd, branch)
        self.assertNotEqual(tip, parent, "%s did not move" % branch)
        self.assertEqual(self.raw(cwd, "rev-list", "--count", "%s..%s" % (parent, branch)), "1\n")
        self.assertEqual(self.rev(cwd, branch + "^"), parent)
        return tip

    # ------------------------------------------------------------ item 1

    def test_item1_one_commit_on_main_and_the_rest_of_the_checkout_untouched(self):
        self.commit_memory_file()
        self.config(memory="memory")
        with open(os.path.join(self.proj, "README.md"), "a") as fh:
            fh.write("local edit\n")
        self.queue()
        before = self.rev(self.proj, "main")
        status_before = self.raw(self.proj, "status", "--porcelain")
        self.assertIn("README.md", status_before)
        self.assertIn("board/config.json", status_before)
        res = self.keeper()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assert_one_memory_commit(self.proj, "main", before)
        self.assertEqual(self.raw(self.proj, "show", "--name-only", "--format=", "main").strip(), self.REL)
        self.assertTrue((self.raw(self.proj, "log", "-1", "--format=%B", "main") or "").startswith(
            "memory: consolidate"))
        self.assertEqual(self.raw(self.proj, "show", "main:" + self.REL), self.MERGED)
        self.assertEqual(self.read(os.path.join(self.proj, self.REL)), self.MERGED)
        self.assertEqual(self.raw(self.proj, "status", "--porcelain"), status_before)

    # ------------------------------------------------------------ item 2

    def test_item2_linked_worktree_on_another_branch_gets_the_commit(self):
        memwt = os.path.join(self.tmp, "memwt")
        git(self.proj, "worktree", "add", "-q", memwt, "-b", "mem", "main")
        self.config(memory=os.path.join(memwt, "memory"))
        self.queue()
        main_before = self.rev(self.proj, "main")
        mem_before = self.rev(self.proj, "mem")
        res = self.keeper()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assert_one_memory_commit(self.proj, "mem", mem_before)
        self.assertEqual(self.rev(self.proj, "main"), main_before)
        self.assertEqual(self.read(os.path.join(memwt, "memory", "role", "worker.md")), self.MERGED)

    # ------------------------------------------------------------ item 3

    def test_item3_the_commit_is_pushed_to_devloop_remote(self):
        self.commit_memory_file()
        bare = self.with_origin()
        self.queue()
        before = self.rev(self.proj, "main")
        self.assertEqual(self.rev(bare, "main"), before)
        res = self.keeper()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        tip = self.assert_one_memory_commit(self.proj, "main", before)
        self.assertEqual(self.rev(bare, "main"), tip)
        self.assertEqual(self.raw(bare, "show", "main:" + self.REL), self.MERGED)

    # ------------------------------------------------------------ item 4

    def test_item4_remote_ahead_is_fast_forwarded_first(self):
        self.commit_memory_file()
        bare = self.with_origin()
        other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", bare, other)
        git(other, "config", "user.name", "o")
        git(other, "config", "user.email", "o@o")
        self.write(os.path.join(other, "Z.txt"), "z\n")
        git(other, "add", "Z.txt")
        git(other, "commit", "-q", "-m", "add Z")
        git(other, "push", "-q", "origin", "main")
        z = self.rev(other, "main")
        with open(os.path.join(self.proj, "README.md"), "a") as fh:
            fh.write("local edit\n")
        readme = self.read(os.path.join(self.proj, "README.md"))
        self.queue()
        self.assertNotEqual(self.rev(self.proj, "main"), z)
        res = self.keeper()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.rev(self.proj, "main"), self.rev(bare, "main"))
        self.assert_one_memory_commit(self.proj, "main", z)
        self.assertEqual(self.read(os.path.join(self.proj, "Z.txt")), "z\n")
        self.assertEqual(self.read(os.path.join(self.proj, "README.md")), readme)

    # ------------------------------------------------------------ item 5

    def test_item5_refused_push_keeps_the_local_commit_and_pages_a_decision(self):
        self.commit_memory_file()
        bare = self.with_origin()
        hook = os.path.join(bare, "hooks", "pre-receive")
        self.write(hook, "#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)
        self.queue()
        before = self.rev(self.proj, "main")
        origin_before = self.rev(bare, "main")
        pages_before = set(self.snap().inbox)
        res = self.keeper()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assert_one_memory_commit(self.proj, "main", before)
        self.assertEqual(self.raw(self.proj, "show", "main:" + self.REL), self.MERGED)
        self.assertEqual(self.rev(bare, "main"), origin_before)
        inbox = self.snap().inbox
        new = [inbox[k] for k in set(inbox) - pages_before]
        self.assertEqual(len(new), 1, new)
        self.assertEqual(new[0].get("kind"), "decision")
        self.assertIn("role/worker.md", new[0].get("text", ""))
        self.assertEqual(self.left(), [], "the five lessons were consumed")

    # ------------------------------------------------------------ item 6

    def test_item6_a_refused_checkout_writes_nothing_and_keeps_the_lessons(self):
        self.commit_memory_file()
        self.config(memory="memory")
        path = os.path.join(self.proj, self.REL)
        self.write(path, "MINE\n")
        self.queue()
        before = self.rev(self.proj, "main")
        res = self.keeper()
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(self.rev(self.proj, "main"), before)
        self.assertEqual(self.read(path), "MINE\n")
        self.assertEqual(len(self.left()), 5)
        with open(os.path.join(self.pool_path, "memory", "state.json")) as fh:
            runs = json.load(fh).get("runs") or []
        self.assertTrue(runs)
        self.assertIs(runs[-1].get("ok"), False)

    # ------------------------------------------------------------ item 7

    def test_item7_init_seeds_the_folder_in_one_commit(self):
        self.config(memory="memory")
        git(self.proj, "commit", "-q", "-am", "config: memory folder")
        self.assertEqual(self.raw(self.proj, "status", "--porcelain"), "")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "memory")))
        before = self.rev(self.proj, "main")
        res = self.run_py("tickets.py", "init")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assert_one_memory_commit(self.proj, "main", before)
        changed = (self.raw(self.proj, "show", "--name-only", "--format=", "main") or "").split()
        self.assertTrue(changed)
        self.assertTrue(all(p.startswith("memory/") for p in changed), changed)
        self.assertIn("memory/project.md", changed)
        self.assertEqual(self.raw(self.proj, "status", "--porcelain"), "")


class T18Acceptance(unittest.TestCase):
    """#18: agent-control's memory/ is its consolidated memory, named by its config."""

    TRACKED = ("memory/role/consolidator.md", "memory/role/creator.md", "memory/role/fixer.md",
               "memory/role/reviewer.md", "memory/role/verifier.md", "memory/role/worker.md",
               "memory/model/fable.md", "memory/model/opus.md", "memory/model/sonnet.md",
               "memory/project.md")

    def tracked(self):
        res = subprocess.run(["git", "-C", HOME, "ls-files", "memory"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout.splitlines()

    def test_config_names_memory(self):
        with open(os.path.join(HOME, "board", "config.json")) as fh:
            devloop = json.load(fh)["devloop"]
        self.assertIn("memory", devloop)
        self.assertEqual(devloop["memory"], "memory")

    def test_consolidated_files_are_tracked(self):
        files = self.tracked()
        missing = [p for p in self.TRACKED if p not in files]
        self.assertEqual(missing, [], "not tracked under memory/")

    def test_old_cards_are_not_tracked(self):
        files = self.tracked()
        bad = [p for p in files
               if os.path.basename(p) == "README.md" or p.endswith(".inbox.md")
               or p in ("memory/role/implementer.md", "memory/role/opener.md")]
        self.assertEqual(bad, [], "old cards still tracked under memory/")


class T19Acceptance(unittest.TestCase):
    """#19: devloop/README.md lists every role; the memory docs follow D-088."""

    ROLES = ["creator", "worker", "worker_simple", "verifier", "reviewer", "fixer",
             "consolidator", "consolidator_peer"]

    def read(self, *p):
        with open(os.path.join(HOME, *p), encoding="utf-8") as f:
            return f.read()

    def section(self, text, heading):
        self.assertIn("\n" + heading + "\n", text, "no section %r" % heading)
        body = text.split("\n" + heading + "\n", 1)[1]
        return body.split("\n## ", 1)[0]

    @staticmethod
    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]

    def role_rows(self):
        sec = self.section(self.read("devloop", "README.md"), "## Roles")
        lines = [l for l in sec.splitlines() if l.strip().startswith("|")]
        self.assertGreaterEqual(len(lines), 3, "the Roles section has no table")
        rows = [l for l in lines[2:]]  # header and separator first
        return {self.cells(r)[0]: r for r in rows}, [self.cells(r)[0] for r in rows]

    def test_item1_one_row_per_role(self):
        by_name, firsts = self.role_rows()
        self.assertEqual(sorted(firsts), sorted("`%s`" % r for r in self.ROLES))
        for name, row in by_name.items():
            cells = self.cells(row)
            self.assertGreaterEqual(len([c for c in cells if c]), 5,
                                    "row %s lacks script, when, models, writes" % name)
            self.assertIn(".py", row, "row %s names no script" % name)
            self.assertTrue(any(m in row for m in ("fable", "opus", "sonnet")),
                            "row %s names no model" % name)

    def test_item2_consolidator_rows(self):
        by_name, _ = self.role_rows()
        self.assertIn("`consolidator`", by_name)
        self.assertIn("`consolidator_peer`", by_name)
        for w in ("memory_keeper.py", "ticket_controller.py", "5 lessons", "3 days", "24 h",
                  "fable", "opus"):
            self.assertIn(w, by_name["`consolidator`"])
        self.assertIn("unlimited_tokens", by_name["`consolidator_peer`"])

    def test_item3_memory_section(self):
        text = self.read("devloop", "README.md")
        mem = self.section(text, "## Memory")
        for w in ("devloop.memory", "memory/inbox", "memory/state.json", "memory/discussions",
                  "agent-control's memory/"):
            self.assertIn(w, mem)
        for w in ("kept **in the pool**", "never copied in", "`memory/project.md` in the pool"):
            self.assertNotIn(w, text)
        settings = self.section(text, "## Project settings (`board/config.json`, key `devloop`)")
        self.assertIn("`memory`", settings)

    def test_item4_design_memory_bullet(self):
        text = self.read("docs", "design", "dev-loop-v2.md")
        bullets = [b for b in text.split("\n- ") if b.startswith("**Memory**")]
        self.assertEqual(len(bullets), 1, "section 3 has one memory bullet")
        self.assertNotIn("(also in the pool)", bullets[0])
        self.assertIn("devloop.memory", bullets[0])

    def test_item5_root_readme_memory_row(self):
        text = self.read("README.md")
        rows = [self.cells(l) for l in text.splitlines() if l.strip().startswith("|")]
        mine = [r for r in rows if r and r[0] == "`memory/`"]
        self.assertEqual(len(mine), 1, "no table row for `memory/`")
        self.assertTrue(len(mine[0]) > 1 and mine[0][1], "the `memory/` row has no description")
        self.assertNotIn("舊記憶卡", text)


if __name__ == "__main__":
    unittest.main()
