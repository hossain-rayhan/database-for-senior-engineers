# PostgreSQL Connection Architecture

## Overview
PostgreSQL follows a **client/server architecture** where client applications connect to a database server to perform operations. The server side uses a multi-process architecture where the main `postmaster` process manages client connections by forking dedicated backend processes. Each client connection gets its own private backend process, ensuring isolation and concurrent processing.

### Client/Server Model
- **Server Side**: The PostgreSQL database server (`postgres`) manages database files, accepts connections, and performs database operations
- **Client Side**: Client applications (psql, web apps, ORMs) connect to the server to execute queries and retrieve data
- **Communication**: Client and server communicate over TCP/IP connections (can be local or remote)

## Process Architecture

### 1. Postmaster Process (Main Server)
- **Role**: The supervisor process that manages the entire PostgreSQL instance
- **Responsibilities**:
  - Listens for incoming client connections on the configured port (default 5432)
  - Forks new backend processes for each client connection
  - Manages shared memory and background processes
  - Handles authentication and connection establishment
- **Lifecycle**: Always running, waiting for client connections

### 2. Backend Processes (Per-Connection)
- **Creation**: Each client connection triggers a fork of the postmaster process
- **Isolation**: Each backend process is completely separate with its own memory space
- **Communication**: Direct communication with assigned client, no intervention from postmaster

```
Client 1 ──TCP──┐
               │
Client 2 ──TCP──┤──► Postmaster ──fork──► Backend 1 (Client 1)
               │    (Port 5432)    ├──► Backend 2 (Client 2)
Client 3 ──TCP──┘                  └──► Backend 3 (Client 3)
```

## Memory Architecture

### Private Memory (Per Backend Process)
Each backend process has its own private memory space containing:
- **Work Memory**: For sorting, hashing, and temporary operations
- **Local Buffers**: Private buffer cache for temporary tables
- **Connection State**: Session variables, prepared statements, cursors
- **Query Execution Context**: Query plans, intermediate results

### Shared Memory (Global)
All backend processes share:
- **Shared Buffer Pool**: Main buffer cache for data pages
- **WAL Buffers**: Write-Ahead Log buffers (shared among all backends)
- **Lock Tables**: Shared locks and synchronization primitives
- **Statistics**: Query statistics and system metrics

## Connection Establishment Flow

### Step-by-Step Process
1. **Client Request**: Client sends connection request to port 5432
2. **Postmaster Accepts**: Postmaster process accepts the TCP connection
3. **Authentication**: Postmaster handles authentication (pg_hba.conf)
4. **Backend Fork**: Postmaster forks a new backend process
5. **Handoff**: Connection ownership transfers to the new backend process
6. **Direct Communication**: Client and backend communicate directly

### Socket Management
- **Socket Identifier**: Each connection gets a unique socket file descriptor
- **Inheritance**: Forked backend process inherits the socket from postmaster
- **Ownership**: Backend process takes full ownership of the socket

#### How Direct Socket Communication Works

In Unix-like systems, a socket is represented by a **file descriptor (FD)** - simply an integer that the OS uses to track the connection. Here's the technical breakdown:

**Socket Uniqueness (4-tuple)**
Each connection is uniquely identified by:
```
(client_ip, client_port, server_ip, server_port)
```
Example connections to the same PostgreSQL server:
```
Client 1: (192.168.1.10, 54321, 192.168.1.100, 5432)
Client 2: (192.168.1.11, 54322, 192.168.1.100, 5432)
Client 3: (192.168.1.10, 54323, 192.168.1.100, 5432)
```

**File Descriptor Inheritance**
1. **Postmaster** accepts connection and gets socket FD (e.g., FD=7)
2. **Fork** creates backend process - socket FD is inherited
3. **Backend** takes ownership of FD=7 for that specific client
4. **OS kernel** routes packets to the correct process based on the 4-tuple

**Direct Communication Flow**
```
Client sends packet → OS Kernel → Checks 4-tuple → Routes to Backend Process (FD=7)
                                                   ↓
                                          Backend reads from FD=7
                                          Backend writes to FD=7
                                                   ↓
                                          OS Kernel → Routes back to Client
```

**Why Postmaster Isn't Involved**
- Once forked, the backend process **owns the socket FD**
- OS kernel directly routes packets to the backend process
- Postmaster only listens for **new connections** on port 5432
- Existing connections bypass postmaster entirely

## Subsequent Requests

### Request Routing
- **Direct Connection**: Once established, client sends all requests directly to its assigned backend process
- **No Postmaster Involvement**: Postmaster doesn't handle subsequent requests
- **Socket Reuse**: Same socket used for the entire session duration

### Connection Lifecycle
```
Client connects ──► Postmaster ──► Fork Backend ──► Direct Communication
                                      │
                                      └──► All subsequent requests
```

## Key Technical Details

### Process Isolation Benefits
- **Crash Isolation**: One backend crash doesn't affect others
- **Security**: Each connection has separate memory space
- **Scalability**: True parallel processing of queries

### Memory Sharing Strategy
- **Read-Only Shared Data**: Catalog cache, system tables
- **Write-Shared Data**: WAL buffers, buffer pool, locks
- **Private Data**: Query execution state, temporary results

### Performance Implications
- **Fork Overhead**: Creating new process has higher overhead than threads
- **Memory Efficiency**: Shared memory reduces overall memory usage
- **Concurrency**: True parallelism through separate processes

## Common Misconceptions Clarified

❌ **Myth**: All connections share the same memory
✅ **Reality**: Each connection has private memory + shared components

❌ **Myth**: Each connection has its own WAL buffer
✅ **Reality**: Single shared WAL buffer for all connections

❌ **Myth**: Postmaster handles all client requests
✅ **Reality**: Postmaster only handles connection establishment

❌ **Myth**: WAL flushing is done by a single dedicated process
✅ **Reality**: Any backend process can flush WAL buffers

## Monitoring Connection Architecture

### Useful Queries
```sql
-- View active connections and their backend processes
SELECT pid, usename, application_name, client_addr, state 
FROM pg_stat_activity;

-- Check shared memory usage
SELECT * FROM pg_shmem_allocations;

-- Monitor WAL activity
SELECT * FROM pg_stat_wal;
```

### System-Level Monitoring
```bash
# View PostgreSQL processes
ps aux | grep postgres

# Check shared memory segments
ipcs -m

# Monitor network connections
netstat -an | grep 5432
```

## Configuration Parameters

### Connection-Related Settings
- `max_connections`: Maximum concurrent connections
- `shared_buffers`: Size of shared buffer pool
- `wal_buffers`: Size of WAL buffer (shared)
- `work_mem`: Per-operation memory limit (private)

This architecture ensures PostgreSQL can handle thousands of concurrent connections while maintaining data integrity and performance through its robust process model and shared memory design.