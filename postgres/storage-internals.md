# PostgreSQL Storage Internals

## Overview

PostgreSQL's storage system is engineered for reliability, performance, and ACID compliance. Understanding how data is physically organized on disk is crucial for performance optimization, troubleshooting, and making informed design decisions. This document explores the storage internals from the file system level down to individual tuple storage.

## Physical Storage Hierarchy

PostgreSQL organizes data in a clear hierarchy from the operating system level down to individual data items:

```
Operating System
├── Data Directory (PGDATA)
    ├── Tablespaces
        ├── Databases
            ├── Relations (Tables/Indexes)
                ├── Relation Files (1GB segments)
                    ├── Pages (8KB blocks)
                        ├── Tuples (Variable length)
```

### Data Directory Structure

The PostgreSQL data directory (typically `/var/lib/postgresql/data`) contains the complete database cluster:

**Essential Directories:**
- **`base/`**: Contains subdirectories for each database by OID
- **`global/`**: Cluster-wide tables (pg_database, pg_authid, etc.)
- **`pg_wal/`**: Write-Ahead Log files
- **`pg_tblspc/`**: Symbolic links to tablespace locations
- **`pg_stat/`**: Statistics files for the stats collector
- **`pg_multixact/`**: Multitransaction status data

**Key Configuration Files:**
- **`postgresql.conf`**: Main configuration file
- **`pg_hba.conf`**: Host-based authentication rules
- **`pg_ident.conf`**: User name mapping rules
- **`PG_VERSION`**: PostgreSQL version number

### Tablespace Organization

Tablespaces provide a way to define locations in the file system where database objects can be stored.

**Default Tablespaces:**
- **`pg_default`**: Default location for user databases and objects
- **`pg_global`**: System catalog tables shared across all databases

**Custom Tablespaces:**
Allow spreading data across multiple storage devices for performance or capacity reasons. Each tablespace creates a directory structure that mirrors the default layout.

### Database Storage Layout

Each database is stored in a subdirectory within the base directory, named by its OID (Object Identifier).

**Database Directory Contents:**
- **Relation Files**: Each table and index is stored in one or more files
- **Visibility Map Files**: Track which pages have only visible tuples
- **Free Space Map Files**: Track available space in each page
- **Initialization Fork**: Contains initialization data for unlogged tables

## Relation File Management

PostgreSQL stores each table and index as one or more operating system files, with sophisticated naming conventions and size management.

### File Naming Convention

**Base Filename**: Uses the relation's OID (Object Identifier)
- Table OID 16384 → file named `16384`
- Index OID 16385 → file named `16385`

**File Extensions for Different Forks:**
- **Main Fork** (no extension): The primary data storage
- **Free Space Map** (`.fsm`): Tracks available space per page
- **Visibility Map** (`.vm`): Tracks all-visible and all-frozen pages
- **Initialization Fork** (`.init`): For unlogged tables

### File Segmentation

PostgreSQL limits individual files to 1GB to avoid file system limitations and improve manageability.

**Segmentation Strategy:**
- **First Segment**: Named with the base OID (e.g., `16384`)
- **Additional Segments**: Numbered sequentially (`16384.1`, `16384.2`, etc.)
- **Automatic Management**: PostgreSQL transparently handles segment boundaries

**Benefits of Segmentation:**
- Compatibility with file systems that have size limits
- Easier backup and recovery of individual segments
- Better performance on some file systems
- Simplified file system operations

### Relation Size Monitoring

Understanding file sizes helps with capacity planning and performance analysis:

```sql
-- Check table and index sizes
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as total_size,
    pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) as table_size,
    pg_size_pretty(pg_indexes_size(schemaname||'.'||tablename)) as indexes_size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Check individual file segments
SELECT 
    pg_relation_filepath('your_table_name') as file_path;
```

## Page Structure Deep Dive

Pages are the fundamental unit of storage I/O, designed for efficiency and reliability.

### Page Header Anatomy

Every page begins with a 24-byte header containing critical metadata:

**Page Header Components:**
- **LSN (Log Sequence Number)**: 8 bytes - Points to the last WAL record that modified this page
- **Checksum**: 2 bytes - CRC checksum for corruption detection (if enabled)
- **Flags**: 2 bytes - Page-level flags (has free line pointers, is all-visible, etc.)
- **Lower/Upper Pointers**: 4 bytes - Define the boundaries of free space
- **Special Space Pointer**: 2 bytes - Start of index-specific data
- **Page Size and Version**: 2 bytes - Page format identifier
- **Prune XID**: 4 bytes - Oldest transaction that might have left dead tuples

### Item Pointer Array (Line Pointers)

The item pointer array provides indirection between logical and physical tuple locations.

**Item Pointer Structure (4 bytes each):**
- **Offset**: 15 bits - Byte offset to tuple within page
- **Length**: 15 bits - Length of tuple in bytes  
- **Flags**: 2 bits - LP_NORMAL, LP_REDIRECT, LP_DEAD, LP_UNUSED

**Indirection Benefits:**
- **Tuple Movement**: Tuples can be moved within a page without changing their logical address
- **Update Chains**: Supports HOT (Heap-Only Tuples) updates
- **Space Reclamation**: Dead space can be reclaimed during page cleanup
- **Defragmentation**: Pages can be compacted without changing tuple identifiers

### Free Space Management

PostgreSQL uses a sophisticated approach to track and manage free space within pages.

**Free Space Organization:**
- **Item Pointers**: Grow downward from the beginning of the page
- **Tuple Data**: Grows upward from the end of the page
- **Free Space**: The gap between item pointers and tuple data
- **Special Space**: Reserved area at the end for index-specific data

**Space Allocation Strategy:**
1. **New Tuple Insertion**: Check if free space is sufficient
2. **Space Compaction**: Remove gaps between tuples if needed
3. **Page Split**: Create new page if current page cannot accommodate
4. **Free Space Map Update**: Record available space for future insertions

### Page-Level Checksums

Page checksums detect data corruption during storage or transmission.

**Checksum Calculation:**
- Computed over the entire page except the checksum field itself
- Uses FNV-1a hash algorithm for speed and effectiveness
- Calculated during page writes and verified during reads

**Corruption Detection:**
- **Read-Time Verification**: Every page read verifies the checksum
- **Automatic Error Reporting**: Checksum failures are logged and reported
- **Data Recovery**: Corruption detection enables targeted backup restoration

## Tuple Storage Format

Tuples are variable-length structures that efficiently store row data while supporting MVCC and other PostgreSQL features.

### Tuple Header Structure

Each tuple begins with a header containing essential metadata for MVCC and system operations.

**Standard Tuple Header (23 bytes minimum):**
- **Transaction IDs**: 8 bytes (XMIN + XMAX) for MVCC visibility
- **Command IDs**: 8 bytes (CMIN + CMAX) for statement-level consistency  
- **CTID**: 6 bytes (Block number + Item offset) for physical location
- **Info Mask**: 2 bytes - Flags indicating tuple state and characteristics
- **Attribute Count**: 1 byte - Number of columns (with flags)
- **Header Length**: 1 byte - Variable header size for optimization

**MVCC Transaction Information:**
- **XMIN**: Transaction ID that inserted this tuple version
- **XMAX**: Transaction ID that deleted or updated this tuple
- **CMIN/CMAX**: Command sequence within the creating/deleting transaction

### Variable-Length Attributes

PostgreSQL efficiently handles columns of varying sizes through sophisticated encoding.

**Attribute Storage Methods:**
- **Fixed-Length**: Stored inline (integers, fixed-length strings)
- **Variable-Length Short**: Stored inline with 1-byte length header
- **Variable-Length Long**: Stored inline with 4-byte length header
- **TOAST Storage**: Large values stored in separate TOAST table

**NULL Value Handling:**
- **NULL Bitmap**: Bit array indicating which columns are NULL
- **Space Efficiency**: NULL values consume no storage space beyond the bitmap
- **Alignment**: NULLs don't affect column alignment requirements

### TOAST (The Oversized-Attribute Storage Technique)

TOAST handles large column values that don't fit comfortably in normal pages.

**TOAST Strategies:**
1. **PLAIN**: Never TOAST this attribute (for fixed-length types)
2. **EXTENDED**: Allow compression and out-of-line storage
3. **EXTERNAL**: Allow out-of-line storage but not compression
4. **MAIN**: Allow compression but prefer inline storage

**TOAST Table Structure:**
- **Chunk ID**: Identifies the original large value
- **Chunk Sequence**: Order of chunks within the large value
- **Chunk Data**: The actual data chunk (up to ~2000 bytes)

**Compression and Chunking:**
- **LZ Compression**: Applied before chunking for EXTENDED strategy
- **Chunk Size**: Optimized to fit multiple chunks per page
- **Automatic Management**: Transparent to applications and queries

## MVCC Storage Implementation

PostgreSQL's MVCC implementation requires careful storage design to maintain multiple tuple versions efficiently.

### Tuple Versioning

MVCC creates new tuple versions for updates rather than modifying existing data.

**Update Behavior:**
1. **New Tuple Creation**: INSERT creates a new tuple with current transaction's XID as XMIN
2. **Update Processing**: UPDATE creates new tuple version and marks old version with XMAX
3. **Delete Processing**: DELETE marks tuple with XMAX, doesn't physically remove it
4. **Visibility Determination**: Each transaction sees appropriate tuple versions based on its snapshot

**Version Chain Management:**
- **CTID Pointers**: Link updated tuples to their newer versions
- **HOT Updates**: Heap-Only Tuples for updates that don't change indexed columns
- **Version Pruning**: Remove obsolete versions during VACUUM operations

### Dead Tuple Accumulation

MVCC creates dead tuples that need periodic cleanup to maintain performance.

**Sources of Dead Tuples:**
- **Aborted Transactions**: All tuples created by aborted transactions become dead
- **Completed Updates**: Old versions of updated tuples become dead
- **Completed Deletes**: Deleted tuples become dead after all transactions can see the deletion

**Impact of Dead Tuples:**
- **Storage Bloat**: Dead tuples consume disk space until removed
- **Performance Degradation**: Scans must examine dead tuples to determine visibility
- **Index Bloat**: Dead tuples still have index entries that slow index scans

### VACUUM Process

VACUUM is PostgreSQL's garbage collection mechanism for removing dead tuples and reclaiming space.

**VACUUM Operations:**
1. **Dead Tuple Identification**: Scan heap pages to find tuples invisible to all transactions
2. **Index Cleanup**: Remove index entries pointing to dead tuples
3. **Space Reclamation**: Mark freed space as available for new tuples
4. **Statistics Update**: Refresh table statistics for query planning
5. **Visibility Map Update**: Mark pages that contain only visible tuples

**VACUUM Variants:**
- **Regular VACUUM**: Reclaims space within existing pages
- **VACUUM FULL**: Rebuilds table to eliminate all dead space (requires exclusive lock)
- **Autovacuum**: Automatic background VACUUM based on activity thresholds

## Free Space Map (FSM)

The Free Space Map tracks available space in each page to optimize tuple placement.

### FSM Structure

The FSM uses a tree structure to efficiently track free space across all pages.

**FSM Organization:**
- **Leaf Level**: One entry per heap page, storing available free space
- **Internal Levels**: Each internal node stores the maximum free space of its children
- **Root Level**: Single node representing maximum free space in entire relation

**Space Quantization:**
- **256 Categories**: Free space is quantized into 256 levels (0-255)
- **Approximate Values**: Provides good enough precision for space allocation
- **Efficient Storage**: Each page's free space fits in one byte

### FSM Operations

The FSM supports efficient insertion point location and space tracking.

**Space Allocation Process:**
1. **Size Requirement**: Determine space needed for new tuple
2. **FSM Search**: Find pages with sufficient free space
3. **Page Selection**: Choose appropriate page from candidates
4. **FSM Update**: Update free space information after insertion

**Maintenance Operations:**
- **VACUUM Updates**: VACUUM refreshes FSM with current free space information
- **Automatic Updates**: Some operations update FSM incrementally
- **FSM Reconstruction**: Can rebuild entire FSM if corrupted

## Visibility Map (VM)

The Visibility Map tracks which pages contain only tuples visible to all transactions.

### VM Purpose and Benefits

The Visibility Map enables several important optimizations.

**All-Visible Pages:**
- **VACUUM Optimization**: Skip scanning pages that have no dead tuples
- **Index-Only Scans**: Avoid heap access when all tuples are visible
- **Freeze Optimization**: Track pages that need transaction ID freezing

**All-Frozen Pages:**
- **Freeze Avoidance**: Skip pages that don't need transaction ID updates
- **Backup Optimization**: WAL can skip logging certain operations on frozen pages

### VM Maintenance

The Visibility Map requires careful maintenance to ensure accuracy.

**VM Updates:**
- **Page Modification**: Clear VM bit when page is modified
- **VACUUM Verification**: Set VM bit only after confirming all tuples are visible/frozen
- **Crash Recovery**: VM updates are WAL-logged for consistency

**VM Accuracy:**
- **Conservative Approach**: VM bits are cleared on any doubt about page state
- **Lazy Updates**: VM bits are set only during VACUUM operations
- **Performance Impact**: Incorrect VM state affects performance but not correctness

## Storage Performance Considerations

Understanding storage internals enables better performance optimization decisions.

### Page Fill Factor

The fill factor controls how much space is left free in each page for future updates.

**Fill Factor Impact:**
- **Lower Fill Factor**: More space for HOT updates, less I/O for updates
- **Higher Fill Factor**: Better space utilization, more tuples per page
- **Default Value**: 100% for most tables, 90% for frequently updated tables

**HOT Updates:**
- **Same Page Updates**: Updates that keep new tuple on same page
- **Index Maintenance**: HOT updates don't require index updates
- **Performance Benefit**: Significant reduction in update overhead

### I/O Patterns

Storage layout affects I/O efficiency and overall database performance.

**Sequential vs Random I/O:**
- **Table Scans**: Benefit from sequential page layout
- **Index Scans**: May cause random I/O patterns
- **Clustering**: Physical ordering can improve range query performance

**Buffer Pool Interaction:**
- **Page Replacement**: LRU and clock sweep algorithms affect which pages stay in memory
- **Read-Ahead**: Sequential scans trigger predictive page loading
- **Write Patterns**: Background writer and checkpointer spread write I/O over time

### Storage Monitoring

Key metrics help identify storage-related performance issues.

```sql
-- Monitor table bloat
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) as size,
    n_dead_tup,
    n_live_tup,
    round(n_dead_tup * 100.0 / GREATEST(n_live_tup + n_dead_tup, 1), 2) as dead_tuple_percent
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC;

-- Check free space map effectiveness
SELECT 
    relname,
    pg_relation_size(oid) / 8192 as pages,
    pg_freespace(oid) as avg_free_bytes
FROM pg_class 
WHERE relkind = 'r' AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public');

-- Monitor VACUUM and analyze activity
SELECT 
    schemaname,
    relname,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze,
    vacuum_count,
    autovacuum_count
FROM pg_stat_user_tables;
```

## Advanced Storage Topics

### Unlogged Tables

Unlogged tables provide better performance by skipping WAL logging at the cost of durability.

**Unlogged Table Characteristics:**
- **No WAL Logging**: Changes are not written to WAL
- **Crash Recovery**: Data is lost if the server crashes
- **Replication**: Not replicated to standby servers
- **Performance**: Faster for temporary or easily reconstructed data

### Temporary Tables

Temporary tables exist only for the duration of a database session.

**Temporary Table Storage:**
- **Session-Local**: Each session has its own copy of temporary tables
- **Automatic Cleanup**: Dropped automatically when session ends
- **Reduced Overhead**: Minimal logging and no visibility to other sessions

### Parallel Storage Operations

Modern PostgreSQL supports parallel operations that affect storage access patterns.

**Parallel Vacuum:**
- **Multiple Workers**: VACUUM can use multiple processes for large tables
- **Index Processing**: Parallel index cleanup for tables with many indexes
- **I/O Coordination**: Workers coordinate to avoid I/O conflicts

**Parallel Query Execution:**
- **Shared Buffer Access**: Multiple workers access shared buffer pool
- **I/O Distribution**: Parallel scans distribute I/O load across workers
- **Memory Management**: Work memory is divided among parallel workers

This storage internals knowledge forms the foundation for understanding PostgreSQL's performance characteristics, optimization strategies, and operational requirements. The physical storage design directly impacts query performance, maintenance operations, and system scalability.
