"""Deterministic loan-file-intake tooling (read-only MVP 1).

Modules:
    manifest      - MANIFEST.yaml + approved-location checks, live-PII heuristic
    pdf_pages     - page counts and readability status per file (never writes)
    extract_text  - per-page text extraction with masking and missing-page flags
    classify      - transparent keyword classifier and low-confidence field heuristics
    inventory     - orchestrating CLI that writes document_inventory.json / loan_file.json
"""
