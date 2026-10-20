# GitHub Actions: critique and package audit

Two composite actions in this repository bring the honesty checks into a pull request (improvement plan E2).
They install QuaeraLabs from this repository; nothing in your repository is executed.

## Critique changed experiments

`tools/actions/critique` runs `quaera critique` on every experiment directory changed by the pull request and posts
the critique as a comment. Each directory directly below `paths` is one experiment (code, results, pre-registration,
report). The Critic uses the prompt measured by the critic test (`evals/critic_v2_run.py`); a `blocking` objection is
confirmed by a second model family when the models given include one.

```yaml
# .github/workflows/critique.yml
on: pull_request
permissions: {contents: read, pull-requests: write}
jobs:
  critique:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}          # the action compares the pull request with its base
      - uses: mikailustuner/quaeralabs/tools/actions/critique@main
        with:
          paths: experiments
          budget: "0.5"                 # USD per experiment directory, never exceeded
          fail-on: blocking             # optional: fail the check on a blocking objection
          models: '{"cheap": "anthropic/<model>", "best": "anthropic/<model>"}'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

The key comes from a repository secret and is passed only to the model provider. A pull request from a fork gets no
secrets, so the action cannot run there; use `pull_request_target` only if you understand its risks.

## Audit evidence packages

`tools/actions/audit` checks every evidence package in the repository with `quaera verify-package`: the ed25519
signature, the AI label, the hash of every file, the manifest schema and, unless `offline: "true"`, every citation in
the report against arXiv, Crossref and DataCite. It needs no model and no key.

```yaml
      - uses: mikailustuner/quaeralabs/tools/actions/audit@main
        with:
          glob: "**/*evidence-package*.zip"
```

## Status
Both actions are written and their commands are tested (`tests/test_improvement_phase56.py`); they have not yet run
in a real pull request. The browser version of the package check is `web/verify/` (GitHub Pages, `.github/workflows/pages.yml`).
