# tests/fixtures/deidentified/

One directory per de-identified historical loan file, named by loan id
(e.g. `LN-TEST-0001/`). Every directory must contain a `MANIFEST.yaml`
declaring `deidentified: true`, who de-identified it, and when.

The intake and orchestrator skills refuse to run on any directory outside
this tree during the read-only MVP.

No live borrower PII, ever. Synthetic names, masked accounts, altered dates.
