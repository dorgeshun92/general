# docs/source-checklists/

Place COPIES of the three authoritative source checklists here:

1. Mako Mortgage Processing Pre-approval Checklist
2. Mako Mortgage Processing Loan Partner Checklist for Submission to Processing
3. Submission to Processing Checklist

Rules:
- Work only with copies. The original PDF remains the authoritative source.
- A text extraction may be stored alongside each PDF for traceability
  (`<name>.extracted.txt`), but the PDF wins on any disagreement.
- These PDFs contain no borrower data, so they are allowed in Git
  (see `.gitignore` exceptions). Anything with borrower data is not.

The PDFs were not available when this repository was scaffolded, so
`config/checklist_catalog.yaml` is an empty template. Run the
checklist-normalization procedure in `docs/checklist-normalization.md`
once the PDFs are in place.
