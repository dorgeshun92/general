-- Mpire Mortgage Operations Copilot — Supabase schema, v1
--
-- Stores DERIVED, MASKED audit outputs only: run metadata, findings, review items,
-- proposed actions, masked Markdown reports, evaluation summaries, and dashboard
-- decisions. It never stores source documents, credit reports, statements, or any
-- unmasked identifier. The sync script refuses files that fail schema validation or
-- the PII check before anything reaches this database.
--
-- Apply with the Supabase CLI (`supabase db push`) or paste into the SQL editor.
-- Row-Level Security is enabled on every table. Authenticated users can read;
-- dashboard writes are append-only decision/request rows stamped with auth.uid();
-- the sync script uses the service role key, which bypasses RLS server-side.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- enums
do $$ begin
  create type result_value as enum ('PASS','FAIL','MISSING','REVIEW','NOT_APPLICABLE');
exception when duplicate_object then null; end $$;
do $$ begin
  create type overall_status as enum ('READY','NOT_READY','HUMAN_REVIEW');
exception when duplicate_object then null; end $$;
do $$ begin
  create type audit_type as enum ('PREAPPROVAL','SUBMISSION_READINESS');
exception when duplicate_object then null; end $$;
do $$ begin
  create type reviewer_role as enum ('LOAN_OFFICER','PROCESSOR','UNDERWRITER','COMPLIANCE','MANAGEMENT');
exception when duplicate_object then null; end $$;
do $$ begin
  create type run_request_status as enum ('QUEUED','PICKED_UP','COMPLETED','REJECTED');
exception when duplicate_object then null; end $$;
do $$ begin
  create type review_decision_kind as enum ('CONFIRMED','OVERRIDDEN','NEEDS_INFO');
exception when duplicate_object then null; end $$;
do $$ begin
  create type action_decision_kind as enum ('ACCEPTED','REJECTED','DEFERRED');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------- core tables
create table if not exists loans (
  loan_id       text primary key check (loan_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$'),
  description   text,
  deidentified  boolean not null default true check (deidentified = true),
  source_root   text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists runs (
  loan_id             text not null references loans(loan_id) on delete cascade,
  run_id              text not null check (run_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$'),
  skill               text,
  started_at          timestamptz,
  completed_at        timestamptz,
  completed_normally  boolean,
  stop_condition      text,
  overall_status      overall_status,
  preapproval_present boolean not null default false,
  submission_present  boolean not null default false,
  los_export_present  boolean,
  catalog_version     text,
  catalog_reviewed    boolean,
  counts              jsonb,                     -- {"PASS":n,"FAIL":n,...} of the gate-defining audit
  blocking_open       integer,
  coverage_percent    numeric(6,2),
  known_limitations   jsonb not null default '[]'::jsonb,
  tool_versions       jsonb,
  manifest            jsonb,                     -- full run_manifest.json (paths + sha256, no content)
  totals              jsonb,                     -- {"files":n,"pages":n,"bytes":n} from document_inventory
  synced_at           timestamptz not null default now(),
  primary key (loan_id, run_id)
);
create index if not exists runs_loan_completed_idx on runs (loan_id, completed_at desc);
create index if not exists runs_status_idx on runs (overall_status);

create table if not exists documents (
  loan_id                   text not null,
  run_id                    text not null,
  document_id               text not null,
  filename                  text not null,
  relative_path             text,
  sha256                    text not null check (sha256 ~ '^[a-f0-9]{64}$'),
  size_bytes                bigint,
  document_type             text not null,
  classification_confidence text not null check (classification_confidence in ('HIGH','MEDIUM','LOW')),
  page_count                integer,
  status                    text not null,
  duplicate_of              text,
  document_date             date,
  primary key (loan_id, run_id, document_id),
  foreign key (loan_id, run_id) references runs(loan_id, run_id) on delete cascade
);

create table if not exists findings (
  loan_id          text not null,
  run_id           text not null,
  audit_type       audit_type not null,
  finding_id       text not null,
  rule_id          text not null,
  result           result_value not null,
  blocking         boolean not null,
  evidence_ids     jsonb not null default '[]'::jsonb,
  explanation      text not null,
  discrepancy      text,
  proposed_action  text,
  reviewer_role    reviewer_role,
  confidence       text not null check (confidence in ('HIGH','MEDIUM','LOW')),
  review_reason    text,
  calculation      jsonb,
  guideline_source jsonb,
  primary key (loan_id, run_id, audit_type, finding_id),
  foreign key (loan_id, run_id) references runs(loan_id, run_id) on delete cascade,
  -- mirror the schema invariants so nothing unevidenced can be stored even by the service role
  constraint pass_requires_evidence check (result <> 'PASS' or jsonb_array_length(evidence_ids) >= 1),
  constraint fail_missing_require_action check (result not in ('FAIL','MISSING') or coalesce(proposed_action,'') <> ''),
  constraint review_requires_reason check (result <> 'REVIEW' or (coalesce(review_reason,'') <> '' and reviewer_role is not null))
);
create index if not exists findings_run_idx on findings (loan_id, run_id);
create index if not exists findings_result_idx on findings (result, blocking);
create index if not exists findings_rule_idx on findings (rule_id);
create index if not exists findings_explanation_fts on findings using gin (to_tsvector('english', explanation));

create table if not exists review_items (
  loan_id       text not null,
  run_id        text not null,
  review_id     text not null,
  category      text not null,
  description   text not null,
  document_ids  jsonb not null default '[]'::jsonb,
  evidence_ids  jsonb not null default '[]'::jsonb,
  reviewer_role reviewer_role not null,
  primary key (loan_id, run_id, review_id),
  foreign key (loan_id, run_id) references runs(loan_id, run_id) on delete cascade
);

create table if not exists missing_documents (
  loan_id       text not null,
  run_id        text not null,
  audit_type    audit_type not null,
  seq           integer not null,
  document_type text not null,
  borrower_id   text,
  description   text not null,
  rule_ids      jsonb not null default '[]'::jsonb,
  primary key (loan_id, run_id, audit_type, seq),
  foreign key (loan_id, run_id) references runs(loan_id, run_id) on delete cascade
);

create table if not exists conflicts (
  loan_id     text not null,
  run_id      text not null,
  audit_type  audit_type not null,
  conflict_id text not null,
  field       text not null,
  "values"    jsonb not null,
  explanation text not null,
  rule_ids    jsonb not null default '[]'::jsonb,
  primary key (loan_id, run_id, audit_type, conflict_id),
  foreign key (loan_id, run_id) references runs(loan_id, run_id) on delete cascade
);

create table if not exists proposed_actions (
  loan_id       text not null,
  run_id        text not null,
  audit_type    audit_type not null,
  action_id     text not null,
  action_type   text not null,
  target        text not null,
  description   text not null,
  before_value  text,
  after_value   text,
  rule_ids      jsonb not null default '[]'::jsonb,
  evidence_ids  jsonb not null default '[]'::jsonb,
  approver_role reviewer_role not null,
  status        text not null default 'DRAFT_HUMAN_APPROVAL_REQUIRED' check (status = 'DRAFT_HUMAN_APPROVAL_REQUIRED'),
  primary key (loan_id, run_id, audit_type, action_id),
  foreign key (loan_id, run_id) references runs(loan_id, run_id) on delete cascade
);

create table if not exists approvals_required (
  loan_id       text not null,
  run_id        text not null,
  audit_type    audit_type not null,
  seq           integer not null,
  description   text not null,
  approver_role reviewer_role not null,
  rule_ids      jsonb not null default '[]'::jsonb,
  primary key (loan_id, run_id, audit_type, seq),
  foreign key (loan_id, run_id) references runs(loan_id, run_id) on delete cascade
);

create table if not exists reports (
  loan_id    text not null,
  run_id     text not null,
  name       text not null,                      -- e.g. report.md, preapproval_report.md
  content_md text not null,                      -- masked Markdown; the sync script re-checks for PII
  sha256     text not null check (sha256 ~ '^[a-f0-9]{64}$'),
  primary key (loan_id, run_id, name),
  foreign key (loan_id, run_id) references runs(loan_id, run_id) on delete cascade
);

-- ---------------------------------------------------------------- dashboard state (append-only)
create table if not exists run_requests (
  request_id   uuid primary key default gen_random_uuid(),
  loan_id      text not null,
  requested_by uuid not null default auth.uid(),
  requested_at timestamptz not null default now(),
  note         text,
  status       run_request_status not null default 'QUEUED',
  run_id       text,
  updated_at   timestamptz not null default now()
);
create index if not exists run_requests_status_idx on run_requests (status, requested_at);

create table if not exists review_decisions (
  decision_id uuid primary key default gen_random_uuid(),
  loan_id     text not null,
  run_id      text not null,
  audit_type  audit_type,                        -- null when the target is a review_item
  target_id   text not null,                     -- finding_id or review_id
  decision    review_decision_kind not null,
  note        text,
  decided_by  uuid not null default auth.uid(),
  decided_at  timestamptz not null default now()
);
create index if not exists review_decisions_target_idx on review_decisions (loan_id, run_id, target_id);

create table if not exists action_decisions (
  decision_id uuid primary key default gen_random_uuid(),
  loan_id     text not null,
  run_id      text not null,
  audit_type  audit_type not null,
  action_id   text not null,
  decision    action_decision_kind not null,
  note        text,
  decided_by  uuid not null default auth.uid(),
  decided_at  timestamptz not null default now()
);
create index if not exists action_decisions_target_idx on action_decisions (loan_id, run_id, action_id);

create table if not exists eval_reports (
  eval_id         uuid primary key default gen_random_uuid(),
  generated_at    timestamptz not null default now(),
  all_targets_met boolean not null,
  targets         jsonb not null,                -- the section-10 target table from eval_report.json
  report          jsonb not null                 -- full eval_report.json
);

-- ---------------------------------------------------------------- views
create or replace view v_latest_runs as
select distinct on (r.loan_id)
  r.loan_id, r.run_id, r.skill, r.completed_at, r.completed_normally, r.stop_condition,
  r.overall_status, r.blocking_open, r.coverage_percent, r.counts, r.catalog_reviewed,
  r.los_export_present, l.description
from runs r join loans l using (loan_id)
order by r.loan_id, r.completed_at desc nulls last, r.synced_at desc;

create or replace view v_review_queue as
select f.loan_id, f.run_id, f.audit_type::text as audit_type, f.finding_id as target_id, 'FINDING' as kind,
       f.rule_id, f.reviewer_role::text as reviewer_role, f.review_reason as reason, f.blocking, f.explanation
from findings f
where f.result = 'REVIEW'
  and not exists (select 1 from review_decisions d
                  where d.loan_id = f.loan_id and d.run_id = f.run_id and d.target_id = f.finding_id
                    and d.audit_type = f.audit_type)
union all
select ri.loan_id, ri.run_id, null, ri.review_id, 'REVIEW_ITEM',
       ri.category, ri.reviewer_role::text, ri.description, false, ri.description
from review_items ri
where not exists (select 1 from review_decisions d
                  where d.loan_id = ri.loan_id and d.run_id = ri.run_id and d.target_id = ri.review_id
                    and d.audit_type is null);

create or replace view v_dashboard_summary as
select
  (select count(*) from loans)                                              as loans,
  (select count(*) from runs)                                               as runs,
  (select count(*) from v_latest_runs where overall_status = 'READY')       as ready,
  (select count(*) from v_latest_runs where overall_status = 'NOT_READY')   as not_ready,
  (select count(*) from v_latest_runs where overall_status = 'HUMAN_REVIEW') as human_review,
  (select count(*) from v_latest_runs where overall_status is null)         as no_gate,
  (select coalesce(sum(blocking_open),0) from v_latest_runs)                as blocking_open,
  (select count(*) from v_review_queue)                                     as review_queue,
  (select count(*) from run_requests where status = 'QUEUED')               as queued_requests,
  (select count(*) from proposed_actions pa
     where not exists (select 1 from action_decisions ad
                       where ad.loan_id = pa.loan_id and ad.run_id = pa.run_id and ad.action_id = pa.action_id
                         and ad.audit_type = pa.audit_type))                as pending_actions;

-- ---------------------------------------------------------------- row-level security
alter table loans              enable row level security;
alter table runs               enable row level security;
alter table documents          enable row level security;
alter table findings           enable row level security;
alter table review_items       enable row level security;
alter table missing_documents  enable row level security;
alter table conflicts          enable row level security;
alter table proposed_actions   enable row level security;
alter table approvals_required enable row level security;
alter table reports            enable row level security;
alter table run_requests       enable row level security;
alter table review_decisions   enable row level security;
alter table action_decisions   enable row level security;
alter table eval_reports       enable row level security;

-- Every signed-in user may read. Anonymous (anon key without a session) reads nothing.
do $$
declare t text;
begin
  foreach t in array array['loans','runs','documents','findings','review_items','missing_documents',
                           'conflicts','proposed_actions','approvals_required','reports',
                           'run_requests','review_decisions','action_decisions','eval_reports']
  loop
    execute format('drop policy if exists %I on %I', t || '_read_authenticated', t);
    execute format('create policy %I on %I for select to authenticated using (true)', t || '_read_authenticated', t);
  end loop;
end $$;

-- Dashboard writes: append-only rows stamped with the caller's uid. Audit content is never
-- writable from the dashboard; only the service role (sync script) inserts runs and findings.
drop policy if exists run_requests_insert_own on run_requests;
create policy run_requests_insert_own on run_requests
  for insert to authenticated with check (requested_by = auth.uid());

drop policy if exists review_decisions_insert_own on review_decisions;
create policy review_decisions_insert_own on review_decisions
  for insert to authenticated with check (decided_by = auth.uid());

drop policy if exists action_decisions_insert_own on action_decisions;
create policy action_decisions_insert_own on action_decisions
  for insert to authenticated with check (decided_by = auth.uid());

-- Views run with the invoker's rights so RLS applies through them.
alter view v_latest_runs set (security_invoker = true);
alter view v_review_queue set (security_invoker = true);
alter view v_dashboard_summary set (security_invoker = true);

-- Full-text search over finding explanations (used by the API and MCP search tools).
create or replace function search_findings(q text, max_rows integer default 50)
returns setof findings
language sql stable security invoker as $$
  select * from findings
  where to_tsvector('english', explanation || ' ' || coalesce(discrepancy,'') || ' ' || coalesce(proposed_action,''))
        @@ plainto_tsquery('english', q)
     or rule_id ilike '%' || q || '%'
  limit greatest(1, least(max_rows, 500));
$$;
