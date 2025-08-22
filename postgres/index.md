# PostgreSQL Indexes

Audience: Engineers who know SQL and want a crisp, practical mental model. Skips academic proofs; keeps what you need to design, tune, and debug.

---
## 1. What an Index Is (Practical Definition)
A separate, smaller (ideally) data structure that maps search keys → locations of table rows, so the executor can avoid scanning every heap page. You trade extra storage + write amplification for lower read latency & fewer I/O operations.

Core benefits:
- Accelerate WHERE / JOIN predicates, ORDER BY, GROUP BY, DISTINCT
- Enforce uniqueness / primary & foreign key constraints
- Enable index‑only scans (serve data without touching heap)

Costs:
- Extra storage (can equal or exceed table if careless)
- Slower INSERT / DELETE, and UPDATE when indexed columns change
- Planner overhead evaluating more choices
- Risk of bloat (dead entries) if high churn + weak maintenance

---
## 2. Default Method: B‑Tree (Lehman–Yao Variant)
Optimized for equality & range. Logarithmic height (usually 2–4 levels). Internal pages route; leaf pages hold ordered key tuples. PostgreSQL B‑Tree stores: key value + TID (block, offset). Physical heap layout is independent (no clustered index by default).

Index‑Only Scans: Require visibility map bits set (page all-visible). Vacuum/Autovacuum maintains those bits—otherwise executor must recheck heap for visibility.

Multi-column rules of thumb:
- Equality predicates first (col = ?)
- Then range / ordering columns (col BETWEEN ... / ORDER BY col)
- High selectivity earlier (reduces visited keys)

INCLUDE (covering) columns: Extra non-key attributes stored only at leaf level for index-only scans without affecting key ordering.

---
## 3. Other PostgreSQL Index Types (When B‑Tree Isn’t Enough)
| Type | Use For | Key Strength | Caveat |
|------|---------|--------------|--------|
| GIN | jsonb, arrays, full-text (tsvector), hstore, trigram | Fast containment / membership / multiple matches | Bigger & slower writes |
| GiST | Spatial (PostGIS), ranges, KNN distance | Flexible, supports nearest-neighbor | Possible false positives (recheck) |
| SP‑GiST | Partitionable / prefix (IP, text tries) | Efficient for skewed / hierarchical data | Fewer operator classes |
| BRIN | Huge append-only tables (time, ID) | Tiny size; scans only relevant block ranges | Low selectivity on small tables |
| Hash | Pure equality single column | Slight niche wins vs B‑Tree rarely | Usually skip; B‑Tree good enough |
| Bloom (ext) | Many low-selectivity columns combined | Compact probabilistic | False positives; equality only |
| pgvector (ANN) | Vector similarity search | Fast approximate nearest neighbor | Approximate; recall tuning required |

Pick index method to match the operators you use (planner can’t use a B‑Tree to optimize `@>` on jsonb; it wants a GIN).

---
## 4. Designing Lean Indexes
Decision checklist before adding one:
1. Query shape: Which predicates? Equality vs range vs containment vs similarity
2. Selectivity: Does predicate filter enough rows? (High cardinality preferred)
3. Access pattern: Need ordering? (Avoid separate ORDER BY index if existing composite can serve prefix order)
4. Coverage: Are reads heap-bound? (Consider INCLUDE for frequently read extra columns)
5. Write impact: How often do indexed columns change? (Hot update avoidance: only non-key column changes can be HOT; touching indexed column forces new index entry)
6. Size: Estimate: entries * (key bytes + ~14B overhead) / fill_factor
7. Lifecycle: Will the dataset churn? Plan autovacuum tuning or periodic REINDEX / pg_repack if severe bloat.

Partial Indexes: Add WHERE clause to narrow rows (e.g., only active, recent). Powerful for sparse predicates; reduce size & write cost.

Expression / Functional: Index the computed value you filter on (e.g., lower(email)). Must reference expression identically in queries.

---
## 5. Observability & Maintenance
Key views & functions:
- `pg_stat_user_indexes` – scans vs tuples read (detect unused indexes: `idx_scan = 0`)
- `pg_stat_all_indexes` – broader scope
- `pg_indexes_size(relname)` / `pg_relation_size(index)` – storage footprint
- `pg_stat_user_tables` – dead tuples driving bloat
- `EXPLAIN (ANALYZE, BUFFERS)` – confirm index usage & I/O pattern
- `pg_stat_statements` – top queries to target indexing effort

Bloat hint signals:
- Index size >> logical data size; high churn table; slow point lookups
- Autovacuum may lag (tune scale factors, raise `maintenance_work_mem`)

Remedies:
- `REINDEX CONCURRENTLY` to rebuild without blocking
- Drop unused / overlapping indexes (simplify planner & save writes)
- Consolidate multiple single-column indexes into a purposeful composite if queries use them together

---
## 6. Common Pitfalls
| Pitfall | Why It Hurts | Fix |
|---------|--------------|-----|
| Indexing low-cardinality boolean alone | Planner may still seq scan; poor selectivity | Combine with another column or partial (WHERE flag = true) |
| Too many overlapping indexes | Write amplification; planner confusion | Audit usage; drop redundant ones |
| Ignoring autovacuum settings for large tables | Dead tuples linger; fewer index-only scans | Lower scale factors; monitor `age(relfrozenxid)` |
| Relying on hash index expecting big gains | B‑Tree usually as good; fewer features historically | Prefer B‑Tree except rare cases |
| Not analyzing after bulk load + index create | Planner misestimates selectivity | `ANALYZE` post load |
| Expression mismatch (lower(email) vs email) | Index unusable; full scans | Query must match expression |

## Indexing Examples with PostgreSQL

### Setup with docker and connect to database
Use a disposable local Postgres (adjust volume path as desired):
```bash
docker run -d --name pgidx -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
docker exec -it pgidx psql -U postgres
```
For all examples below, run inside psql unless noted. Reset easily: `docker rm -f pgidx && (re-run docker run ...)`.

Create a sandbox schema:
```sql
CREATE SCHEMA idxlab; SET search_path=idxlab,public; \dn+
```

Each use case: problem → index → query. (Add `EXPLAIN (ANALYZE, BUFFERS)` to observe plans.)

---
### 1. Primary Key (Implicit B‑Tree) – Fast Row Lookup
```sql
CREATE TABLE users (
  id BIGSERIAL PRIMARY KEY,
  email TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
INSERT INTO users(email) SELECT 'user'||g||'@pglab.io' FROM generate_series(1,50000) g;
SELECT * FROM users WHERE id = 1234;
```
Primary key automatically supplies unique B‑Tree.

### 2. Unique Secondary Index – Email Login
```sql
CREATE UNIQUE INDEX idx_users_email ON users (email);
SELECT * FROM users WHERE email = 'user42@pglab.io';
```
Guarantees uniqueness + fast lookup separate from PK.

### 3. Composite Index – Latest Orders per User
```sql
CREATE TABLE orders (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT now(),
  status TEXT,
  total_cents INT
);
INSERT INTO orders(user_id,status,total_cents,created_at)
SELECT (random()*50000)::int+1,
       (ARRAY['NEW','PAID','SHIPPED','CANCELLED'])[1+floor(random()*4)],
       (random()*5000)::int,
       now() - (random()*'30 days'::interval)
FROM generate_series(1,200000);
CREATE INDEX idx_orders_user_created ON orders (user_id, created_at DESC);
SELECT id,total_cents FROM orders WHERE user_id=1234 ORDER BY created_at DESC LIMIT 5;
```
Equality column first, then ordering column.

### 4. Partial Index – Active Sessions Only
```sql
CREATE TABLE sessions (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT,
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);
INSERT INTO sessions(user_id,expires_at,revoked_at)
SELECT (random()*50000)::int+1,
       now() + (random()*'1 hour'::interval),
       CASE WHEN random()<0.7 THEN NULL ELSE now() END
FROM generate_series(1,300000);
-- Predicate must be IMMUTABLE; time-dependent functions like now() are not allowed.
-- Index only on stable condition; apply the moving time filter in the query.
CREATE INDEX idx_sessions_active ON sessions (user_id, expires_at)
  WHERE revoked_at IS NULL;  -- immutable predicate
SELECT * FROM sessions
 WHERE user_id=42
   AND revoked_at IS NULL
   AND expires_at > now()   -- dynamic part evaluated at runtime
 ORDER BY expires_at LIMIT 3;
```
Indexes only the non-revoked subset → smaller & cheaper. Note: Adding `expires_at > now()` inside the index definition is invalid (non-IMMUTABLE) and would also go stale. If you need faster pruning of obviously expired rows, consider:
- Periodic purge/job to delete expired sessions
- A boolean `is_active` maintained by application logic (then partial index WHERE is_active)
- A fixed cutoff partial index (e.g. `expires_at > '2025-01-01'`) for archive partitions, not rolling “now()”.

### 5. Expression Index – Case-Insensitive Email
```sql
CREATE INDEX idx_users_lower_email ON users ((lower(email)));
SELECT * FROM users WHERE lower(email)=lower('User123@pglab.io');
```
Query expression must match index expression.

### 6. Covering (INCLUDE) Index – Avoid Heap Visits
```sql
CREATE INDEX idx_orders_user_cover ON orders (user_id) INCLUDE (status,total_cents,created_at);
SELECT status,total_cents FROM orders WHERE user_id=777 LIMIT 10;
```
Can become index-only once pages marked all-visible. Use this pattern when: (a) a hot read path repeatedly fetches only these few columns, (b) the base table row is much wider (avoids extra heap I/O), (c) data is relatively stable so included columns aren’t updated constantly, and (d) high concurrency amplifies buffer cache savings. Skip or delay if the table is tiny (seq scan cheap), queries usually need many other columns, or included columns churn—each UPDATE touching them still rewrites the index entry.

### 7. GIN on jsonb – Attribute Containment
```sql
CREATE TABLE products (id BIGSERIAL PRIMARY KEY, attrs JSONB NOT NULL);
INSERT INTO products(attrs)
SELECT jsonb_build_object(
  'color',(ARRAY['red','green','blue','black'])[1+floor(random()*4)],
  'size',(ARRAY['S','M','L','XL'])[1+floor(random()*4)],
  'onsale',(random()<0.3)
) FROM generate_series(1,80000);
CREATE INDEX idx_products_attrs ON products USING gin (attrs jsonb_path_ops);
SELECT count(*) FROM products WHERE attrs @> '{"color":"red","onsale":true}';
```
`jsonb_path_ops` is a containment‑focused operator class: smaller & faster for `@>` but it does NOT accelerate key-existence operators like `?` / `?&` / `?|`. If you also need those, use the default `jsonb_ops`:
```sql
CREATE INDEX idx_products_attrs_ops ON products USING gin (attrs); -- jsonb_ops implicit
```

When to choose which:
- Mostly `@>` filters on medium/large jsonb → `jsonb_path_ops` (lean & fast)
- Mixed operators (`@>`, `?`, `?&`, `?|`) → default `jsonb_ops`
- Very frequent writes + large pending list growth → consider `ALTER INDEX idx_products_attrs SET (fastupdate = off);` after bulk load, then `REINDEX` if bloat appears.

Key GIN tuning/observability:
- `fastupdate` (default on) buffers inserts in a pending list → faster writes, later merged (VACUUM or autovacuum). Turning it off lowers write bursts but increases immediate work.
- `gin_pending_list_limit` (shared setting) caps pending list size; large list can cause sudden bulk merge latency spike.
- Check bloat / tuple counts: `SELECT * FROM pg_stat_user_indexes WHERE indexrelname='idx_products_attrs';`
- Rebuild if overly large vs data: `REINDEX INDEX CONCURRENTLY idx_products_attrs;`

Query tips:
- Always keep the literal JSON structure (key order doesn’t matter). Extra keys in row are fine; containment only requires provided pair(s) exist.
- Combine with other predicates (e.g., status column) to further reduce heap visits.

If you only ever need a few scalar keys, consider normal columns instead of JSONB; simpler B‑Trees beat any GIN for point lookups.
