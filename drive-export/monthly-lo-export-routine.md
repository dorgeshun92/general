# Monthly routine — ModelMatch LO export to Google Drive

Create this from the **claude.ai Routines UI**, not the CLI. CLI-created routines
on this org cannot carry connector grants, so the fired session would have no
ModelMatch and no Google Drive tools and would fail every run.

Settings:
- **Schedule:** monthly, 1st of the month (cron `23 13 1 * *` = 8:23am Eastern)
- **Connectors:** attach **modelmatch** and **Google Drive**
- **New session each run:** yes
- **Prompt:** everything below the line

## What it does

Each run adds the NEXT 1,000 loan officers by rank — never the same ones again.
Month 1 took the top 1,000 and recorded a volume floor; each later month pulls
the next 1,000 below that floor. Nothing is overwritten and no name repeats.
About 29 months of runway from the 28,846-record pool.

Cost: 1,000 credits per run, against a 2,000/month allowance.

## Drive layout (folder `1VheDLmnyt4U8zVi-8wKVlYqhk_AenxtB`)

| File | Role |
|---|---|
| `LO-Master-TX-CA-FL.csv` | Accumulating master, deduped by NMLS ID. Grows every month. |
| `LO-<YYYY>-<MM>-batch<NN>.csv` | That month's additions only. Kept forever. |
| `lo-export-state.json` | Resume state: last batch, next volume floor, delivered NMLS ids. |

---

You run the monthly loan-officer export for MPIRE Financial Group and deliver it to Google Drive. Work only in Drive folder id `1VheDLmnyt4U8zVi-8wKVlYqhk_AenxtB` ("ModelMatch LO Lists — TX, CA, FL").

STEP 1 — CHECK BUDGET FIRST
Call modelmatch `me` with view `credits`. This run costs 1,000 credits. If the available balance is under 1,100, STOP: post nothing to Drive, change no files, and report that the run was skipped for low credits and what the balance was. Never run a partial export.

STEP 2 — READ RESUME STATE
Read `lo-export-state.json` (file id `1dS6HIXZb0ADbtF39XyD_3Gjoh8iHX8Kg`) from that folder. Take `next_volume_floor`, `last_batch` and `delivered_nmls_ids`. If that file cannot be read, STOP and report it — do NOT start over from the top of the ranking, which would re-deliver people already in the master and waste 1,000 credits.

STEP 3 — SUBMIT THE EXPORT
Call modelmatch `submitBulkDelivery` with exactly:
- `entityType`: "originators"
- `format`: "csv"
- `limit`: 1000
- `period`: "last12Months"
- `sort`: [{"field": "volume", "order": "desc"}]
- `flatFilters`: {"state": ["TX","CA","FL"], "companyCategory": "OTHER", "units": {"gte": 12}, "avgLoanAmount": {"lte": 1500000}, "hasPhone": true, "volume": {"lt": <next_volume_floor from state>}}
- `columns`: copy the column list from the previous batch file's header, in the same order (Loan Officer, NMLS ID, Company, Company NMLS, Office City, Office State, Top Production City, Top Production State, Volume in TX/CA/FL, Loans in TX/CA/FL, Total Volume, Total Loans, Avg Loan, Purchase %, Refi %, Conventional %, FHA %, VA %, Years in Industry, Months at Company, Licensed Since, Last Loan, Role, Phone, Email, LinkedIn)

Do NOT use `advancedFilters`. It silently disables the in-state scoping, so `scopedVolume` comes back equal to national volume and the "Volume in TX/CA/FL" column becomes wrong. Geography must go in `flatFilters`.

STEP 4 — WAIT FOR THE JOB
Poll `getJob` with jobType "originators" until status is `completed`. If it fails, report the error and change nothing in Drive.

STEP 5 — DOWNLOAD
Download the file from the job's `resultsUrl` (the S3 presigned link). Do NOT use `downloadUrl` — that host (api.modelmatch.com) is blocked by the sandbox egress proxy and will fail with a 403 CONNECT.

STEP 6 — CLEAN AND APPEND
- Drop every row whose `Company NMLS` is `2108504` (MPIRE Financial Group — Jay's own shop).
- Drop any row whose `NMLS ID` already appears in `delivered_nmls_ids`. There should be none; if there are, say so in the report.
- Add two leading columns to each kept row: `Batch` = last_batch + 1, `First Added` = today's date (YYYY-MM-DD).
- Write `LO-<YYYY>-<MM>-batch<NN>.csv` as a NEW file in the folder — never overwrite an existing batch file.
- Download the current `LO-Master-TX-CA-FL.csv`, append the kept rows to the end, and update that file in place. Keep the header row exactly once. Never reorder or rewrite existing rows.
- Write CSVs as UTF-8 with a BOM so Excel opens accented names correctly.

STEP 7 — UPDATE STATE
Update `lo-export-state.json` with: `last_batch` incremented, `last_run_date` = today, `next_volume_floor` = the LOWEST `Total Volume` among the rows just delivered, `rows_delivered_total` and `credits_spent_total` increased, and `delivered_nmls_ids` extended with the new ids. Getting this wrong breaks next month, so write it before reporting success.

STEP 8 — REPORT
One short summary: batch number, rows added, new master total, credits charged, the new volume floor, and how many of the pool remain (28,846 minus master total). If anything was skipped or dropped, say what and why.

RULES
- Never overwrite or delete an existing batch file, and never rewrite rows already in the master. This list only ever grows.
- Never exceed `limit` 1000 in one run, and never run twice in a month.
- If the pool is exhausted (the export returns far fewer than 1,000 rows), say so and recommend widening the filters rather than silently delivering a short file.
- Every figure you report must come from the job result or the files. Never estimate a count.
