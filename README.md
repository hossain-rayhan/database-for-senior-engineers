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

### Database on Kubernetes
- **[Leader Election and Failover with K8s](data-on-k8s/leader_election_and_failover_with_k8s.md)**: Patterns and best practices for high availability

