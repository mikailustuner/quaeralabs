# Lean project mode: `quaera prove`

Close the `sorry` of one theorem or lemma in your own Lean 4 project. There is no research question, hypothesis or report: just
the proof search, Lean's verdict and a diff.

```bash
quaera prove MyProject/Foo.lean --decl my_lemma --budget 1            # prints a diff; the file is not changed
quaera prove MyProject/Foo.lean --decl my_lemma --budget 1 --write    # shows the diff, asks, then writes
```

Build your project once first (`lake build`): the search imports your compiled modules.

## What it does
1. It finds the project root (the nearest directory with `lakefile.toml` or `lakefile.lean`) and the declaration.
2. It tries automation tactics without a model, then asks the model for whole proofs. It continues with step-by-step
   proving in the Lean REPL (short files), sketches with helper lemmas and further rounds while the budget lasts. This is the
   same search a research run uses.
3. Every candidate is compiled against your project inside the sandbox, which has no network and reads the project
   read-only. The candidate counts only if Lean accepts it, it has no `sorry`, and the declaration and everything it uses
   depend only on the standard axioms.
4. A found proof is built once more from scratch, together with the rest of the file. Only then is it shown.

## What the model can and cannot change
- Everything above the declaration is yours and stays byte for byte the same.
- From the model's answer, two things are taken: new helper lemmas, placed directly above your declaration, and your
  declaration with its proof. Its doc comment and attributes stay with it.
- The statement must stay exactly as you wrote it.
- In the part the model wrote, these are rejected: a new `axiom`, commands that print output or change syntax (`#eval`,
  `#print`, `macro`, `notation`, `elab`, …), and `sorry`, `admit` or `native_decide`. Your own file may use any of these.

## Speed
If your project uses the same Lean version as QuaeraLabs' pinned workspace (`lean/lean-toolchain`), checks run in the Lean
REPL: your imports are loaded once and each check takes about a second. With another Lean version every check is a one-shot
build of the file, which is slower but gives the same verdict.

## Budget
`--budget` is a hard cap in USD; `--max-calls`, `--max-hours` and `--max-tokens` bound subscription CLIs. `--max-output`
(default 6000 tokens) limits each call's output, which keeps the worst-case cost of a single call within small budgets. The
event log of each run is kept in `~/.quaera/prove/<name>-<hash>/quaera.db`.

## Limits of this version
- Long files: the model sees the imports, the scope commands (`namespace`, `open`, `variable`, …) and the last 150 lines
  before the declaration. Step-by-step REPL proving is off for such files.
- Verified lemmas from your project are not added to the lemma bank: they depend on your definitions.
- Tested end to end with real Lean on a project without Mathlib (`tests/test_prove_file.py`, marker `lean`). Projects that
  depend on Mathlib use the same code path but have not been measured yet (improvement plan C3, measurement).
