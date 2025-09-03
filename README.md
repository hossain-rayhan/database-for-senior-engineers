# Database Internals for Senior Engineers

A comprehensive learning repository exploring distributed database concepts, internals, and implementations with practical examples from PostgreSQL and MongoDB.

## 🎯 Purpose

This repository serves as a knowledge base for senior engineers diving into database internals, particularly focusing on distributed systems concepts. It contains terminology, proof-of-concepts, and practical examples that bridge the gap between theoretical knowledge and real-world implementations.

## 📚 Topics Covered

### PostgreSQL Deep Dive
- **Architecture**: Process model, shared memory, and storage layout
- **[Connection Architecture](postgres/connection.md)**: Client/server model, process forking, and socket management
- **[Process Architecture](postgres/process.md)**: Multi-process model, background processes, and monitoring
- **[Engine Architecture](postgres/postgres-engine.md)**: Core engine components, query processing, storage, and MVCC internals
- **[Storage Internals](postgres/storage-internals.md)**: Physical storage, page structure, tuple format, and space management
- **[Write-Ahead Logging (WAL) Deep Dive](postgres/wal.md)**: WAL record structure, LSNs, checkpoints, replication & hands-on inspection
- **[VACUUM & ANALYZE (Autovacuum, Space Reuse, Planner Stats)](postgres/vacuum_and_analyze.md)**: Dead tuple lifecycle, XID wraparound prevention, statistics maintenance, hands-on lab
- **[Indexes (Design, Types, Practical Labs)](postgres/index.md)**: B-Tree fundamentals, specialized index types (GIN/GiST/BRIN/etc.), design trade-offs, and hands-on recipes

### Distributed Database Concepts
- **[Global Strong Consistency with PostgreSQL](distributed-database-concepts/global-strong/global_strong.md)**: Synchronous replication, zero data loss guarantees, and practical Kubernetes deployment

### Database on Kubernetes
- **[Leader Election and Failover with K8s](data-on-k8s/leader_election_and_failover_with_k8s.md)**: Patterns and best practices for high availability

### AI and Database
- **[Vector Databases: A Beginner’s Guide](ai_and_data/vector_database.md)**: Introduction to vector databases, their role in AI/LLM workloads, and practical examples

