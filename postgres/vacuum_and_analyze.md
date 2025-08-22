# PostgreSQL Autovacuum – Deep Operational Guide

## 1. Definition (What Autovacuum Really Is)
Autovacuum is a background subsystem that schedules and runs two maintenance tasks on tables:
- VACUUM (heap & index cleanup + freezing)
- ANALYZE (statistics refresh)
Triggered *independently* per table, based on activity counters and transaction ID (XID) age.
It is PostgreSQL's garbage collector + statistics caretaker + wraparound safety net.

Core goals:
1. Reclaim & recycle dead tuples (not shrink file, but make space usable again)
2. Prevent transaction ID wraparound (by freezing old tuples)
3. Maintain planner statistics (ANALYZE)
4. Maintain visibility map bits (all-visible / all-frozen) enabling index‑only scans & lighter future vacuum passes
5. Remove dead index entries (via index vacuum phase)

---
## 2. Why You Need It (Failure Modes if You Don’t)
| Layer | If Autovacuum Starved / Off | Consequence |
|-------|-----------------------------|-------------|
| Storage reuse | Dead tuples accumulate | Table / index bloat, higher I/O, cache dilution |
| Planner | Stats become stale | Bad cardinality estimates, wrong join orders, latency spikes |
| Visibility map | Pages not marked all-visible | Fewer index‑only scans; future VACUUM must rescan pages |
| XID aging | relfrozenxid not advanced | Forced aggressive vacuums → potential cluster read‑only lockdown |
| Replication / logical decoding | Old XIDs pinned | Retained bloat & WAL retention (slots cannot advance) |
| Crash recovery | More dirty / dead churn | Longer recovery times |

Wraparound is existential: ignoring it long enough forces PostgreSQL to block writes to prevent data corruption.

---
## 3. Core Concepts Refresher
- MVCC: UPDATE/DELETE never overwrite in-place; old versions become dead once no future snapshot can see them.
- XIDs: 32-bit; freezing rewrites old XIDs to a special permanent ID so wraparound math stays valid.
- Dead tuple lifecycle: live → recently dead → dead (globally invisible) → pruned/vacuumed → space reusable.
- FSM (Free Space Map): Only populated when VACUUM (or opportunistic pruning) reports free space.
- Visibility Map: Bit per page (all-visible / all-frozen) enabling short-circuit scans & selective freezing.

### 3.1 Transaction ID Wraparound (Beginner Friendly)
Think of a transaction ID (XID) as a car odometer with only 10 digits: after it reaches its max, it rolls over to 0000000000 and keeps counting. PostgreSQL XIDs are 32‑bit numbers (~4.29 billion range). After consuming ~2 billion more than an old row’s XID, that old XID can appear to be “in the future” if not handled — breaking visibility rules. To avoid that, PostgreSQL periodically “freezes” very old row versions so they are treated as infinitely old and safe forever.

Key plain-language points:
- Every write transaction gets a number (XID); rows store creator (xmin) and sometimes deleter (xmax) XIDs.
- Visibility = comparisons between your snapshot (ranges of in-progress XIDs) and the row’s XIDs.
- Because numbers wrap, really old unfrozen XIDs would compare incorrectly after the counter cycles.
- Freezing replaces old XID values in tuple headers with a special FrozenXID marker; frozen rows no longer participate in wraparound math.
- Autovacuum steadily freezes old tuples so you never hit crisis time.

What “wraparound danger” looks like:
1. Cluster keeps doing write transactions; XID counter advances.
2. Some large rarely-touched table still has very old xmin values.
3. `age(relfrozenxid)` for that table approaches the `autovacuum_freeze_max_age` limit (default 200M).
4. PostgreSQL forces aggressive vacuums; if still ignored and the global age nears ~2B, the system protects data by refusing new writes (read‑only enforcement) until you vacuum.

Simple analogy:
- Not freezing is like never archiving old invoices; eventually your numbering wraps and invoice #123 collides with a new #123 — chaos. Freezing = stamping old invoices “archived” so future comparisons don’t treat them as active documents.

---
## 4. How Autovacuum Decides to Run
Two independent threshold formulas per table (counters live in stats subsystem):
```
VACUUM triggers when: n_dead_tup >= vacuum_threshold + vacuum_scale_factor * reltuples
ANALYZE triggers when: n_mod_since_analyze >= analyze_threshold + analyze_scale_factor * reltuples
```
Defaults (often):
```
vacuum_threshold = 50
vacuum_scale_factor = 0.2  (20%)
analyze_threshold = 50
analyze_scale_factor = 0.1 (10%)
```
Large tables rarely hit those (20% of 100M = 20M dead tuples!) → you *must* lower scale factors for high-churn large relations.

Wraparound / aggressive vacuum trigger:
- If age(relfrozenxid) approaches autovacuum_freeze_max_age (default 200M) → force vacuum ignoring cost delay.
- Emergency at ~95% of that age; beyond ~2B cluster forced read‑only.

---
## 5. Autovacuum Architecture & Phases
1. Launcher wakes every `autovacuum_naptime` (default 1 min). Picks candidate tables ordered by urgency (wraparound > dead tuples > analyze need).
2. Spawns workers (up to `autovacuum_max_workers`).
3. Worker executes `VACUUM [ (options) ]` and/or `ANALYZE`:
   - Heap scan (may be abbreviated if visibility map says all-visible & not aggressive)
   - Dead tuple identification & index cleanup
   - Heap pruning / line pointer cleanup
   - Free space reporting (FSM)
   - Freeze eligible tuples (set all-frozen bit if page fully frozen)
   - Optionally ANALYZE: sample rows, compute per-column stats + extended stats
4. Cost-based delay: worker accumulates cost for page hits/misses, index scans, etc. Pauses according to `autovacuum_vacuum_cost_delay` until cost bucket drains—unless an aggressive wraparound vacuum (delay disabled).


---
## 6. Essential Configuration Parameters
| Parameter | Purpose | Typical Tuning Direction |
|-----------|---------|--------------------------|
| autovacuum | Must be on (default) | Never off in prod |
| autovacuum_max_workers | Parallelism of maintenance | Increase for many large active tables (5–10) |
| autovacuum_naptime | Launcher wake interval | Keep 30–60s; rarely change |
| autovacuum_vacuum_scale_factor | Dead tuple fraction | Lower (0.01–0.05) for large high-churn tables |
| autovacuum_vacuum_threshold | Fixed floor | Raise slightly (e.g. 200–1000) to avoid tiny tables churn |
| autovacuum_analyze_scale_factor | Stats refresh fraction | 0.02–0.1 typical for large OLTP |
| autovacuum_freeze_max_age | Wraparound cap | Leave default; monitor age(relfrozenxid) |
| autovacuum_multixact_freeze_max_age | For multixacts | Use default unless heavy row‑locking patterns |
| log_autovacuum_min_duration | Logging threshold | Set (e.g. 5s or 0) for visibility |
| maintenance_work_mem | Vacuum index cleanup memory | Increase (256MB+ for large indexes) |
| parallel_leader_participation | Indirect effect | Not vacuum, but relevant for ANALYZE parallelism |

Per-table overrides via:
```sql
ALTER TABLE big_hot_table SET (
  autovacuum_enabled = true,
  autovacuum_vacuum_scale_factor = 0.01,
  autovacuum_vacuum_threshold = 1000,
  autovacuum_analyze_scale_factor = 0.02,
  autovacuum_analyze_threshold = 500,
  autovacuum_vacuum_cost_limit = 3000,
  autovacuum_vacuum_cost_delay = 5
);
```

---
## 7. Tuning Playbooks (Scenario-Based)
### A. Large, Update-Heavy OLTP Table (100M rows, sustained churn)
Problems: Bloat, high dead tuples between runs.
Actions:
- Lower vacuum_scale_factor to 0.01 (or even 0.005)
- Keep threshold ~500–2000 to avoid overhead on tiny bursts
- Raise autovacuum_max_workers cluster-wide (e.g., 8)
- Raise maintenance_work_mem for better index cleanup
- Monitor `n_dead_tup` vs. threshold to validate

### B. Many Small Tables (Microservices style)
Problems: Too many pointless autovacuums.
Actions:
- Slightly raise thresholds (200) to batch work
- Leave scale factor moderate (0.1) because row counts small
- Ensure `log_autovacuum_min_duration` helps confirm not overdoing

### C. ETL Batch Table (Bulk nightly load + truncate/reload)
Use TRUNCATE (fast resets stats) then ANALYZE after load. May disable autovacuum on staging table if you rewrite fully:
```sql
ALTER TABLE staging_raw SET (autovacuum_enabled = false);
-- After load
ANALYZE staging_raw;
```
(Keep enabled if incremental updates occur.)

### D. Near Wraparound Alert
Symptoms: log line about “database is not accepting commands to avoid wraparound data loss”.
Immediate actions:
1. Identify largest ages:
```sql
SELECT relname, age(relfrozenxid) AS xid_age
FROM pg_class
WHERE relkind = 'r'
ORDER BY xid_age DESC LIMIT 20;
```
2. Run manual aggressive vacuums in priority order:
```sql
VACUUM (FREEZE, VERBOSE) schema.table;
```
3. Investigate long-lived transactions blocking cleanup.

### E. Logical Replication Slot Retention
High dead tuples because `xmin` horizon pinned.
- Check replication slots:
```sql
SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
FROM pg_replication_slots;
```

---
## 8. Hands-On Lab: Practical Autovacuum Tutorial (Docker)
Goal: Watch dead tuples form, trigger autovacuum, observe cleanup & stats refresh, tweak thresholds.
Time: 15–30 minutes.

### 8.1 Start Ephemeral Instance (Aggressive Settings for Fast Demo)
```bash
docker run -d --name pgvac \
  -e POSTGRES_PASSWORD=postgres \
  -p 5433:5432 \
  postgres:16 \
  -c autovacuum_vacuum_scale_factor=0.01 \
  -c autovacuum_analyze_scale_factor=0.02 \
  -c autovacuum_vacuum_threshold=50 \
  -c autovacuum_analyze_threshold=50 \
  -c log_autovacuum_min_duration=0 \
  -c shared_buffers=256MB \
  -c maintenance_work_mem=256MB
```
Tail autovacuum activity:
```bash
docker logs -f pgvac | grep -i autovacuum &
```

### 8.2 Connect & Baseline
```bash
docker exec -it pgvac psql -U postgres
```
```sql
CREATE DATABASE vaclab;
\c vaclab
\timing on
SELECT relname, n_dead_tup, last_autovacuum, last_analyze FROM pg_stat_user_tables;
```

### 8.3 Create Two Tables (One With Autovacuum Disabled)
```sql
CREATE TABLE churn (
  id bigserial PRIMARY KEY,
  payload text,
  updated_at timestamptz default now()
) WITH (fillfactor=90);

CREATE TABLE churn_no_av (
  id bigserial PRIMARY KEY,
  payload text,
  updated_at timestamptz default now()
) WITH (fillfactor=90, autovacuum_enabled = false);

ANALYZE churn; ANALYZE churn_no_av;  -- establish initial stats
```

### 8.4 Seed Rows & Generate Dead Tuples (UPDATE Pattern)
```sql
INSERT INTO churn(payload) SELECT repeat('x',100) FROM generate_series(1,20000);
INSERT INTO churn_no_av(payload) SELECT repeat('x',100) FROM generate_series(1,20000);

DO $$
BEGIN
  FOR i IN 1..5 LOOP
    UPDATE churn SET payload = repeat('a',100) || i::text WHERE id % 2 = 0;      -- half rows updated
    UPDATE churn_no_av SET payload = repeat('b',100) || i::text WHERE id % 2 = 0; -- same pattern
  END LOOP;
END$$;
```
Check accumulation:
```sql
SELECT relname, n_dead_tup, last_autovacuum FROM pg_stat_user_tables WHERE relname LIKE 'churn%';
```

Output:
```
vaclab=# SELECT relname, n_dead_tup, last_autovacuum FROM pg_stat_user_tables WHERE relname LIKE 'churn%';
   relname   | n_dead_tup |        last_autovacuum
-------------+------------+-------------------------------
 churn       |      50000 | 2025-08-22 17:12:45.506853+00
 churn_no_av |      50000 |
(2 rows)

Time: 1.551 ms
```
- churn: Has 50,000 dead tuples right now. It did have an autovacuum at 17:12:45 UTC, but since then your UPDATE/DELETE activity created another 50k dead tuples. It hasn’t crossed its next autovacuum trigger yet (or the launcher hasn’t scheduled it yet).
- churn_no_av: Also shows 50,000 dead tuples, but last_autovacuum is NULL because autovacuum is disabled for that table (you set autovacuum_enabled = false). So dead tuples will just keep accumulating until you VACUUM manually.

### 8.5 Observe Autovacuum Firing
Wait ~1 minute (launcher wake). Re-run the query above; `churn` should show `last_autovacuum` changing while `churn_no_av` keeps rising. Confirm in logs (outside psql) for "automatic vacuum of table ... churn" lines.

### 8.6 Manual VACUUM on Disabled Table
```sql
VACUUM (VERBOSE, ANALYZE) churn_no_av;
SELECT relname, n_dead_tup, last_vacuum, last_analyze FROM pg_stat_user_tables WHERE relname LIKE 'churn%';
```

### 8.7 Cleanup
Exit psql then remove container:
```bash
\q
docker rm -f pgvac
```

## 9. ANALYZE
ANALYZE collects / refreshes planner statistics. It samples table pages (not usually a full scan) and writes estimates into system catalogs (`pg_statistic`, extended stats tables, and updates `pg_class.reltuples`). These stats drive the optimizer’s row count & selectivity estimates (choice of index vs seq scan, join order, memory sizing). It does NOT reclaim space, remove dead tuples, or freeze XIDs—that’s VACUUM. Run it manually after bulk loads, large distribution shifts, creating indexes or extended stats you want used immediately, or after maintenance windows where autovacuum was disabled.

Minimal usage:
```sql
ANALYZE;                 -- whole database
ANALYZE mytable;         -- one table
ANALYZE mytable (col1);  -- specific column(s)
VACUUM (ANALYZE) mytable;-- space cleanup + stats refresh together
```
If plans look wrong right after large data changes, ANALYZE is the first, cheapest corrective action.

