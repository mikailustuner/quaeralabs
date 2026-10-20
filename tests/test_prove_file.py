"""Lean project mode, `quaera prove` (improvement plan C3).

Most tests use a fake Lean. `test_real_project_end_to_end` (marker: lean) builds a small Lean project without Mathlib,
so it runs in seconds wherever the pinned Lean toolchain is installed."""

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quaera.lean import DEFAULT_WORKSPACE, statement_of
from quaera.prove_file import (ProveError, load_target, namespace_of, prove_file, qualify, rebuild, splice, view_of)

MAIN = """import Demo.Basic

namespace Demo

#eval double 3

/-- `double` is twice its argument. -/
@[simp] theorem double_eq (n : Nat) : double n = 2 * n := by
  sorry

theorem after (n : Nat) : double n + 0 = double n := by
  simp

end Demo
"""
PROOF = """import Demo.Basic
namespace Demo
theorem after (n : Nat) : double n + 0 = double n := by
  rfl
theorem helper_two (n : Nat) : n + n = 2 * n := by omega
/-- `double` is twice its argument. -/
@[simp] theorem double_eq (n : Nat) : double n = 2 * n := by
  unfold double; exact helper_two n
end Demo
"""


def project(tmp_path, main=MAIN) -> Path:
    root = tmp_path / "demo"
    (root / "Demo").mkdir(parents=True)
    (root / "lakefile.toml").write_text('name = "demo"\ndefaultTargets = ["Demo"]\n\n[[lean_lib]]\nname = "Demo"\n')
    (root / "Demo" / "Basic.lean").write_text("def double (n : Nat) : Nat := n + n\n")
    (root / "Demo.lean").write_text("import Demo.Basic\n")
    (root / "Demo" / "Main.lean").write_text(main)
    return root


def test_target_keeps_doc_comment_and_attributes_with_the_declaration(tmp_path):
    t = load_target(project(tmp_path) / "Demo" / "Main.lean", "double_eq")
    assert t.prefix.endswith("#eval double 3\n\n") and t.decl.startswith("/-- `double` is twice")
    assert t.suffix.startswith("\ntheorem after") and t.statement == statement_of(MAIN, "double_eq")
    assert t.project == (tmp_path / "demo").resolve()


def test_target_errors(tmp_path):
    root = project(tmp_path)
    with pytest.raises(ProveError, match="no `theorem missing`"):
        load_target(root / "Demo" / "Main.lean", "missing")
    with pytest.raises(ProveError, match="has no `sorry`"):
        load_target(root / "Demo" / "Main.lean", "after")
    (tmp_path / "lone.lean").write_text("theorem x : True := by sorry\n")
    with pytest.raises(ProveError, match="not inside a Lean project"):
        load_target(tmp_path / "lone.lean", "x")


def test_rebuild_takes_only_new_helpers_and_the_declaration(tmp_path):
    t = load_target(project(tmp_path) / "Demo" / "Main.lean", "double_eq")
    full = rebuild(PROOF, t)
    assert full.startswith(t.prefix)                         # the user's part is byte for byte the same
    rest = full[len(t.prefix):]
    assert rest.startswith("theorem helper_two") and "@[simp] theorem double_eq" in rest
    assert "rfl" not in rest and "end Demo" not in rest      # the model's copy of `after` and other text are dropped
    assert rebuild("theorem unrelated : True := trivial", t) is None
    new = splice(full, t)
    assert new.endswith(t.suffix) and new.count("theorem after") == 1


def test_namespaces_qualify_the_axiom_check():
    assert namespace_of("namespace A\nsection\nnamespace B.C\nend C\n") == ["A"]
    assert qualify("x", "namespace A\nnamespace B\n") == "A.B.x" and qualify("_root_.x", "namespace A\n") == "x"


def test_long_files_are_shortened_for_the_model(tmp_path):
    filler = "".join(f"theorem t{i} : {i} = {i} := rfl\n" for i in range(800))
    t = load_target(project(tmp_path, MAIN.replace("#eval double 3\n", filler)) / "Demo" / "Main.lean", "double_eq")
    view, short = view_of(t)
    assert short and len(view) < len(t.prefix) and "earlier lines of Main.lean omitted" in view
    assert view.startswith("import Demo.Basic\nnamespace Demo") and view.endswith(t.decl)


@dataclass
class Report:
    verified: bool
    compiled: bool = True
    errors: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    axioms: list = field(default_factory=list)
    output: str = ""


class FakeLean:
    """Accepts a file iff the declaration's proof mentions `helper_two` and no `sorry`; records every call."""
    repl_bin = None

    def __init__(self):
        self.calls = []

    def check(self, source, theorem, approved, clean=False, trusted_prefix=None, qualified=None):
        self.calls.append({"theorem": theorem, "qualified": qualified, "prefix_ok": source.startswith(trusted_prefix or ""),
                           "clean": clean})
        ok = "exact helper_two" in source and "sorry" not in source.split("namespace Demo", 1)[1].split("theorem after")[0]
        return Report(ok)

    def goals(self, source):
        return []

    def tactic(self, state, text):
        return {"proofState": None, "goals": [], "error": "no REPL"}


def test_prove_file_with_a_scripted_model(tmp_path):
    t = load_target(project(tmp_path) / "Demo" / "Main.lean", "double_eq")
    lean, asked = FakeLean(), []

    def ask(system, prompt, n, lane=None):
        asked.append(prompt)
        return "```lean\n" + (PROOF if len(asked) > 1 else PROOF.replace("exact helper_two n", "omega")) + "```"

    out = prove_file(t, lean, ask, rounds=1)
    assert out.solved and out.model_calls == 2
    assert "+theorem helper_two" in out.diff and "-  sorry" in out.diff and out.new_text.endswith(t.suffix)
    assert all(c["prefix_ok"] for c in lean.calls) and {c["qualified"] for c in lean.calls} == {"Demo.double_eq"}
    assert lean.calls[-1]["clean"] and lean.calls[-2]["clean"]           # final one-shot builds
    assert "user's Lean project demo" in asked[0] and "helper lemmas directly above `double_eq`" in asked[0]


def test_prove_file_reports_no_proof(tmp_path):
    t = load_target(project(tmp_path) / "Demo" / "Main.lean", "double_eq")
    out = prove_file(t, FakeLean(), lambda s, p, n, lane=None: "```lean\nimport Demo.Basic\n```", rounds=1)
    assert not out.solved and out.new_text is None and "no proof" in out.stopped


# --- real Lean ------------------------------------------------------------------------------------------------------

LAKE = shutil.which("lake") or str(Path.home() / ".elan" / "bin" / "lake")


@pytest.mark.lean
@pytest.mark.skipif(not Path(LAKE).exists() or not (DEFAULT_WORKSPACE / "lean-toolchain").exists(), reason="no Lean toolchain")
def test_real_project_end_to_end(tmp_path, monkeypatch, capsys):
    """`quaera prove` against a real Lean project, with a scripted model; the file changes only with --write --yes."""
    import quaera.cli as cli
    from quaera.gateway import ScriptedProvider

    root = project(tmp_path)
    shutil.copy(DEFAULT_WORKSPACE / "lean-toolchain", root / "lean-toolchain")
    subprocess.run([LAKE, "build"], cwd=root, check=True, capture_output=True, timeout=600)
    main = root / "Demo" / "Main.lean"
    monkeypatch.setenv("QUAERA_HOME", str(tmp_path / "home"))
    replies = iter([PROOF.replace("exact helper_two n", "exact bogus_lemma n"), PROOF] + [PROOF] * 20)
    monkeypatch.setattr(cli, "make_providers", lambda families=None: {"scripted": ScriptedProvider(lambda s, p: f"```lean\n{next(replies)}```")})

    assert cli.main(["prove", str(main), "--decl", "double_eq", "--budget", "1", "--quiet"]) == 0
    assert main.read_text() == MAIN                                   # without --write nothing changes
    out = capsys.readouterr().out
    assert "+theorem helper_two" in out and "clean build of the whole file" in out
    runs = list((tmp_path / "home" / "prove").iterdir())
    import sqlite3
    events = {r[0] for r in sqlite3.connect(runs[0] / "quaera.db").execute("select kind from events")}
    assert {"lean.output", "model.call", "prove.done"} <= events

    replies = iter([PROOF] * 20)
    assert cli.main(["prove", str(main), "--decl", "double_eq", "--budget", "1", "--quiet", "--write", "--yes"]) == 0
    new = main.read_text()
    assert "exact helper_two n" in new and "sorry" not in new and new.startswith(MAIN.split("/-- `double`")[0])
    built = subprocess.run([LAKE, "env", "lean", str(main)], cwd=root, capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stdout + built.stderr     # Lean itself accepts the written file
