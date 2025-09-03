# Global Strong Consistency with PostgreSQL: A Practical Guide

Building reliable distributed systems requires careful consideration of consistency guarantees. This guide explores implementing "global strong" consistency using PostgreSQL's synchronous replication. This is a practical approach that achieves near-zero data loss without the complexity of multi-master setups or consensus algorithms.

## What is Global Strong Consistency?

**Global strong consistency** means that once a write is acknowledged to the client, that data is:
1. **Durably stored** on the primary database
2. **Synchronously replicated** to all defined replica nodes
3. **Immediately readable** from any synchronized replica with the same data

This provides **Recovery Point Objective (RPO) ≈ 0**, meaning any promoted replica contains all confirmed writes, eliminating data loss during failover scenarios.

### Why "Global"?

The term "global" comes from the theoretical deployment where databases are distributed across different geographical regions worldwide. In a true global setup, you would have:
- **Primary database** in one region (e.g., US East)
- **Replica 1** in another region (e.g., Europe)  
- **Replica 2** in a third region (e.g., Asia)

This geographic distribution provides disaster recovery and read performance benefits across the globe, hence "global" strong consistency.

### Theory vs. Practice in This Guide

**In Theory**: Deploy across multiple regions for true global coverage
**In Practice**: We deploy all 3 databases in the same region for simplicity

This same-region approach lets us focus on the core PostgreSQL replication mechanics without dealing with cross-region networking complexities like VNet peering, latency, and firewall rules. The consistency guarantees and configuration are identical whether databases are in the same region or across continents.

### What This Guide Covers
- Single-writer, multi-reader PostgreSQL setup
- Synchronous streaming replication using `synchronous_standby_names`
- Practical deployment on Kubernetes (same-region for simplicity)
- Real-world troubleshooting and configuration issues

### What This Guide Does NOT Cover
- Multi-master replication
- Consensus algorithms (Raft, Paxos)
- Global clock synchronization (TrueTime)
- Cross-region networking complexity

## How This Differs from Simple PostgreSQL Replication

Many PostgreSQL tutorials show basic replication, but this guide focuses on **global strong consistency** which has specific requirements:

| Aspect | Simple Replication | Global Strong (This Guide) |
|--------|-------------------|----------------------------|
| **Synchronization** | Asynchronous (default) | **Synchronous** - writes wait for all replicas |
| **Data Loss Risk** | Possible during failover | **Zero data loss** (RPO ≈ 0) |
| **Write Acknowledgment** | Returns immediately | **Waits for replica confirmation** |
| **Configuration** | `synchronous_commit = off` | `synchronous_standby_names = 'ANY 2 (...)'` |
| **Failover Safety** | May lose recent writes | **All confirmed writes preserved** |
| **Performance** | Faster writes | Higher write latency but guaranteed durability |
| **Use Case** | Read scaling, backup | **Mission-critical systems requiring zero data loss** |

### Key Technical Differences

**1. Synchronous Commit Behavior**
```sql
-- Simple replication (async)
synchronous_commit = off          # Fast writes, possible data loss

-- Global strong (this guide)  
synchronous_commit = on           # Slower writes, zero data loss
synchronous_standby_names = 'ANY 2 (replica1, replica2)'
```

**2. Write Guarantees**
- **Simple**: Write returns when saved to primary WAL
- **Global Strong**: Write returns only after confirmed on ALL replicas

**3. Failover Characteristics** 
- **Simple**: Recent writes may be lost during failover
- **Global Strong**: Any replica can be promoted with zero data loss

This makes global strong consistency suitable for financial systems, critical business data, and any scenario where data loss is unacceptable.

---

## PostgreSQL Synchronous Replication Architecture

PostgreSQL achieves global strong consistency through **synchronous streaming replication**:

```
┌─────────────┐    sync repl    ┌─────────────┐
│   Primary   │ ──────────────► │  Replica 1  │
│             │                 │             │
│ Writes here │                 │ Read-only   │
└─────────────┘                 └─────────────┘
       │                               
       │         sync repl              
       └────────────────────► ┌─────────────┐
                               │  Replica 2  │
                               │             │
                               │ Read-only   │
                               └─────────────┘
```

**Key Configuration**: `synchronous_standby_names = 'ANY 2 (replica1, replica2)'`

This setting ensures writes are **not acknowledged** until confirmed on both replicas, guaranteeing data durability across all nodes.

---

## Core Configuration Requirements

### Primary Database Settings
```sql
-- Enable WAL for replication
wal_level = replica

-- Allow replica connections
max_wal_senders = 10
max_replication_slots = 10

-- Ensure synchronous commits
synchronous_commit = on

-- Critical: Must match or be exceeded by replicas
max_connections = 200
shared_buffers = 256MB

-- Enable external connections
listen_addresses = '*'
```

### Replication Role Setup
```sql
-- Create dedicated replication user
CREATE ROLE repl WITH REPLICATION LOGIN ENCRYPTED PASSWORD 'ReplPassw0rd!';
```

### Authentication Configuration (pg_hba.conf)
```bash
# Allow replication connections (note: 'database = all' does NOT cover replication)
host replication repl 0.0.0.0/0 scram-sha-256
```

**Critical Point**: PostgreSQL requires explicit `replication` database entries in pg_hba.conf. Generic `all` database rules do not apply to replication connections.

---

## Same-Region Kubernetes Lab Setup

We'll deploy three databases in the same Kubernetes cluster to avoid cross-region networking complexity while demonstrating the replication mechanics.

### Prerequisites
- Azure CLI (`az`) logged in
- `kubectl` installed  
- `psql` client available
- Azure quota for one AKS cluster

### Environment Variables
```bash
export RG=pg-global-rg
export REGION=eastus2
export AKS_CLUSTER=pg-cluster
export PG_SUPER_PASS='SuperPassw0rd!'
export REPL_PASS='ReplPassw0rd!'
export DB_NAMESPACE=pg
```

### Step 1: Create Infrastructure
```bash
# Create resource group and AKS cluster
az group create -n "$RG" -l "$REGION"
az aks create -g "$RG" -n "$AKS_CLUSTER" -l "$REGION" \
  --node-count 1 --node-vm-size Standard_D4s_v3 --generate-ssh-keys

# Get cluster credentials
az aks get-credentials -g "$RG" -n "$AKS_CLUSTER"
```

### Step 2: Deploy Primary Database
```bash
# Create namespace and secrets
kubectl create namespace "$DB_NAMESPACE"
kubectl -n "$DB_NAMESPACE" create secret generic pg-secrets \
  --from-literal=POSTGRES_PASSWORD="$PG_SUPER_PASS" \
  --from-literal=REPL_PASSWORD="$REPL_PASS"

# Deploy primary (without sync settings initially)
kubectl -n "$DB_NAMESPACE" apply -f primary.yaml

# Get primary IP for replica configuration  
PRIMARY_IP=$(kubectl -n "$DB_NAMESPACE" get svc pg-primary -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Primary IP: $PRIMARY_IP"
```

### Step 3: Configure Replication Prerequisites

**Create replication role:**
```bash
PRIMARY_POD=$(kubectl -n "$DB_NAMESPACE" get pod -l app=pg-primary -o jsonpath='{.items[0].metadata.name}')

kubectl -n "$DB_NAMESPACE" exec -it "$PRIMARY_POD" -- psql -U postgres -c \
  "CREATE ROLE repl WITH REPLICATION LOGIN ENCRYPTED PASSWORD '$REPL_PASS';"
```

**Configure pg_hba.conf for replication access:**
```bash
kubectl -n "$DB_NAMESPACE" exec "$PRIMARY_POD" -- bash -c \
  'echo "host replication repl 0.0.0.0/0 scram-sha-256" >> /var/lib/postgresql/data/pg_hba.conf'

kubectl -n "$DB_NAMESPACE" exec "$PRIMARY_POD" -- psql -U postgres -c 'SELECT pg_reload_conf();'
```

**Verify configuration:**
```bash
kubectl -n "$DB_NAMESPACE" exec "$PRIMARY_POD" -- psql -U postgres -c \
  "SELECT line_number,type,database,user_name,address,auth_method FROM pg_hba_file_rules WHERE 'replication' = ANY(database);"
```

### Step 4: Deploy Replicas

**Update replica YAML files with primary IP:**
```bash
sed -i "s/<PRIMARY_IP>/$PRIMARY_IP/g" replica1.yaml
sed -i "s/<PRIMARY_IP>/$PRIMARY_IP/g" replica2.yaml
```

**Deploy both replicas:**
```bash
kubectl -n "$DB_NAMESPACE" apply -f replica1.yaml
kubectl -n "$DB_NAMESPACE" apply -f replica2.yaml
```

**Monitor replica startup:**
```bash
kubectl -n "$DB_NAMESPACE" get pods -w
```

### Step 5: Enable Synchronous Replication

**Edit primary.yaml to add synchronous replication:**
```yaml
args:
  - -c
  - wal_level=replica
  - -c
  - max_wal_senders=10
  - -c
  - max_replication_slots=10
  - -c
  - synchronous_commit=on
  - -c
  - shared_buffers=256MB
  - -c
  - max_connections=200
  - -c
  - listen_addresses=*
  - -c
  - synchronous_standby_names=ANY 2 (replica1, replica2)  # ADD THIS LINE
```

**Apply the change:**
```bash
kubectl -n "$DB_NAMESPACE" apply -f primary.yaml
kubectl -n "$DB_NAMESPACE" rollout status deploy/pg-primary
```

**Verify synchronous replication is active:**
```bash
kubectl -n "$DB_NAMESPACE" exec deploy/pg-primary -- psql -U postgres -c \
  "SELECT application_name, sync_state, write_lsn, flush_lsn, replay_lsn FROM pg_stat_replication;"
```

Expected output shows both replicas with `sync_state = 'sync'`.

### Step 6: Test Global Strong Consistency

**Write test data:**
```bash
kubectl -n "$DB_NAMESPACE" exec deploy/pg-primary -- psql -U postgres -c \
  "CREATE TABLE consistency_test(id serial primary key, data text, created_at timestamp default now());"

kubectl -n "$DB_NAMESPACE" exec deploy/pg-primary -- psql -U postgres -c \
  "INSERT INTO consistency_test(data) VALUES('Global strong test - $(date)');"
```

**Verify data appears on all replicas:**
```bash
# Get replica IPs
R1_IP=$(kubectl -n "$DB_NAMESPACE" get svc pg-replica1 -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
R2_IP=$(kubectl -n "$DB_NAMESPACE" get svc pg-replica2 -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Read from both replicas
psql -h "$R1_IP" -U postgres -c "SELECT * FROM consistency_test;"
psql -h "$R2_IP" -U postgres -c "SELECT * FROM consistency_test;"
```

Both queries should return identical data, confirming global strong consistency.

---

## Common Issues and Solutions

### Issue 1: Replica Crashes with "Insufficient Parameter Settings"

**Problem**: Replica fails with error like:
```
FATAL: recovery aborted because of insufficient parameter settings
DETAIL: max_connections = 100 is a lower setting than on the primary server, where its value was 200.
```

**Root Cause**: PostgreSQL replicas must have parameter settings that match or exceed the primary's configuration.

**Solution**: Ensure replica YAML includes ALL primary configuration parameters:
```yaml
# In replica1.yaml and replica2.yaml
containers:
- name: postgres
  image: postgres:16
  args:
    - -c
    - wal_level=replica
    - -c
    - max_wal_senders=10
    - -c
    - max_replication_slots=10
    - -c
    - synchronous_commit=on
    - -c
    - shared_buffers=256MB
    - -c
    - max_connections=200        # MUST match or exceed primary
    - -c
    - listen_addresses=*
```

### Issue 2: Replication Slot Already Exists

**Problem**: Init container fails with:
```
pg_basebackup: error: replication slot "replica1" already exists
```

**Solution**: Drop the existing slot:
```bash
kubectl -n "$DB_NAMESPACE" exec deploy/pg-primary -- psql -U postgres -c \
  "SELECT pg_drop_replication_slot('replica1');"
```

### Issue 3: Missing Volume Mounts in Init Containers

**Problem**: pg_basebackup appears successful but replica crashes on startup.

**Root Cause**: Init container and main container must share the same data directory.

**Solution**: Ensure init container has proper volume mount:
```yaml
initContainers:
- name: basebackup
  image: postgres:16
  volumeMounts:                    # CRITICAL: Must be present
  - name: data
    mountPath: /var/lib/postgresql/data
```

### Issue 4: Authentication Failures

**Problem**: Replicas cannot connect to primary for basebackup.

**Solution**: Verify pg_hba.conf has explicit replication rule:
```bash
kubectl -n "$DB_NAMESPACE" exec "$PRIMARY_POD" -- psql -U postgres -c \
  "SELECT * FROM pg_hba_file_rules WHERE 'replication' = ANY(database);"
```

---

## Failover Testing

### Simulate Primary Failure and Test New Primary

**Step 1: Scale down the original primary**
```bash
# Scale down primary
kubectl -n "$DB_NAMESPACE" scale deploy/pg-primary --replicas=0
```

**Step 2: Promote replica1 to become the new primary**
```bash
# Promote replica1 to primary
kubectl -n "$DB_NAMESPACE" exec deploy/pg-replica1 -- psql -U postgres -c "SELECT pg_promote();"

# Verify promotion
kubectl -n "$DB_NAMESPACE" exec deploy/pg-replica1 -- psql -U postgres -c "SELECT pg_is_in_recovery();"
```

Result should show `pg_is_in_recovery = f` (false), indicating replica1 is now the primary.

**Step 3: Write new data to the promoted primary (replica1)**
```bash
# Set password for connection
export PGPASSWORD='SuperPassw0rd!'

# Get replica1 IP (now the new primary)
R1_IP=$(kubectl -n "$DB_NAMESPACE" get svc pg-replica1 -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Write new data to the promoted primary
psql -h "$R1_IP" -U postgres -c \
  "INSERT INTO consistency_test(data) VALUES('Written to NEW primary after failover - $(date)');"

# Verify the data exists on the new primary
psql -h "$R1_IP" -U postgres -c "SELECT * FROM consistency_test ORDER BY id;"
```

**Step 4: Check if replica2 receives the new data**
```bash
# Get replica2 IP
R2_IP=$(kubectl -n "$DB_NAMESPACE" get svc pg-replica2 -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Check if replica2 has the new data
psql -h "$R2_IP" -U postgres -c "SELECT * FROM consistency_test ORDER BY id;"
```

**Expected Behavior:**
- ✅ The new data should appear on replica1 (new primary) immediately
- ❌ The new data will **NOT** appear on replica2 because it's still trying to replicate from the old primary

**Step 5: Reconfigure replica2 to follow the new primary (Optional)**

Here are the actual commands to make replica2 follow replica1 as the new primary:

**Option A: Using pg_rewind (Recommended - faster)**
```bash
# Get the new primary IP (replica1)
NEW_PRIMARY_IP=$(kubectl -n "$DB_NAMESPACE" get svc pg-replica1 -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Stop PostgreSQL on replica2 (but keep the pod running)
kubectl -n "$DB_NAMESPACE" exec deploy/pg-replica2 -- pg_ctl stop -D /var/lib/postgresql/data

# Run pg_rewind from inside the replica2 pod (already running as postgres user)
kubectl -n "$DB_NAMESPACE" exec deploy/pg-replica2 -- bash -c "
export PGPASSWORD='ReplPassw0rd!'
pg_rewind --target-pgdata=/var/lib/postgresql/data \\
  --source-server=\"host=$NEW_PRIMARY_IP port=5432 user=repl dbname=postgres\" \\
  --progress
"

# Update recovery configuration to point to new primary
kubectl -n "$DB_NAMESPACE" exec deploy/pg-replica2 -- bash -c "
echo \"standby_mode = 'on'\" > /var/lib/postgresql/data/recovery.conf
echo \"primary_conninfo = 'host=$NEW_PRIMARY_IP port=5432 user=repl application_name=replica2'\" >> /var/lib/postgresql/data/recovery.conf
"

# Restart PostgreSQL on replica2
kubectl -n "$DB_NAMESPACE" exec deploy/pg-replica2 -- pg_ctl start -D /var/lib/postgresql/data
```

**Alternative: If the above doesn't work, try this simpler approach:**
```bash
# Run the commands step by step in the replica2 pod
kubectl -n "$DB_NAMESPACE" exec -it deploy/pg-replica2 -- bash

# Inside the pod (already running as postgres user):
export PGPASSWORD='ReplPassw0rd!'
pg_ctl stop -D /var/lib/postgresql/data

pg_rewind --target-pgdata=/var/lib/postgresql/data \
  --source-server="host=$NEW_PRIMARY_IP port=5432 user=repl dbname=postgres" \
  --progress

echo "standby_mode = 'on'" > /var/lib/postgresql/data/recovery.conf
echo "primary_conninfo = 'host=$NEW_PRIMARY_IP port=5432 user=repl application_name=replica2'" >> /var/lib/postgresql/data/recovery.conf

pg_ctl start -D /var/lib/postgresql/data
exit
```

**Option B: Fresh pg_basebackup (Simpler but slower)**
```bash
# Get the new primary IP (replica1)
NEW_PRIMARY_IP=$(kubectl -n "$DB_NAMESPACE" get svc pg-replica1 -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Update replica2.yaml to point to the new primary IP
sed -i "s/<PRIMARY_IP>/$NEW_PRIMARY_IP/g" replica2.yaml

# Delete and recreate replica2 with fresh basebackup
kubectl -n "$DB_NAMESPACE" delete deploy pg-replica2
kubectl -n "$DB_NAMESPACE" apply -f replica2.yaml

# Wait for it to come up
kubectl -n "$DB_NAMESPACE" get pods -w
```

## When to Use This Pattern

✅ **Good fit when:**
- Single write region is acceptable
- Strong durability requirements (zero data loss)
- Moderate write volume with acceptable latency impact
- Read scaling across multiple nodes needed

❌ **Not suitable when:**
- Need writes from multiple regions simultaneously
- Extremely low latency requirements (<30-40ms globally)
- Complex conflict resolution needed
- Automatic partition tolerance required

---

## Cleanup
```bash
# Delete all resources
az group delete -n "$RG" --yes --no-wait
```

---

This approach provides a solid foundation for understanding global strong consistency with PostgreSQL before tackling more complex distributed systems challenges.
