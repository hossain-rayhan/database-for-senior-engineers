

# Leader Election and Failover with Kubernetes Lease API: Operator-Based and Sidecar-Based Approaches

## Introduction

There are two common patterns for implementing high availability (HA) leader election and failover for PostgreSQL clusters on Kubernetes using the Kubernetes Lease API:

1. **Operator-Based Approach** (as used by CloudNativePG and most K8s-native operators):
	- A central operator (controller) is responsible for monitoring the cluster, performing leader election, and orchestrating failover.
2. **Sidecar-Based Approach** (decentralized):
	- Each PostgreSQL pod includes a sidecar container that participates in leader election using the Lease API and can promote itself to primary if it acquires the lease.

This document describes both approaches, their mechanisms, and their trade-offs.

---



## 1. Operator-Based Approach: CNPG as an example

### Overview


CloudNativePG (CNPG) manages PostgreSQL clusters on Kubernetes, supporting high availability by ensuring that only one pod acts as the primary (leader) at any time, with the rest as replicas. When the instance count is greater than one, CNPG orchestrates automatic failover and switchover to maintain service continuity. This document focuses on the PostgreSQL pod (database cluster) leader election and failover process, not the operator-level Lease API.

### Purpose

- Ensures a single PostgreSQL pod acts as the primary (leader) for write operations.
- Handles automatic failover and switchover between pods.

### Mechanism

#### a. Pod Role Management

- Each PostgreSQL pod runs an "instance manager" process as its main container.
- The operator maintains the desired state of the cluster, including which pod should be primary.
- Pod roles are reflected via Kubernetes labels (e.g., `cnpg.io/role=primary` or `cnpg.io/role=replica`).

#### b. Status Monitoring

- The operator continuously monitors pod health and replication status via an HTTP API exposed by the instance manager.
- The operator updates the `status` field of the `Cluster` CRD, including `CurrentPrimary` and `TargetPrimary`.

#### c. Failover Process (Instance Count > 1)

1. **Detection**: The operator detects primary failure (e.g., pod crash, readiness probe failure, loss of replication).
2. **Election**: The operator selects the most up-to-date, healthy replica as the new primary (based on replication lag and readiness).
3. **Promotion**: The operator instructs the chosen replica (via the instance manager) to promote itself to primary.
4. **Label Update**: The operator updates pod labels so the new primary is labeled as such, and the old primary (if it returns) is demoted to replica.
5. **Service Routing**: The Kubernetes Service for the primary (e.g., `<cluster>-rw`) automatically routes traffic to the new primary pod based on labels.

#### d. Switchover

- Switchover is a planned role change, typically for maintenance.
- The operator follows a similar process as failover but is triggered by user action.
- The current primary is demoted, and a selected replica is promoted.

### How New Requests Are Routed to the New Primary

- Each CNPG cluster creates a Kubernetes Service (e.g., `<cluster>-rw`) for client connections to the primary.
- This Service uses a label selector (e.g., `cnpg.io/role=primary`) to select the pod that is currently the primary.
- After failover, the operator updates pod labels so the new primary pod has the `primary` label.
- Kubernetes automatically updates the Service endpoints to point to the new primary pod.
- Applications always connect to the Service, which always routes to the current primary—no client change is needed.


### How the Old Primary Becomes a Replica

- When a new primary is promoted, the operator instructs the old primary (if reachable) to demote itself to a replica via the instance manager.
- The operator updates the pod’s label from `primary` to `replica`.
- The instance manager reconfigures PostgreSQL to follow the new primary and resumes streaming replication as a standby.
- The old primary pod is no longer selected by the primary Service and will not receive new write requests.

#### Code Reference

- Main reconciliation logic: `internal/controller/cluster_controller.go`
- Pod label updates: `pkg/reconciler/instance/metadata.go` (`updateRoleLabels`)
- Pod spec construction: `pkg/specs/pods.go` (`createPostgresContainers`)
- Instance manager: Main process in the PostgreSQL container, started via `/controller/manager instance run`


### Kubernetes Lease API vs. Cluster Failover

- **Operator-level leader election** uses the Lease API to coordinate operator pods.
- **Cluster-level failover** is managed by the operator, not by the Lease API, but relies on Kubernetes primitives (labels, Services, readiness/liveness probes) for orchestration.

#### Split-Brain and Network Partition Considerations

While CNPG is designed to minimize the risk of split-brain (multiple primaries) and issues caused by network partitions at the PostgreSQL pod level, some risk remains because there is no distributed consensus protocol between pods. If the operator loses connectivity to the current primary (e.g., due to a network partition), it may promote a new primary from the available replicas. If the old primary later becomes reachable, there is a brief window where two pods could both believe they are primary (split-brain). CNPG mitigates this by demoting the old primary when it returns, having the instance manager in each pod regularly check and enforce its assigned role, and waiting for pods to acknowledge their new roles before fully completing failover or switchover. Absolute prevention of split-brain is not possible in all network partition scenarios, but the window and impact are minimized by these mechanisms.


### Key Points

- Only one operator pod is active at a time (Lease API).
- Only one PostgreSQL pod is primary at a time (operator logic).
- Pod labels and Kubernetes Services are used for traffic routing.
- The instance manager process in each pod enables fine-grained control and status reporting.
- Failover is automatic and transparent to clients using the Service endpoint.

---


## 2. Sidecar-Based Approach: Decentralized Lease-Based Leader Election

### Purpose

- Achieve PostgreSQL HA by having each pod participate directly in leader election and failover, without a central operator making all decisions.

### Mechanism

#### a. Lease Ownership by Sidecar

- Each PostgreSQL pod includes a sidecar container responsible for leader election.
- All sidecars compete for a shared Lease object in the cluster (using the Kubernetes Lease API).
- At any time, only one pod's sidecar should own the lease.

#### b. Leader Responsibilities

- The pod whose sidecar holds the lease acts as the failover coordinator.
- It queries the replication status (e.g., replication slots, WAL positions) of all pods.
- It selects the replica with the minimal replication lag and promotes it to primary.
- If the sidecar's own pod is the best candidate, it promotes its own PostgreSQL instance.

#### c. Demotion and Fencing

- If a pod loses the lease, its sidecar must immediately demote its PostgreSQL instance to a replica.
- This minimizes the risk of split-brain (multiple primaries).

#### d. Service Routing

- As with the operator-based approach, Kubernetes Services use pod labels (e.g., `cnpg.io/role=primary`) to route client requests to the current primary.
- The sidecar updates the pod's labels as roles change.

### Trade-Offs and Considerations

**Advantages:**
- Decentralizes failover logic, reducing reliance on a single operator/controller pod.
- Can provide faster failover in some scenarios, as each pod can react independently to lease changes.
- May be simpler to reason about in small clusters or environments where operator privileges are restricted.

**Disadvantages:**
- Increased risk of split-brain or multiple primaries if lease handling or fencing is not robust, since the Kubernetes Lease API is not a full consensus system.
- More complex fencing and demotion logic required in each pod/sidecar to avoid data corruption.
- Harder to coordinate planned switchovers or maintenance, as there is no central authority.
- Less common in the Kubernetes ecosystem, so less community support and fewer reference implementations.

**Operational Considerations:**
- Requires careful implementation of lease acquisition, renewal, and loss handling in the sidecar.
- Monitoring and alerting must be distributed across all pods, not just the operator.
- Upgrades and configuration changes may be more difficult to coordinate.





