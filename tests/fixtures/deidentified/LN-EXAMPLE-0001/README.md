# LN-EXAMPLE-0001 — harness self-test fixture

This directory exists so the evaluation harness (`scripts/eval/run_eval.py`)
and its unit tests have a fixture/answer-key pair to exercise. It is NOT a
de-identified loan file: it holds no PDFs and no borrower facts.

- `MANIFEST.yaml` carries `harness_self_test: true`; `run_eval.py` skips it
  during real evaluations unless `--include-self-test` is passed.
- The answer key at `tests/expected/LN-EXAMPLE-0001/` uses placeholder rule
  ids (`SUB-EXAMPLE-*`) and placeholder evidence ids; it is not a checklist.
- Running `/mortgage-file-audit` against this directory is not meaningful.
