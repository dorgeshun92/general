# LN-EDGE-ENCRYPTED — designed edge case (stop-condition form)

This answer key is `{"expected_stop_condition": "ENCRYPTED_DOCUMENT"}` rather
than an `audit_result`. It means the correct outcome for the fixture
`tests/fixtures/deidentified/LN-EDGE-ENCRYPTED/` (created by the intake work)
is that NO completed audit is produced and `run_manifest.json` in the run
directory carries the stop reason `ENCRYPTED_DOCUMENT`. The harness scores
this under "Correct stop/escalation behavior".
