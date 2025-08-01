# PostgreSQL Process Architecture

## Overview
When you start a PostgreSQL server, it doesn't just run a single process. Instead, PostgreSQL creates a multi-process architecture with several specialized processes working together to provide database functionality. This document details all the processes you'll see when running a PostgreSQL instance.

## Process Hierarchy

### 1. Postmaster Process (Main Process)
- **Process Name**: `postgres`
- **Role**: Main supervisor process
- **Responsibilities**:
  - Starting and managing all other PostgreSQL processes
  - Listening for client connections
  - Forking backend processes for each client connection
  - Managing shared memory initialization
  - Signal handling and process coordination
- **Lifecycle**: First to start, last to stop
- **Command Line**: Usually shows as `postgres -D /var/lib/postgresql/data`

## Background Processes (Always Running)

### 2. Background Writer Process
- **Process Name**: `postgres: background writer`
- **Purpose**: Writes dirty pages from shared buffers to disk
- **What are Dirty Pages?**:
  - **Clean Page**: Memory page that matches disk version (unchanged)
  - **Dirty Page**: Memory page that has been modified but not written to disk yet
  - **Creation**: Pages become dirty when applications INSERT, UPDATE, or DELETE data
  - **Risk**: If system crashes, dirty pages in memory are lost (hence WAL for recovery)
- **Benefits**:
  - Reduces checkpoint I/O spikes by continuously writing dirty pages
  - Improves overall system performance by spreading disk writes over time
  - Ensures data durability by persisting changes to disk
  - Frees up buffer space for new data
- **Configuration**: Controlled by `bgwriter_*` parameters

### 3. WAL Writer Process
- **Process Name**: `postgres: wal writer`
- **Purpose**: Flushes WAL (Write-Ahead Log) buffers to disk
- **Trigger Mechanisms**:
  1. **Periodic Timer**: Wakes up every `wal_writer_delay` milliseconds (default: 200ms)
  2. **Buffer Threshold**: Triggered when WAL buffers reach `wal_writer_flush_after` bytes (default: 1MB)
  3. **Backend Requests**: Backend processes signal for immediate flush during commits
  4. **Administrative Signals**: Manual triggers via system signals or commands
- **Operations**:
  - Periodic WAL buffer flushes to reduce commit latency
  - Responds to immediate flush requests from backend processes
  - Ensures transaction durability by persisting WAL records
  - Prevents WAL buffer overflow by proactive flushing
- **Benefits**:
  - Reduces transaction commit times by pre-flushing WAL data
  - Spreads I/O load over time instead of bursts during commits
  - Improves overall system responsiveness
- **Configuration**: `wal_writer_delay`, `wal_writer_flush_after`, `synchronous_commit`

### 4. Checkpointer Process
- **Process Name**: `postgres: checkpointer`
- **Purpose**: Performs checkpoint operations
- **Functions**:
  - Writes all dirty pages to disk
  - Updates control file
  - Truncates WAL files when safe
- **Configuration**: `checkpoint_*` parameters

### 5. Autovacuum Launcher
- **Process Name**: `postgres: autovacuum launcher`
- **Purpose**: Manages automatic VACUUM and ANALYZE operations
- **Responsibilities**:
  - Monitors table statistics
  - Launches autovacuum worker processes
  - Prevents transaction ID wraparound
- **Configuration**: `autovacuum_*` parameters

### 6. Stats Collector Process
- **Process Name**: `postgres: stats collector`
- **Purpose**: Collects and maintains database statistics
- **Data Collected**:
  - Table and index usage statistics
  - Database activity metrics
  - Query performance data
- **Output**: Updates `pg_stat_*` views

### 7. Logical Replication Launcher (if enabled)
- **Process Name**: `postgres: logical replication launcher`
- **Purpose**: Manages logical replication subscriptions
- **Functions**:
  - Starts and stops subscription workers
  - Monitors replication health
  - Handles subscription lifecycle

## Dynamic Processes (Created as Needed)

### 8. Backend Processes (Per Client Connection)
- **Process Name**: `postgres: username dbname client_ip(port) [state]`
- **Purpose**: Handle individual client connections
- **Examples**:
  ```
  postgres: myuser mydb 192.168.1.100(54321) idle
  postgres: myuser mydb 192.168.1.100(54322) SELECT
  postgres: myuser mydb 192.168.1.100(54323) idle in transaction
  ```
- **Connection States Explained**:
  - **`idle`**: Connection open, waiting for next command from client
    - *Normal state* for connection pools and interactive sessions
    - *Resource usage*: Minimal (just memory for connection state)
  - **`active`**: Currently executing a query
    - *Resource usage*: CPU, memory, potentially I/O
    - *Query visible* in `pg_stat_activity.query` column
  - **`idle in transaction`**: Inside open transaction, not executing query
    - *⚠️ Warning*: Holds locks, can block other operations
    - *Common issue*: Long-running transactions causing performance problems
  - **`idle in transaction (aborted)`**: Transaction failed, awaiting rollback
    - *Problem*: Still holds locks until client issues ROLLBACK
    - *Resolution*: Client must send ROLLBACK command
  - **`fastpath function call`**: Executing function via fastpath interface
    - *Usage*: Some drivers use this for optimized function calls
    - *Less common* than other states
- **Monitoring**: Use `pg_stat_activity` to see current state and query details

### 9. Autovacuum Worker Processes
- **Process Name**: `postgres: autovacuum worker process`
- **Purpose**: Perform actual VACUUM/ANALYZE operations
- **Lifecycle**: Started by autovacuum launcher, terminate when done
- **Limit**: Controlled by `autovacuum_max_workers`

### 10. WAL Sender Processes (Replication)
- **Process Name**: `postgres: wal sender process`
- **Purpose**: Stream WAL data to standby servers
- **Types**:
  - Physical replication (streaming)
  - Logical replication
- **Configuration**: `max_wal_senders`

### 11. WAL Receiver Process (Standby Only)
- **Process Name**: `postgres: wal receiver process`
- **Purpose**: Receive WAL data from primary server
- **Location**: Only on standby/replica servers
- **Function**: Applies received WAL records

### 12. Parallel Worker Processes
- **Process Name**: `postgres: parallel worker`
- **Purpose**: Execute parallel queries
- **Lifecycle**: Created for parallel query execution, terminated when done
- **Configuration**: `max_parallel_workers`, `max_parallel_workers_per_gather`

## Process Monitoring Commands

### Docker Container Setup (For Testing)
```bash
# Start PostgreSQL container
docker run -d --name postgres-demo \
  -e POSTGRES_PASSWORD=demo123 \
  -e POSTGRES_DB=testdb \
  -p 5432:5432 \
  postgres:15

# Verify container is running
docker ps

# Exec into the container
docker exec -it postgres-demo bash

# Connect to PostgreSQL from within container
psql -U postgres -d testdb

# Clean up when done
docker stop postgres-demo && docker rm postgres-demo
```

### View All PostgreSQL Processes
```bash
# Linux/Unix (on host machine with native PostgreSQL installation)
ps aux | grep postgres

# Alternative with process tree
pstree -p $(pgrep -f "postgres.*postmaster")

# macOS
ps aux | grep postgres

# ⚠️ IMPORTANT: For Docker containers, behavior differs by location:

# 1. FROM WSL/HOST MACHINE - Shows Docker processes
ps aux | grep postgres
# This WON'T show PostgreSQL processes inside the container
# You'll only see the docker container process

# 2. INSIDE THE CONTAINER - Shows PostgreSQL processes  
docker exec -it postgres-demo bash
ps aux | grep postgres  # May not work if ps command is missing

# 3. ALTERNATIVE: Use /proc filesystem inside container
docker exec -it postgres-demo bash -c "
  for pid in \$(ls /proc | grep '^[0-9]*\$'); do 
    if [ -f /proc/\$pid/cmdline ]; then 
      cmdline=\$(cat /proc/\$pid/cmdline | tr '\0' ' ')
      if echo \"\$cmdline\" | grep -q postgres; then 
        echo \"PID \$pid: \$cmdline\"
      fi
    fi
  done"

# 4. FROM HOST: Check what's running in the container
docker exec postgres-demo ps aux | grep postgres  # If ps is available
```

### Example Output
```bash
$ ps aux | grep postgres
postgres  1234  0.0  1.2  123456  12345 ?  S  10:00  0:00 postgres -D /var/lib/postgresql/data
postgres  1235  0.0  0.8  123456   8192 ?  Ss 10:00  0:00 postgres: background writer
postgres  1236  0.0  0.8  123456   8192 ?  Ss 10:00  0:00 postgres: wal writer
postgres  1237  0.0  0.8  123456   8192 ?  Ss 10:00  0:00 postgres: checkpointer
postgres  1238  0.0  0.8  123456   8192 ?  Ss 10:00  0:00 postgres: autovacuum launcher
postgres  1239  0.0  0.8  123456   8192 ?  Ss 10:00  0:00 postgres: stats collector
postgres  1240  0.0  1.0  123456  10240 ?  Ss 10:05  0:01 postgres: myuser mydb 192.168.1.100(54321) idle
```

### Real Container Example (PostgreSQL 15)
```bash
# Using /proc filesystem when ps is not available
$ for pid in $(ls /proc | grep '^[0-9]*$'); do 
    if [ -f /proc/$pid/cmdline ]; then 
      echo "PID $pid: $(cat /proc/$pid/cmdline | tr '\0' ' ')"
    fi
  done | grep postgres

PID 1: postgres                              # Postmaster process  
PID 62: postgres: checkpointer               # Checkpointer process
PID 63: postgres: background writer          # Background writer
PID 65: postgres: walwriter                  # WAL writer  
PID 66: postgres: autovacuum launcher        # Autovacuum launcher
PID 67: postgres: logical replication launcher # Logical replication launcher
```

### PostgreSQL Internal Views
```sql
-- View all active processes
SELECT pid, usename, application_name, client_addr, state, query_start
FROM pg_stat_activity;

-- View background processes
SELECT pid, backend_type 
FROM pg_stat_activity 
WHERE backend_type IS NOT NULL;

-- Check replication processes
SELECT pid, application_name, client_addr, state, sync_state
FROM pg_stat_replication;
```

### Real Container Query Results
```sql
-- Background processes in PostgreSQL 15 container
SELECT pid, backend_type, application_name, state FROM pg_stat_activity 
WHERE backend_type IS NOT NULL ORDER BY pid;

 pid |         backend_type         | application_name | state  
-----+------------------------------+------------------+--------
  62 | checkpointer                 |                  | 
  63 | background writer            |                  | 
  65 | walwriter                    |                  | 
  66 | autovacuum launcher          |                  | 
  67 | logical replication launcher |                  | 
 213 | client backend               | psql             | active
```

## Process Count Summary

### Minimum Processes (Basic Setup)
| Process Type | Count | Always Present |
|--------------|--------|----------------|
| Postmaster | 1 | ✅ |
| Background Writer | 1 | ✅ |
| WAL Writer | 1 | ✅ |
| Checkpointer | 1 | ✅ |
| Autovacuum Launcher | 1 | ✅ (if enabled) |
| Stats Collector | 1 | ✅ |
| **Total Base Processes** | **6** | **Minimum** |

### Additional Processes (Variable)
| Process Type | Count | Condition |
|--------------|--------|-----------|
| Backend Processes | 0-max_connections | Per client connection |
| Autovacuum Workers | 0-autovacuum_max_workers | When needed |
| WAL Senders | 0-max_wal_senders | If replication enabled |
| WAL Receiver | 0-1 | Standby servers only |
| Parallel Workers | 0-max_parallel_workers | During parallel queries |
| Logical Rep Launcher | 0-1 | If logical replication enabled |

## Configuration Impact on Process Count

### Key Parameters
```postgresql
# Connection limits
max_connections = 100                    # Max backend processes

# Autovacuum
autovacuum = on                         # Enable autovacuum launcher
autovacuum_max_workers = 3              # Max autovacuum workers

# Replication
max_wal_senders = 10                    # Max WAL sender processes

# Parallel processing
max_parallel_workers = 8                # Max parallel workers
max_parallel_workers_per_gather = 2     # Per query limit
```

### Example Calculations
For a busy PostgreSQL server with:
- 50 active connections
- 3 autovacuum workers running
- 2 WAL senders (replication)
- 4 parallel workers (running queries)

**Total Process Count**: 6 (base) + 50 (backends) + 3 (autovacuum) + 2 (wal senders) + 4 (parallel) = **65 processes**

## Troubleshooting Process Issues

### Common Problems
1. **Too Many Connections**: Backends = max_connections
2. **High Autovacuum Activity**: Multiple autovacuum workers running
3. **Replication Lag**: WAL sender processes backed up
4. **Parallel Query Issues**: Many parallel worker processes

### Monitoring Commands
```bash
# Count PostgreSQL processes
pgrep postgres | wc -l

# Group by process type
ps aux | grep postgres | awk '{print $11, $12}' | sort | uniq -c

# Monitor process creation/termination
watch 'ps aux | grep postgres | wc -l'

# Container-specific monitoring (when ps is not available)
# Use /proc filesystem
for pid in $(ls /proc | grep '^[0-9]*$'); do 
  if [ -f /proc/$pid/cmdline ]; then 
    cmdline=$(cat /proc/$pid/cmdline | tr '\0' ' ')
    if echo "$cmdline" | grep -q postgres; then 
      echo "PID $pid: $cmdline"
    fi
  fi
done

# Docker container process monitoring from host
docker exec postgres-demo bash -c "ls /proc | grep '^[0-9]*$' | wc -l"
```

This multi-process architecture provides PostgreSQL with robustness, scalability, and fault isolation, ensuring that issues with one component don't bring down the entire database system.