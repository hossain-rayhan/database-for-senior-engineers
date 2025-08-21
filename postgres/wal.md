## PostgreSQL Write-Ahead Logging (WAL) – Focused Practical Guide

Write-Ahead Logging (WAL) = the sequential log PostgreSQL writes BEFORE modifying data files so it can guarantee crash recovery and support replication. This version trims deep internals and keeps what you actually use day‑to‑day.

Audience: Engineers who want to understand, observe, and reason about WAL without source‑level detail.

### 1. Why WAL Exists (Durability, Atomicity, Replication)
PostgreSQL guarantees that once a transaction is reported COMMIT, its effects survive crashes. It achieves this via Write-Ahead Logging (WAL): every change to persistent data is logged sequentially *before* the corresponding data pages are written to the main data files (heap, indexes, catalogs). Crash recovery replays WAL to bring data files to a consistent state.

Key design goals:
- Sequential I/O path for low latency (fsync fewer large contiguous writes vs scattered random page writes)
- Fast crash recovery (re-do idempotent logical/physical operations)
- Physical replication & Point-In-Time Recovery (PITR)
- Foundation for logical decoding / change streams

### 2. High-Level Modification Path
1. Client issues DML (e.g., INSERT).
2. Parser / planner / executor produces and applies row modifications in shared buffers.
3. WAL record(s) describing each change are built in backend local memory.
4. WAL records are appended into WAL buffers (shared memory ring).
5. On transaction commit: a commit record is generated; WAL up to the commit LSN is flushed (fsync) to durable storage. Only then does the backend ACK COMMIT to client.
6. Background writer / checkpointer later flush dirty data pages; they may lag commits because recovery can redo from WAL to reconstruct pages.

### 3. WAL File Structure & Naming
WAL lives under `pg_wal/` (pre-10 versions: `pg_xlog/`). WAL is segmented into fixed-size files (default 16MB; build-time configurable by `--with-wal-segsize` and visible via `SHOW wal_segment_size;`). Each segment name encodes:

`<TIMELINE 8 hex><LOG 8 hex><SEG 8 hex>`

Example: `00000001000000000000002B`
- Timeline: `00000001`
- Logical Log (high bits of LSN): `00000000`
- Segment (low bits of upper 32 bits): `0000002B`

Segments are recycled: once no longer needed for crash recovery, replication, or archiving, they are renamed and reused rather than deleted/created (reducing fragmentation & inode churn).

Segment size note: `wal_segment_size` (often 16MB) is fixed when the cluster is initialized (set at build or `initdb`). You cannot change it later without recreating the cluster. Larger segments = fewer files & switches (larger archive units). Smaller segments = more frequent switches (useful for streaming small pieces) but slightly more overhead. It does NOT change total WAL volume generated—only packaging.

### 4. LSN (Log Sequence Number)
An LSN is a 64-bit pointer into the WAL stream, formatted as `X/Y` where X and Y are 32-bit hex values (upper/lower). It is monotonically increasing.

Common functions:
- `pg_current_wal_lsn()` – current insert position
- `pg_last_wal_replay_lsn()` – on a standby, last replayed
- `pg_walfile_name(lsn)` – convert LSN to WAL filename
- `pg_wal_lsn_diff(a,b)` – byte difference

Internally, WAL is a byte-serialized sequence of variable-size records; the LSN of a record is its starting offset.

### 5. (Trimmed) What’s Inside a WAL Record
Practical takeaway: WAL records are small physical change descriptions; sometimes they include a Full Page Image (FPI) = entire 8KB page copied once after a checkpoint for safety. FPIs are a common reason for “unexpected” WAL growth.

### 6. Checkpoints & Full Page Images (FPIs)
Checkpoint = snapshot marker + dirty pages flushed. First change to a page AFTER a checkpoint logs an FPI (if `full_page_writes=on`). Fewer checkpoints (larger `max_wal_size`) ⇒ fewer FPIs ⇒ less WAL volume.

### 7. Crash Recovery (One-Line View)
On restart PostgreSQL replays WAL from the last checkpoint to the end; done.

### 8. WAL File Retention (Practical)
PostgreSQL recycles old segments. They stick around longer if: replicas/slots need them, archiving not finished, or you forced a large `wal_keep_size`. Typical growth problems: an unused replication slot or failed archive command.

### 9. Core Settings to Know First
| Setting | Plain Reason |
|---------|--------------|
| max_wal_size | Bigger ⇒ fewer checkpoints (fewer FPIs) |
| min_wal_size | Keep some segments to avoid churn |
| full_page_writes | Leave ON for safety |
| wal_compression | Shrinks FPIs if WAL volume high |
| wal_level | Use `replica` (or `logical` only if you need logical decoding) |
| synchronous_commit | Latency vs durability trade (leave `on` unless you accept risk) |

### 10. WAL & MVCC (Multi-Version Concurrency Control)
WAL logs changes. MVCC decides visibility using row metadata. Vacuum cleans old versions later. That’s usually all you need.

### 11. Logical Decoding vs Physical WAL
- Physical replication replays byte-level changes (block modifications) – exact binary copy.
- Logical decoding reads WAL and reconstructs row-level changes (INSERT/UPDATE/DELETE) ignoring page layout specifics. Enabled by `wal_level=logical` and consumption via replication slots.

### 12. Observability – Core Views & Functions
- `pg_current_wal_lsn()` / `pg_last_wal_receive_lsn()` / `pg_last_wal_replay_lsn()`
- `pg_stat_wal` (PG 14+): WAL generation / sync / write timings.
- `pg_stat_bgwriter`: checkpoints and buffers written.
- `pg_stat_replication`: replication lag (byte difference). Combine with `pg_wal_lsn_diff`.
- `pg_stat_archiver`: archival success/failure.
- `pg_replication_slots`: retention due to slots.
- `pg_waldump` (external command) to inspect records.

### 13. Hands-On Lab: Inspecting WAL & LSN Progression (Docker in WSL)
Environment: Single PostgreSQL 16 instance running in a Docker container inside WSL for fast, isolated experimentation.

Prereqs: Docker engine accessible in WSL (`docker ps` works).

1. Pull image
```bash
docker pull postgres:16
```
2. Start container (named `pgwal`) with a host bind mount for persistence (adjust path)
```bash
docker run -d --name pgwal -e POSTGRES_PASSWORD=postgres -p 5432:5432 -v $HOME/pgwal_data:/var/lib/postgresql/data postgres:16
```
3. Verify it is running
```bash
docker ps --filter name=pgwal
```
4. Exec a shell inside
```bash
docker exec -it pgwal bash
```
5. Connect with psql (inside container)
```bash
psql -U postgres
```
6. (Alternative from host) Use client without entering container
```bash
docker exec -it pgwal psql -U postgres
```

Data directory inside container: `/var/lib/postgresql/data` (WAL dir: `/var/lib/postgresql/data/pg_wal`).

Usage pattern: Run each command one at a time; observe LSN progression. From host you can prefix with `docker exec -it pgwal` if not already inside the container shell. Examples below assume you're already `bash`-ed into the container.

#### 13.1 Baseline Cluster Introspection
Current WAL insert position:
```bash
psql -U postgres -c "SELECT pg_current_wal_lsn();"
```
Current WAL file name:
```bash
psql -U postgres -c "SELECT pg_walfile_name(pg_current_wal_lsn());"
```
Show segment size:
```bash
psql -U postgres -c "SHOW wal_segment_size;"
```
Show data directory (to locate pg_wal):
```bash
psql -U postgres -c "SHOW data_directory;"
```
List a few WAL files (container path):
```bash
ls -lh /var/lib/postgresql/data/pg_wal | head
```

#### 13.2 Create Lab Database & Table
Create database:
```bash
psql -U postgres -c "CREATE DATABASE wal_lab;"
```
Connect to database (interactive) or single command variant; here single command examples use `-d`:
```bash
psql -U postgres -d wal_lab -c "CREATE TABLE t_demo(id bigserial PRIMARY KEY, payload text);"
```
Record LSN after schema create:
```bash
psql -U postgres -d wal_lab -c "SELECT pg_current_wal_lsn() AS lsn_after_create;"
```

#### 13.3 Bulk Insert & Measure Growth
Insert 1000 rows:
```bash
psql -U postgres -d wal_lab -c "INSERT INTO t_demo(payload) SELECT repeat('x',200) FROM generate_series(1,1000);"
```
LSN after bulk insert:
```bash
psql -U postgres -d wal_lab -c "SELECT pg_current_wal_lsn() AS lsn_after_bulk_insert;"
```
Bytes since start (rough baseline – using 0/0 is simplistic but illustrative):
```bash
psql -U postgres -d wal_lab -c "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0') AS bytes_since_start;"
```

If you captured the previous LSNs, compute delta (replace placeholders):
```bash
psql -U postgres -d wal_lab -c "SELECT pg_wal_lsn_diff('LSN_AFTER_BULK','LSN_AFTER_CREATE') AS bulk_bytes;"
```

#### 13.4 Checkpoint & Full Page Image Demonstration
Force a checkpoint:
```bash
psql -U postgres -c "CHECKPOINT;"
```
Record LSN after checkpoint:
```bash
psql -U postgres -c "SELECT pg_current_wal_lsn() AS lsn_after_checkpoint;"
```
Small update (likely triggers FPI for that page if first post-checkpoint change):
```bash
psql -U postgres -d wal_lab -c "UPDATE t_demo SET payload='y' WHERE id=1;"
```
Record LSN:
```bash
psql -U postgres -d wal_lab -c "SELECT pg_current_wal_lsn() AS lsn_after_update;"
```
Compute bytes for single-row update (replace placeholders):
```bash
psql -U postgres -d wal_lab -c "SELECT pg_wal_lsn_diff('LSN_AFTER_UPDATE','LSN_AFTER_CHECKPOINT') AS bytes_update;"
```

#### 13.5 Force WAL Segment Switch
Trigger switch:
```bash
psql -U postgres -c "SELECT pg_switch_wal();"
```
Show new WAL file:
```bash
psql -U postgres -c "SELECT pg_current_wal_lsn(), pg_walfile_name(pg_current_wal_lsn());"
```

#### 13.6 Inspect WAL Records (pg_waldump)
Find three newest WAL files:
```bash
ls -t /var/lib/postgresql/data/pg_wal | head -n 3
```
Dump first few records of one (replace FILENAME):
```bash
pg_waldump /var/lib/postgresql/data/pg_wal/FILENAME | head -n 20
```

#### 13.7 Generate Additional Workload & Measure Rate
Baseline LSN:
```bash
psql -U postgres -d wal_lab -c "SELECT pg_current_wal_lsn() AS baseline_lsn;"
```
Insert 50k rows:
```bash
psql -U postgres -d wal_lab -c "INSERT INTO t_demo(payload) SELECT repeat('z',200) FROM generate_series(1,50000);"
```
LSN after load:
```bash
psql -U postgres -d wal_lab -c "SELECT pg_current_wal_lsn() AS post_load_lsn;"
```
Compute delta (replace placeholders):
```bash
psql -U postgres -d wal_lab -c "SELECT pg_size_pretty(pg_wal_lsn_diff('POST_LOAD','BASELINE')) AS workload_bytes;"
```

#### 13.8 Cleanup (Optional)
```bash
exit
docker stop pgwal && docker rm pgwal
```

### 14. Performance Pointers (Short List)
- Big WAL spikes after checkpoints? Increase `max_wal_size`.
- Lots of WAL from updates? Check if you changed indexed columns (non-HOT updates).
- Disk churn of segment create/remove? Raise `min_wal_size`.
- High WAL size overall? Consider `wal_compression=on` (measure CPU first).

### 15. Common Pitfalls
- Forgotten replication slot (logical or physical) holds WAL forever.
- Failing archive command (if archiving enabled) causes WAL pileup.
- Too small `max_wal_size` forces frequent checkpoints (extra FPIs + latency).

### 16. Quick Cheat Sheet
| Task | Command |
|------|---------|
| Current LSN | `SELECT pg_current_wal_lsn();` |
| WAL filename for LSN | `SELECT pg_walfile_name(pg_current_wal_lsn());` |
| Switch segment | `SELECT pg_switch_wal();` |
| Force checkpoint | `CHECKPOINT;` |
| Bytes between LSNs | `SELECT pg_wal_lsn_diff('LSN1','LSN2');` |
| Dump WAL file | `pg_waldump <file>` |
| Show replication lag | `SELECT pg_size_pretty(pg_wal_lsn_diff(sent, replay)) ...` (join on `pg_stat_replication`) |


---
This pared-down guide covers actionable WAL knowledge: what it is, how to see it grow, and how to influence it. Add back advanced topics only when a concrete need appears.

