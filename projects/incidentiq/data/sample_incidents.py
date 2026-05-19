"""Curated sample incident corpus used to seed the knowledge base during local development.

This module is the entire seed knowledge base for the IncidentIQ RAG system. It
exposes two primary collections — `INCIDENTS` (30 detailed postmortems across
six categories) and `SOPS` (10 runbook documents) — plus a combined
`ALL_DOCUMENTS` export and accessor helpers used by the ingestion pipeline.

Every record is hand-written to be technically accurate enough that the
retriever surfaces concrete, actionable guidance (real commands, log lines, and
configuration knobs) when grounding LLM answers.
"""

from __future__ import annotations

INCIDENTS: list[dict] = [
    # ────────────────────────────────────────────────────────────────────────
    # CATEGORY 1 — DATABASE
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "INC-001",
        "title": "PostgreSQL Primary Node Connection Pool Exhaustion",
        "severity": "P1",
        "category": "Database",
        "tags": ["postgresql", "pgbouncer", "connection-pool", "production", "p1"],
        "description": (
            "At 14:22 UTC the primary PostgreSQL 14 cluster (db-prod-1) began rejecting new "
            "connections with 'FATAL: sorry, too many clients already'. PgBouncer pool fill "
            "rate hit 100% and cl_waiting climbed past 800. Checkout, search, and account APIs "
            "returned 503 for ~18% of requests for 22 minutes; revenue impact estimated at "
            "$42K. Detected by Datadog monitor 'pg.connections.utilization > 90%' which paged "
            "the DBOC on-call."
        ),
        "triage_steps": [
            "Run: psql -h db-prod-1 -U postgres -c \"SELECT count(*), state FROM pg_stat_activity GROUP BY state;\"",
            "Connect to PgBouncer admin and run: SHOW POOLS; SHOW CLIENTS; SHOW SERVERS;",
            "Identify long-running idle-in-transaction sessions: SELECT pid, usename, application_name, state, NOW()-xact_start AS xact_age, query FROM pg_stat_activity WHERE state='idle in transaction' ORDER BY xact_age DESC LIMIT 20;",
            "Check max_connections vs current usage: SHOW max_connections; SELECT count(*) FROM pg_stat_activity;",
            "Inspect PgBouncer config: cat /etc/pgbouncer/pgbouncer.ini | grep -E 'pool_mode|default_pool_size|max_client_conn'",
            "Tail PgBouncer logs for 'closing because' and 'no more connections': journalctl -u pgbouncer -n 500 --no-pager",
            "Correlate with application deploys via: kubectl rollout history deployment/analytics-api -n analytics",
            "Capture pg_locks snapshot: SELECT locktype, mode, count(*) FROM pg_locks GROUP BY locktype, mode ORDER BY count DESC;",
        ],
        "root_cause": (
            "A scheduled analytics ETL deployed in release v3.8.0 opened explicit transactions "
            "but never committed when its downstream Kafka producer raised an exception. "
            "Connections were returned to PgBouncer as 'idle in transaction', exhausting the "
            "200-slot transaction pool within 14 minutes of the ETL trigger."
        ),
        "resolution_steps": [
            "Terminate offending idle-in-transaction sessions: SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction' AND NOW()-xact_start > interval '5 minutes';",
            "Temporarily raise PgBouncer default_pool_size from 200 to 350 and reload: pgbouncer -R /etc/pgbouncer/pgbouncer.ini",
            "Roll back analytics-api to v3.7.4: kubectl rollout undo deployment/analytics-api -n analytics --to-revision=42",
            "Set statement_timeout=30s and idle_in_transaction_session_timeout=120s on the analytics DB role: ALTER ROLE analytics_etl SET idle_in_transaction_session_timeout = '120s';",
            "Verify pool utilisation drops below 60% in Datadog dashboard 'PostgreSQL — Connections'",
            "Deploy v3.8.1 with try/finally guard ensuring conn.rollback() and conn.close() in producer exception path",
            "Restore default_pool_size to 200 once new build is stable for 30 minutes",
            "Add Datadog monitor 'pg.idle_in_transaction.duration > 60s' paging DBOC at warning threshold",
        ],
        "sop_reference": "SOP-DB-001",
        "mttr_minutes": 28,
        "lessons_learned": (
            "Application code must guarantee transaction termination on every exception path; "
            "connection pools are a hard ceiling, not a soft target. Add idle_in_transaction "
            "timeouts at the role level so a single buggy service cannot consume the cluster."
        ),
        "related_incidents": ["INC-004", "INC-016"],
    },
    {
        "id": "INC-002",
        "title": "MySQL Replication Lag Exceeding 30 Minutes",
        "severity": "P2",
        "category": "Database",
        "tags": ["mysql", "replication", "lag", "read-replica", "p2"],
        "description": (
            "Read-replica mysql-replica-2 (MySQL 8.0.34) drifted from Seconds_Behind_Master=2 "
            "to 1,847 seconds (~30m48s) between 02:10 and 02:48 UTC. Customer-facing search and "
            "reporting endpoints reading from the replica started returning stale data older "
            "than 25 minutes. Detected by PMM alert 'mysql_replica_lag > 600s'."
        ),
        "triage_steps": [
            "Run on replica: SHOW REPLICA STATUS\\G and capture Seconds_Behind_Source, Replica_IO_Running, Replica_SQL_Running, Last_Errno",
            "Confirm IO thread is healthy: SHOW PROCESSLIST; look for 'Waiting for source to send event'",
            "Identify the in-flight SQL apply: SELECT * FROM performance_schema.replication_applier_status_by_worker;",
            "Check binlog position skew: on source SHOW MASTER STATUS; on replica compare Exec_Source_Log_Pos vs Read_Source_Log_Pos",
            "Inspect the long transaction: pt-query-digest --since '30m' /var/log/mysql/mysql-bin.* | head -50",
            "Verify replica disk I/O is not saturated: iostat -xz 2 5 (look for %util on the data volume)",
            "Check parallel-apply settings: SHOW VARIABLES LIKE 'replica_parallel%';",
            "Capture row counts touched by the offending statement via: mysqlbinlog --base64-output=DECODE-ROWS -v <binlog> | grep -c 'UPDATE\\|DELETE'",
        ],
        "root_cause": (
            "A single-threaded SQL apply was processing a 1.2-billion-row UPDATE issued by a "
            "monthly retention job (DELETE/UPDATE in one transaction without batching). "
            "replica_parallel_workers was set to 1 and replica_parallel_type=DATABASE, so the "
            "logical clock could not parallelise commits on the same schema."
        ),
        "resolution_steps": [
            "On replica, stop replication safely: STOP REPLICA SQL_THREAD;",
            "Switch to LOGICAL_CLOCK based parallel apply: SET GLOBAL replica_parallel_type='LOGICAL_CLOCK'; SET GLOBAL replica_parallel_workers=8; SET GLOBAL replica_preserve_commit_order=ON;",
            "Restart SQL thread: START REPLICA SQL_THREAD;",
            "Monitor catch-up every 60s: pt-heartbeat --check h=mysql-replica-2 -u monitor",
            "On the primary, rewrite the retention job to delete in 10K-row chunks with sleep: while [ true ]; do mysql -e \"DELETE FROM events WHERE created_at < NOW()-INTERVAL 90 DAY LIMIT 10000\"; sleep 0.5; done",
            "Persist parallel-apply config in /etc/mysql/conf.d/replication.cnf and reboot the replica during the next maintenance window",
            "Re-enable replica in HAProxy backend once Seconds_Behind_Source < 5 for 5 minutes: echo 'enable server mysql_replicas/mysql-replica-2' | socat - /var/run/haproxy.sock",
        ],
        "sop_reference": "SOP-DB-002",
        "mttr_minutes": 74,
        "lessons_learned": (
            "Bulk DML against replicated tables must be chunked. Always enable LOGICAL_CLOCK "
            "parallel apply on read-replicas with non-trivial write volume; the default "
            "single-threaded apply is a latent P2 waiting to fire."
        ),
        "related_incidents": ["INC-005"],
    },
    {
        "id": "INC-003",
        "title": "MongoDB Atlas Disk Space at 95% Capacity",
        "severity": "P2",
        "category": "Database",
        "tags": ["mongodb", "atlas", "disk-space", "wiredtiger", "p2"],
        "description": (
            "MongoDB Atlas M40 cluster 'analytics-prod' triggered the 95% disk utilisation "
            "alert at 09:14 UTC; data volume at 1.42 TB of 1.5 TB provisioned. Growth rate "
            "measured at 8 GB/hour. At current trajectory the cluster would hit 100% in ~10 "
            "hours, after which writes would fail with NotWritablePrimary. No customer impact "
            "yet; detected by Atlas built-in alert 'Disk space % used > 95'."
        ),
        "triage_steps": [
            "From Atlas UI → Metrics → 'Disk Space Used' confirm the trajectory and per-node usage",
            "Connect via mongosh and run: db.stats() and db.runCommand({dbStats: 1, scale: 1024*1024*1024})",
            "List collections by size: db.getCollectionNames().map(c => ({name: c, size: db[c].stats().size, indexSize: db[c].stats().totalIndexSize}))",
            "Identify largest collection's growth driver: db.events_raw.stats() and compare to last week's snapshot",
            "Check existing TTL indexes: db.events_raw.getIndexes().filter(i => i.expireAfterSeconds !== undefined)",
            "Inspect oplog size and window: rs.printReplicationInfo()",
            "Check WiredTiger cache pressure: db.serverStatus().wiredTiger.cache",
            "Verify no in-progress compact or initial sync: db.currentOp({\"command.compact\": {$exists: true}})",
        ],
        "root_cause": (
            "A 2024-Q4 product release added high-cardinality event logging to the events_raw "
            "collection without a TTL index. Documents had been retained indefinitely for 11 "
            "weeks, accumulating ~640 GB of data that the analytics product only queries for "
            "the last 30 days."
        ),
        "resolution_steps": [
            "Open Atlas → Cluster → Configuration → scale storage from 1.5 TB to 2 TB (online, no downtime)",
            "Once storage scaled, create TTL index in background: db.events_raw.createIndex({created_at: 1}, {expireAfterSeconds: 2592000, background: true, name: 'ttl_30d'})",
            "Monitor TTL monitor progress: db.runCommand({serverStatus: 1}).metrics.ttl",
            "Verify deletions begin within 60 seconds: db.events_raw.find({created_at: {$lt: new Date(Date.now()-30*86400*1000)}}).count() should trend to 0",
            "After 24 hours, run compact on secondary nodes one at a time to reclaim disk: db.runCommand({compact: 'events_raw'})",
            "Step down primary and repeat compact on the former-primary",
            "Add Atlas alert 'Disk space % used > 75%' as a leading indicator with PagerDuty integration",
            "File data-retention review ticket DR-2024-118 to audit all collections lacking TTL or archival policy",
        ],
        "sop_reference": "SOP-DB-001",
        "mttr_minutes": 95,
        "lessons_learned": (
            "Every collection that captures time-series or event data must ship with a TTL "
            "policy or external archival job. Disk-space alerts at 75% give roughly 10x the "
            "headroom to respond compared to the default 90%/95% thresholds."
        ),
        "related_incidents": ["INC-025"],
    },
    {
        "id": "INC-004",
        "title": "PostgreSQL Deadlock Storm Causing Application Timeouts",
        "severity": "P1",
        "category": "Database",
        "tags": ["postgresql", "deadlock", "locking", "checkout", "p1"],
        "description": (
            "Between 11:08 and 11:34 UTC, the checkout-service logged 1,247 deadlock_detected "
            "errors against db-prod-1 PostgreSQL 14, up from a baseline of ~3/day. p99 latency "
            "on POST /checkout climbed from 180ms to 14s and 6.4% of orders failed with HTTP "
            "500. Triggered by Sentry 'deadlock_detected' fingerprint exceeding 100/minute."
        ),
        "triage_steps": [
            "Capture active locks: SELECT pid, locktype, mode, granted, relation::regclass, query FROM pg_locks JOIN pg_stat_activity USING (pid) WHERE NOT granted ORDER BY pid;",
            "Identify blocking chains: SELECT blocked.pid AS blocked_pid, blocking.pid AS blocking_pid, blocked.query AS blocked_query, blocking.query AS blocking_query FROM pg_stat_activity blocked JOIN pg_stat_activity blocking ON blocking.pid = ANY(pg_blocking_pids(blocked.pid));",
            "Grep PostgreSQL logs for deadlock detail: sudo grep -A 20 'deadlock detected' /var/log/postgresql/postgresql-14-main.log | tail -200",
            "Correlate with recent deploy: kubectl rollout history deployment/checkout-service -n commerce",
            "Diff the offending transaction code: git diff v4.2.0..v4.2.1 -- services/checkout/src/order_repo.py",
            "Check lock_timeout and deadlock_timeout: SHOW lock_timeout; SHOW deadlock_timeout;",
            "Count deadlock pairs by table: grep 'deadlock detected' /var/log/postgresql/*.log | awk -F'relation' '{print $2}' | sort | uniq -c | sort -rn",
            "Inspect Sentry breadcrumbs for the failing transaction order on a sample event",
        ],
        "root_cause": (
            "checkout-service v4.2.1 introduced an inventory_holds update that locked rows in "
            "the order (orders → inventory_holds) opposite to the existing fulfilment job "
            "(inventory_holds → orders). Concurrent traffic produced classic ABBA deadlocks "
            "that PostgreSQL's deadlock detector resolved by aborting one side."
        ),
        "resolution_steps": [
            "Roll back checkout-service to v4.2.0: kubectl rollout undo deployment/checkout-service -n commerce",
            "Confirm deadlock rate drops to zero in Sentry within 5 minutes",
            "Author hotfix v4.2.2 that always locks rows in canonical order: SELECT … FROM orders WHERE id = $1 FOR UPDATE; then SELECT … FROM inventory_holds WHERE order_id = $1 FOR UPDATE;",
            "Add idempotent retry with exponential backoff for SQLSTATE 40P01 in the repository layer (max 3 attempts)",
            "Run EXPLAIN (ANALYZE, BUFFERS) on both transactions to confirm matching access paths",
            "Deploy v4.2.2 to canary (5% traffic) for 30 minutes, monitor pg_stat_database.deadlocks",
            "Promote v4.2.2 cluster-wide once canary is clean for 60 minutes",
            "Open architectural ticket ARCH-411 to document canonical lock ordering for all multi-table transactions",
        ],
        "sop_reference": "SOP-DB-001",
        "mttr_minutes": 41,
        "lessons_learned": (
            "Any transaction touching more than one table must lock rows in a documented, "
            "globally-consistent order. Add a pre-merge static check (custom ruff rule) that "
            "flags multi-table FOR UPDATE blocks for human review."
        ),
        "related_incidents": ["INC-001", "INC-018"],
    },
    {
        "id": "INC-005",
        "title": "MySQL Slow Query Log Showing Full Table Scans",
        "severity": "P3",
        "category": "Database",
        "tags": ["mysql", "slow-query", "indexing", "performance", "p3"],
        "description": (
            "Weekly performance review surfaced that the orders.list_by_customer query had "
            "regressed from p50=12ms to p50=2.4s after the 2024-10-08 schema migration. The "
            "slow_query_log on mysql-prod-1 showed 18,000 entries/day exceeding 1s, almost all "
            "scanning the entire orders table (rows_examined ≈ 14M)."
        ),
        "triage_steps": [
            "Aggregate slow query log: pt-query-digest /var/log/mysql/mysql-slow.log --since=7d --limit=20",
            "EXPLAIN the worst offender: EXPLAIN FORMAT=JSON SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT 50;",
            "Confirm full scan: look for 'type: ALL' and 'rows: 14000000' in the EXPLAIN output",
            "List existing indexes on the table: SHOW INDEX FROM orders;",
            "Compare with the pre-migration schema: git show 2024-10-08:schema/orders.sql",
            "Estimate index size impact: SELECT data_length/1024/1024 AS data_mb, index_length/1024/1024 AS idx_mb FROM information_schema.TABLES WHERE table_name='orders';",
            "Check innodb_buffer_pool utilisation: SHOW ENGINE INNODB STATUS\\G",
        ],
        "root_cause": (
            "Migration 2024-10-08-rename-user-to-customer renamed the column user_id to "
            "customer_id but the accompanying CREATE INDEX statement for idx_customer_id was "
            "lost during a Git rebase. Production therefore ran without the index for 5 weeks."
        ),
        "resolution_steps": [
            "Create the missing index online using pt-online-schema-change to avoid blocking: pt-online-schema-change --alter 'ADD INDEX idx_customer_created (customer_id, created_at DESC)' D=commerce,t=orders --execute",
            "Verify EXPLAIN now uses 'type: ref' and rows examined drops to <100 for the typical query",
            "Re-run pt-query-digest after 24 hours and confirm the query is no longer in the top-20",
            "Author a regression test in tests/db/test_query_plans.py that fails CI if EXPLAIN type for canonical queries becomes ALL or index",
            "Update the schema migration template to include an 'indexes' YAML block reviewed by DBOC",
            "Backport the index to staging and dev clusters via the standard migration pipeline",
            "Document the incident and the new template in the engineering wiki page 'Schema Migrations 101'",
        ],
        "sop_reference": "SOP-DB-002",
        "mttr_minutes": 180,
        "lessons_learned": (
            "Slow-query regressions are usually missing indexes after refactors. A CI check "
            "that EXPLAINs critical queries against a representative dataset catches these "
            "before they ship."
        ),
        "related_incidents": ["INC-002"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # CATEGORY 2 — KUBERNETES / CONTAINER
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "INC-006",
        "title": "Production Pod CrashLoopBackOff — OOMKilled",
        "severity": "P1",
        "category": "Kubernetes",
        "tags": ["kubernetes", "oomkilled", "crashloopbackoff", "jvm", "p1"],
        "description": (
            "All 6 replicas of deployment 'pricing-service' in namespace commerce entered "
            "CrashLoopBackOff at 18:50 UTC, ~4 minutes after rollout of image v2.4.1. Pods "
            "exited with code 137 (OOMKilled) after ~45s. Pricing API returned 503 for 100% of "
            "traffic for 11 minutes; downstream cart and checkout degraded simultaneously. "
            "Detected by Prometheus alert KubePodCrashLooping firing on all pods."
        ),
        "triage_steps": [
            "Confirm the symptom: kubectl get pods -n commerce -l app=pricing-service -o wide",
            "Describe a failing pod and capture exit code: kubectl describe pod pricing-service-7d8c9-xyz -n commerce | grep -A 5 'Last State'",
            "Read previous-container logs: kubectl logs pricing-service-7d8c9-xyz -n commerce --previous --tail=200",
            "Verify reason is OOMKilled: kubectl get pod pricing-service-7d8c9-xyz -n commerce -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}'",
            "Compare memory limit vs JVM Xmx: kubectl get deployment pricing-service -n commerce -o jsonpath='{.spec.template.spec.containers[0].resources}'",
            "Inspect JVM flags inside the image: kubectl debug -it pricing-service-7d8c9-xyz -n commerce --image=busybox -- cat /proc/1/cmdline | tr '\\0' ' '",
            "Diff against previous image manifest: docker image inspect registry.internal/pricing-service:v2.4.0 v2.4.1 | jq '.[].Config.Env'",
            "Check recent code changes: git log v2.4.0..v2.4.1 --oneline --stat services/pricing/",
        ],
        "root_cause": (
            "Image v2.4.1 bumped the base image from eclipse-temurin:17-jre-alpine to "
            "eclipse-temurin:21-jre-alpine, which changed the JVM's default MaxRAMPercentage "
            "behaviour. Combined with an unbounded -Xmx and a 1Gi container limit, the JVM "
            "tried to allocate ~900Mi of heap and overflowed the cgroup memory ceiling within "
            "the first warm-up cache load."
        ),
        "resolution_steps": [
            "Immediately roll back: kubectl rollout undo deployment/pricing-service -n commerce",
            "Verify rollout: kubectl rollout status deployment/pricing-service -n commerce --timeout=120s",
            "Patch the deployment to set memory request=768Mi, limit=1536Mi: kubectl set resources deployment pricing-service -n commerce --requests=memory=768Mi --limits=memory=1536Mi",
            "Update Dockerfile to set explicit JVM heap: ENV JAVA_TOOL_OPTIONS='-XX:MaxRAMPercentage=70 -XX:InitialRAMPercentage=70 -Xss512k'",
            "Build v2.4.2, run JVM warm-up load test locally with the new memory ceiling for 10 minutes",
            "Deploy v2.4.2 to canary (1 pod) and monitor JVM metrics (jvm_memory_used_bytes) for 30 minutes",
            "Roll out to full fleet once canary is steady and no OOMKilled events observed",
            "Add Prometheus alert: container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.85 for 5m",
        ],
        "sop_reference": "SOP-K8S-001",
        "mttr_minutes": 17,
        "lessons_learned": (
            "JVM containers must declare explicit -XX:MaxRAMPercentage or -Xmx that fits "
            "inside the cgroup limit with at least 25% headroom for metaspace, code cache and "
            "native buffers. Base-image bumps require a heap-sizing review."
        ),
        "related_incidents": ["INC-017"],
    },
    {
        "id": "INC-007",
        "title": "Kubernetes Node NotReady — Disk Pressure",
        "severity": "P2",
        "category": "Kubernetes",
        "tags": ["kubernetes", "node-pressure", "disk", "kubelet", "containerd", "p2"],
        "description": (
            "Worker node ip-10-42-3-21.ec2.internal (m5.2xlarge, 100GB gp3) transitioned to "
            "NotReady with taint node.kubernetes.io/disk-pressure at 03:42 UTC. 14 pods were "
            "evicted; 6 of those landed pending due to scheduling pressure on the remaining "
            "nodes. No customer impact but capacity headroom dropped below 15%. Detected by "
            "kube-state-metrics alert KubeNodeNotReady."
        ),
        "triage_steps": [
            "Confirm node status: kubectl get nodes -o wide | grep NotReady",
            "Describe the node taints and conditions: kubectl describe node ip-10-42-3-21.ec2.internal | grep -A 30 Conditions",
            "SSH to the node and check disk: ssh ip-10-42-3-21 'df -h /var/lib/containerd /var/log /'",
            "Find the largest consumers: ssh ip-10-42-3-21 'sudo du -sh /var/lib/containerd/* /var/log/* 2>/dev/null | sort -h | tail -20'",
            "Inspect kubelet logs: ssh ip-10-42-3-21 'sudo journalctl -u kubelet --no-pager -n 500 | grep -i pressure'",
            "List dangling images: ssh ip-10-42-3-21 'sudo crictl images | wc -l; sudo crictl images --quiet | xargs -r sudo crictl inspecti | jq -r .status.id | sort -u | wc -l'",
            "Check kubelet image GC thresholds: ssh ip-10-42-3-21 'sudo cat /var/lib/kubelet/config.yaml | grep -i image'",
            "Look for runaway container logs: ssh ip-10-42-3-21 'sudo find /var/log/containers -size +500M -ls'",
        ],
        "root_cause": (
            "kubelet imageGCHighThresholdPercent was left at the upstream default of 85, but a "
            "high-churn CI/CD workflow on this node pulled ~120 distinct image tags over 48 "
            "hours, all retained because containerd's content store sat at 82%. The disk also "
            "carried 38GB of unrotated container stdout logs from a chatty service."
        ),
        "resolution_steps": [
            "Cordon the node: kubectl cordon ip-10-42-3-21.ec2.internal",
            "Free disk by pruning unused images: ssh ip-10-42-3-21 'sudo crictl rmi --prune'",
            "Truncate oversized container logs (one-time, non-rotating): ssh ip-10-42-3-21 'sudo find /var/log/containers -size +500M -exec truncate -s 100M {} +'",
            "Verify disk usage now < 70%: ssh ip-10-42-3-21 'df -h /var/lib/containerd'",
            "Restart kubelet to clear the pressure taint: ssh ip-10-42-3-21 'sudo systemctl restart kubelet'",
            "Wait for node to return Ready: kubectl get nodes -w | grep ip-10-42-3-21",
            "Uncordon: kubectl uncordon ip-10-42-3-21.ec2.internal",
            "Patch kubelet config across the fleet: set imageGCHighThresholdPercent=70, imageGCLowThresholdPercent=55, containerLogMaxSize=50Mi, containerLogMaxFiles=5, then roll the kubelet DaemonSet/userdata",
        ],
        "sop_reference": "SOP-K8S-002",
        "mttr_minutes": 52,
        "lessons_learned": (
            "Default kubelet image-GC thresholds are too lax for build-heavy clusters. "
            "Container logs without rotation will silently consume the node disk; configure "
            "containerLogMaxSize and a host-level logrotate fallback."
        ),
        "related_incidents": ["INC-010"],
    },
    {
        "id": "INC-008",
        "title": "Deployment Rollout Stuck at 0/3 Replicas",
        "severity": "P1",
        "category": "Kubernetes",
        "tags": ["kubernetes", "rollout", "imagepullbackoff", "deployment", "p1"],
        "description": (
            "After merging release v5.1.0, the rolling update of deployment 'notifications-api' "
            "in namespace platform froze with 0/3 new replicas ready and 3/3 old replicas "
            "terminating. The old ReplicaSet had already been scaled to zero by the time the "
            "stall was noticed, leaving zero healthy pods. Outbound notifications stopped "
            "entirely for 9 minutes."
        ),
        "triage_steps": [
            "Check rollout status: kubectl rollout status deployment/notifications-api -n platform --timeout=30s",
            "Describe the new ReplicaSet: kubectl describe rs $(kubectl get rs -n platform -l app=notifications-api --sort-by=.metadata.creationTimestamp -o name | tail -1) -n platform",
            "List pod events: kubectl get events -n platform --sort-by=.metadata.creationTimestamp | grep notifications-api",
            "Inspect the pending pod: kubectl describe pod -n platform -l app=notifications-api,pod-template-hash=$(kubectl get rs -n platform -l app=notifications-api --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.labels.pod-template-hash}')",
            "Confirm ImagePullBackOff in 'Events' section and capture the image reference",
            "Try the pull from the node: ssh <node> 'sudo crictl pull registry.internal/notifications-api:v5.1.0'",
            "List tags actually pushed: curl -sf https://registry.internal/v2/notifications-api/tags/list | jq",
            "Cross-check CI/CD pipeline output: gh run view <run-id> --log | grep -E 'docker push|tagged'",
        ],
        "root_cause": (
            "The release pipeline tagged the image as v5.1.0-rc1 in the build stage but the "
            "Helm values bumped to v5.1.0 (without -rc1). Because Kubernetes had already "
            "scaled the old ReplicaSet to zero before noticing all new pods were stuck in "
            "ImagePullBackOff, there were no surviving pods to serve traffic."
        ),
        "resolution_steps": [
            "Roll back to the last known-good revision: kubectl rollout undo deployment/notifications-api -n platform --to-revision=87",
            "Confirm old pods are scheduling and Ready: kubectl get pods -n platform -l app=notifications-api -w",
            "Push the missing tag from CI artifact: docker tag notifications-api:v5.1.0-rc1 registry.internal/notifications-api:v5.1.0 && docker push registry.internal/notifications-api:v5.1.0",
            "Trigger the deploy again with corrected values: helm upgrade notifications-api ./charts/notifications-api -n platform -f values-prod.yaml --atomic --timeout 5m",
            "Verify all 3 pods reach Ready and pass readiness probes",
            "Add maxUnavailable=0 and maxSurge=1 to the deployment strategy to prevent total-outage rollouts",
            "Add Helm pre-install hook that checks image existence in the registry before applying the chart",
            "Add CI assertion: built image tag MUST equal the chart appVersion or fail the pipeline",
        ],
        "sop_reference": "SOP-K8S-001",
        "mttr_minutes": 16,
        "lessons_learned": (
            "Rollouts must use maxUnavailable=0 so a broken new image can never tear down the "
            "last healthy pod. CI must enforce that the image tag built and the chart "
            "appVersion deployed are identical."
        ),
        "related_incidents": ["INC-006"],
    },
    {
        "id": "INC-009",
        "title": "Horizontal Pod Autoscaler Not Scaling Under Load",
        "severity": "P2",
        "category": "Kubernetes",
        "tags": ["kubernetes", "hpa", "autoscaling", "metrics-server", "p2"],
        "description": (
            "During the 19:00 UTC marketing email blast, traffic to product-api spiked 4x. CPU "
            "averaged 96% across 3 pods, yet the HPA stayed at MinReplicas=3 instead of "
            "scaling toward MaxReplicas=20. Latency p99 climbed from 90ms to 4.2s; 11% of "
            "requests timed out. Detected by Datadog SLO burn alert."
        ),
        "triage_steps": [
            "Confirm HPA state: kubectl get hpa product-api -n commerce -o wide",
            "Describe the HPA and check 'AbleToScale', 'ScalingLimited' conditions: kubectl describe hpa product-api -n commerce",
            "Verify metrics-server is healthy: kubectl get pods -n kube-system -l k8s-app=metrics-server && kubectl top nodes",
            "Try fetching pod metrics directly: kubectl get --raw '/apis/metrics.k8s.io/v1beta1/namespaces/commerce/pods' | jq '.items[] | select(.metadata.name | startswith(\"product-api\"))'",
            "Tail metrics-server logs: kubectl logs -n kube-system -l k8s-app=metrics-server --tail=200",
            "Check resource requests are set on the deployment (HPA requires requests): kubectl get deployment product-api -n commerce -o jsonpath='{.spec.template.spec.containers[0].resources.requests}'",
            "Inspect controller-manager logs for HPA reconcile errors: kubectl logs -n kube-system kube-controller-manager-master-1 --tail=300 | grep horizontal",
            "Validate API throttling: kubectl get --raw /apis/metrics.k8s.io/v1beta1 -v=6 2>&1 | grep -i 'too many'",
        ],
        "root_cause": (
            "metrics-server pod had been OOMKilled three hours earlier and entered "
            "CrashLoopBackOff. Without a metrics source the HPA fell back to its "
            "lastScaleTime decision and refused to scale up. Compounded by a recent change "
            "that doubled the metrics-server scrape interval and increased its memory "
            "footprint past its 200Mi limit."
        ),
        "resolution_steps": [
            "Manually scale to relieve immediate pressure: kubectl scale deployment product-api -n commerce --replicas=15",
            "Patch metrics-server resources: kubectl set resources deployment metrics-server -n kube-system --requests=cpu=200m,memory=400Mi --limits=cpu=500m,memory=800Mi",
            "Restart the pod and confirm Ready: kubectl rollout restart deployment metrics-server -n kube-system",
            "Validate the metrics API again: kubectl top pods -n commerce",
            "Wait 60 seconds and confirm HPA picks up the load: kubectl get hpa product-api -n commerce -w",
            "Gradually return to autoscaling by removing the manual override: kubectl scale deployment product-api -n commerce --replicas=3 (HPA will scale back as needed)",
            "Add Prometheus alert: up{job='metrics-server'} == 0 for 2m (critical)",
            "Add a secondary scaling signal via KEDA based on Datadog request rate so HPA is not single-source-dependent",
        ],
        "sop_reference": "SOP-K8S-001",
        "mttr_minutes": 36,
        "lessons_learned": (
            "HPA is only as reliable as metrics-server. Treat metrics-server as a tier-1 "
            "dependency: alert on its health, oversize its resources, and consider an "
            "external metric source (KEDA/Prometheus Adapter) for critical workloads."
        ),
        "related_incidents": ["INC-006", "INC-016"],
    },
    {
        "id": "INC-010",
        "title": "Persistent Volume Claim Stuck in Pending State",
        "severity": "P2",
        "category": "Kubernetes",
        "tags": ["kubernetes", "pvc", "storage", "ebs", "csi", "p2"],
        "description": (
            "A new StatefulSet 'analytics-clickhouse' could not start because PVC "
            "data-analytics-clickhouse-0 remained in Pending state for 18 minutes. No volume "
            "was being provisioned. Blocked the rollout of the Q4 analytics platform "
            "(non-customer-facing, internal data team). Detected by ArgoCD sync timeout."
        ),
        "triage_steps": [
            "Confirm PVC status: kubectl get pvc -n analytics data-analytics-clickhouse-0",
            "Describe the PVC and capture provisioning events: kubectl describe pvc data-analytics-clickhouse-0 -n analytics",
            "Verify the StorageClass exists and points to the right provisioner: kubectl get storageclass gp3-encrypted -o yaml",
            "Check the EBS CSI controller pod logs: kubectl logs -n kube-system -l app=ebs-csi-controller -c csi-provisioner --tail=300",
            "Inspect EBS CSI external-provisioner events: kubectl get events -n kube-system --field-selector reason=ProvisioningFailed --sort-by=.metadata.creationTimestamp | tail -20",
            "Confirm IAM role assumed by the CSI driver: aws sts get-caller-identity --profile $(kubectl exec -n kube-system ebs-csi-controller-0 -- env | grep AWS_ROLE_ARN)",
            "Check AWS EBS service quotas: aws service-quotas get-service-quota --service-code ebs --quota-code L-D18FCD1D",
            "Verify the AZ matches the node: kubectl get nodes -L topology.kubernetes.io/zone",
        ],
        "root_cause": (
            "The AWS EBS gp3 volume quota for the production account was set to 50 TiB and we "
            "were already at 49.92 TiB. The CSI provisioner received "
            "VolumeLimitExceeded from the EBS API but its exponential backoff masked the "
            "error in pod events for the first 15 minutes."
        ),
        "resolution_steps": [
            "Open an AWS Service Quota increase request for L-D18FCD1D (EBS gp3 total storage) from 50 to 100 TiB",
            "While waiting on AWS approval, free space by deleting orphaned PVs: kubectl get pv | grep Released | awk '{print $1}' | xargs -I{} kubectl patch pv {} -p '{\"metadata\":{\"finalizers\":null}}' && kubectl delete pv <name>",
            "Re-check provisioner: kubectl delete pvc data-analytics-clickhouse-0 -n analytics (StatefulSet will recreate)",
            "Confirm volume binds and pod starts: kubectl get pvc -n analytics -w",
            "Once AWS increases the quota, validate via: aws service-quotas get-service-quota --service-code ebs --quota-code L-D18FCD1D",
            "Tag all PVs with team and cost-center labels for future quota planning: kubectl label pv <name> team=analytics cost-center=data-platform",
            "Add a Prometheus alert: kubelet_volume_stats_capacity_bytes summed against the AWS quota at 80%",
        ],
        "sop_reference": "SOP-K8S-002",
        "mttr_minutes": 88,
        "lessons_learned": (
            "Cloud provider quotas (EBS, ENI, vCPU, ELB) must be tracked alongside cluster "
            "capacity. Add a CronJob that exports current quota utilisation to Prometheus so "
            "alerts fire at 80% rather than after the first failed provisioning."
        ),
        "related_incidents": ["INC-007", "INC-026"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # CATEGORY 3 — NETWORK / CONNECTIVITY
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "INC-011",
        "title": "Corporate VPN Gateway Unresponsive — 500 Users Affected",
        "severity": "P1",
        "category": "Network",
        "tags": ["vpn", "cisco-asa", "ipsec", "remote-access", "p1"],
        "description": (
            "At 08:14 UTC the primary Cisco ASA 5555-X VPN concentrator (vpn-gw-1) stopped "
            "accepting new IKEv2 connections and began dropping established sessions. 500+ "
            "remote employees lost access to internal Jira, GitHub Enterprise, and Kubernetes "
            "kubeconfig endpoints. Detected by SolarWinds NPM 'tunnel-down' synthetic check "
            "and a flood of Helpdesk tickets."
        ),
        "triage_steps": [
            "Ping the gateway from a known-good source: ping vpn-gw-1.corp.internal",
            "SSH to the standby ASA and check failover state: show failover | include State",
            "On vpn-gw-1, inspect VPN sessions: show vpn-sessiondb summary",
            "Count IKEv2 SAs: show crypto ikev2 sa | count",
            "Check resource usage: show cpu usage; show memory; show traffic",
            "Tail ASA syslog for IKE errors: show logging | include 'IKE\\|crypto' | last 200",
            "Inspect crypto map counters: show crypto ipsec sa | include pkts_drop",
            "Verify upstream ISP link: traceroute -n 8.8.8.8 from the ASA's outside interface",
        ],
        "root_cause": (
            "An undocumented limit on simultaneous IKEv2 child SAs (max-sa 4000) was reached "
            "after the company-wide return-to-remote-work policy update. New SA negotiations "
            "queued and timed out; existing tunnels were torn down by re-key attempts that "
            "failed against the same exhausted pool."
        ),
        "resolution_steps": [
            "Initiate failover to the standby ASA: failover active (executed on vpn-gw-2)",
            "Confirm new traffic is flowing through vpn-gw-2: show vpn-sessiondb summary",
            "On vpn-gw-1, clear all crypto sessions: clear crypto ikev2 sa; clear crypto ipsec sa",
            "Update IKEv2 policy to raise the child-SA limit: crypto ikev2 policy 10 → tunnel-group DefaultRAGroup ipsec-attributes → set ikev2 max-sa 10000",
            "Save running config: copy running-config startup-config",
            "Sync config to standby: write standby",
            "After 30 minutes of stable failover, fail back during low-usage hours",
            "Add SolarWinds threshold alert on 'IKEv2 SAs in use > 80% of configured max-sa'",
        ],
        "sop_reference": "SOP-NET-001",
        "mttr_minutes": 38,
        "lessons_learned": (
            "Capacity-related VPN limits are invisible until breached. Track every saturable "
            "resource (max-sa, conn-max, xlate-max) in monitoring with alerts at 70%/85%. "
            "Run a quarterly failover drill so on-call know the exact command path."
        ),
        "related_incidents": [],
    },
    {
        "id": "INC-012",
        "title": "Internal DNS Resolution Failures Across All Services",
        "severity": "P1",
        "category": "Network",
        "tags": ["dns", "coredns", "kubernetes", "resolution", "p1"],
        "description": (
            "Starting 06:02 UTC, ~70% of pods cluster-wide failed to resolve "
            "*.svc.cluster.local hostnames with NXDOMAIN. Service-to-service traffic broke for "
            "checkout, payments, and inventory in production cluster eks-prod-use1. Customer "
            "API error rate jumped to 60% within 90 seconds. Detected by Datadog 'dns.failure' "
            "metric and synthetic uptime checks."
        ),
        "triage_steps": [
            "Reproduce from a known-good pod: kubectl run -it --rm debug --image=nicolaka/netshoot --restart=Never -- bash -c 'dig +short kubernetes.default.svc.cluster.local'",
            "List CoreDNS pods and their status: kubectl get pods -n kube-system -l k8s-app=kube-dns -o wide",
            "Describe a failing CoreDNS pod: kubectl describe pod -n kube-system <coredns-pod>",
            "Tail CoreDNS logs for SERVFAIL/NXDOMAIN: kubectl logs -n kube-system -l k8s-app=kube-dns --tail=300 --prefix",
            "Dump the active Corefile: kubectl get configmap coredns -n kube-system -o yaml > /tmp/coredns-current.yaml",
            "Compare against the Git source of truth: git diff HEAD -- k8s/coredns/Corefile",
            "Check kube-dns endpoints: kubectl get endpoints kube-dns -n kube-system",
            "Validate the cluster CIDR DNS upstream: kubectl run -it --rm debug --image=nicolaka/netshoot --restart=Never -- bash -c 'cat /etc/resolv.conf'",
        ],
        "root_cause": (
            "A merged PR modified the CoreDNS Corefile 'forward .' directive from "
            "'10.0.0.2 10.0.0.3' to '8.8.8.8' as a 'quick fix' for an upstream resolution "
            "problem, removing the VPC DNS resolver path. CoreDNS thus could not resolve any "
            "internal AWS PrivateLink or Route53 private-zone records, cascading into all "
            ".svc.cluster.local lookups that depended on upstream chains."
        ),
        "resolution_steps": [
            "Roll back the Corefile via kubectl edit configmap coredns -n kube-system to restore 'forward . 10.0.0.2 10.0.0.3'",
            "Force CoreDNS to pick up the change: kubectl rollout restart deployment coredns -n kube-system",
            "Verify DNS resolution from a debug pod: dig +short kubernetes.default.svc.cluster.local; dig +short google.com",
            "Watch CoreDNS error rate drop in Datadog dashboard 'CoreDNS — Cluster eks-prod-use1'",
            "Open hotfix PR reverting the original change in Git so future Argo CD syncs do not re-apply the bad config",
            "Add a CI policy check (OPA Gatekeeper / kyverno) that blocks Corefile changes touching the 'forward' directive without DBOC/SRE label",
            "Add a Prometheus alert: coredns_dns_responses_total{rcode='SERVFAIL'} rate > 10/min for 2m",
            "Document the resolver chain (VPC → CoreDNS → upstream) in the network architecture wiki",
        ],
        "sop_reference": "SOP-NET-001",
        "mttr_minutes": 19,
        "lessons_learned": (
            "DNS is the cluster's most-critical shared dependency. Treat the Corefile as "
            "tier-1 infrastructure code with mandatory review and automated guardrails. "
            "Synthetic DNS probes from inside the cluster catch this within 30 seconds."
        ),
        "related_incidents": ["INC-014"],
    },
    {
        "id": "INC-013",
        "title": "AWS ALB Health Checks Failing — Target Group Unhealthy",
        "severity": "P2",
        "category": "Network",
        "tags": ["aws", "alb", "health-check", "security-group", "terraform", "p2"],
        "description": (
            "ALB 'public-api-alb' began returning HTTP 503 from all targets at 16:48 UTC. "
            "TargetGroup tg-public-api showed 0/8 healthy targets. Public API traffic failed "
            "for 6 minutes before automatic DNS failover to the secondary region kicked in, "
            "limiting customer-visible impact to ~2% of requests."
        ),
        "triage_steps": [
            "Check target group health: aws elbv2 describe-target-health --target-group-arn arn:aws:elasticloadbalancing:us-east-1:1234:targetgroup/tg-public-api/abc",
            "Note the 'Reason' field for each unhealthy target (Target.Timeout, Target.FailedHealthChecks, etc.)",
            "From a Kubernetes node, curl the target pod directly: kubectl run debug --image=nicolaka/netshoot --rm -it -- curl -v http://<pod-ip>:8080/healthz",
            "List the pod's endpoints: kubectl get endpoints public-api -n platform",
            "Check the security group attached to the worker nodes: aws ec2 describe-security-groups --group-ids sg-0a1b2c3d",
            "Inspect Terraform plan diff for the last change to the network module: terraform plan -target=module.network",
            "Tail ALB access logs in S3 for 'target_status_code' = '-': aws s3 cp s3://alb-logs/prefix/ - --recursive | grep '\"-\"'",
            "Verify the ALB security group can reach the target port: aws ec2 describe-security-groups --filters Name=group-name,Values=public-api-alb-sg",
        ],
        "root_cause": (
            "Terraform PR #2104 'refactor: consolidate ingress rules' removed an inbound rule "
            "on the worker-node security group that allowed TCP 8080 from the ALB security "
            "group. The diff was hidden inside a for_each refactor and not flagged in review. "
            "Without the rule, health-check SYNs were silently dropped at the SG boundary."
        ),
        "resolution_steps": [
            "Restore the rule out-of-band immediately: aws ec2 authorize-security-group-ingress --group-id sg-0a1b2c3d --protocol tcp --port 8080 --source-group sg-public-api-alb",
            "Wait one health-check interval and confirm targets return to healthy: aws elbv2 describe-target-health --target-group-arn ... --query 'TargetHealthDescriptions[].TargetHealth.State'",
            "Open revert PR for Terraform change and import the manual SG rule: terraform import aws_security_group_rule.worker_from_alb sg-0a1b2c3d_ingress_tcp_8080_8080_sg-public-api-alb",
            "Push and apply the Terraform fix in a controlled window: terraform apply -target=module.network.aws_security_group_rule.worker_from_alb",
            "Add a Conftest / OPA policy that fails plans removing ingress rules from worker-node SGs without an SRE-approved override label",
            "Add a Synthetics canary that hits the ALB DNS every 30s from outside AWS",
            "Document SG dependency map for the public API in confluence",
        ],
        "sop_reference": "SOP-NET-002",
        "mttr_minutes": 47,
        "lessons_learned": (
            "Security-group refactors are dangerous because Terraform diffs can hide rule "
            "deletions inside resource renames. Use policy-as-code to block destructive "
            "changes to tier-1 SGs and always pair refactors with an integration smoke test."
        ),
        "related_incidents": ["INC-016"],
    },
    {
        "id": "INC-014",
        "title": "Intermittent Packet Loss Between Microservices",
        "severity": "P2",
        "category": "Network",
        "tags": ["packet-loss", "conntrack", "linux", "kubernetes", "p2"],
        "description": (
            "Sporadic timeouts (3–5%) between order-service and inventory-service in eks-prod-use1 "
            "starting 13:30 UTC. Jaeger showed retries succeeding on the second attempt; p99 "
            "for POST /reserve climbed from 70ms to 1.8s. No deploys in the last 24h. Detected "
            "by SLO burn rate alert; not initially obvious because per-request success rate "
            "stayed above 95%."
        ),
        "triage_steps": [
            "Confirm the failure pattern by running mtr from an order-service pod to inventory-service: kubectl exec -it order-service-xxx -- mtr -rwn -c 100 inventory-service.commerce.svc.cluster.local",
            "Identify the node hosting the affected pods: kubectl get pod -o wide -l app=order-service",
            "On the offending node, check conntrack table: ssh <node> 'sudo cat /proc/sys/net/netfilter/nf_conntrack_count /proc/sys/net/netfilter/nf_conntrack_max'",
            "Look for table-full drops: ssh <node> 'sudo dmesg -T | grep -i conntrack | tail -50'",
            "Tcpdump a 30-second sample of the suspect flow: ssh <node> 'sudo tcpdump -i any -nn -s 0 host <inventory-ip> and port 8080 -c 5000 -w /tmp/cap.pcap'",
            "Check VPC Flow Logs for REJECT entries between the two ENIs: aws ec2 describe-flow-logs and CloudWatch Logs Insights query",
            "Inspect kube-proxy iptables/IPVS rules: ssh <node> 'sudo iptables -t nat -L KUBE-SERVICES -n | grep inventory'",
            "Look at IRQ/softirq distribution: ssh <node> 'mpstat -P ALL 2 5'",
        ],
        "root_cause": (
            "The conntrack table on three worker nodes was full (nf_conntrack_max=131072 "
            "vs nf_conntrack_count peaking at 131072). New flows were silently dropped with "
            "'nf_conntrack: table full, dropping packet' messages in dmesg. Load growth from "
            "a new B2B integration pushed steady-state connections past the kernel default."
        ),
        "resolution_steps": [
            "Apply an immediate kernel tunable bump on all worker nodes via a DaemonSet running on every node: sysctl -w net.netfilter.nf_conntrack_max=524288",
            "Increase the hash bucket count: echo 131072 | sudo tee /sys/module/nf_conntrack/parameters/hashsize",
            "Persist the change in /etc/sysctl.d/99-conntrack.conf: net.netfilter.nf_conntrack_max=524288 and net.netfilter.nf_conntrack_buckets=131072",
            "Re-run mtr from order-service to inventory-service and confirm 0% packet loss",
            "Add a node-exporter alert: node_nf_conntrack_entries / node_nf_conntrack_entries_limit > 0.7",
            "Roll a kernel-args bake change (Bottlerocket / Talos) so the new default ships with future AMIs",
            "Open a capacity ticket to evaluate sticky-session affinity for the B2B traffic so connection counts grow sub-linearly",
        ],
        "sop_reference": "SOP-NET-002",
        "mttr_minutes": 64,
        "lessons_learned": (
            "Default Linux conntrack limits are sized for laptops, not for nodes carrying "
            "high concurrent connection counts. Bake larger values into the AMI/image and "
            "alert on conntrack utilisation as a tier-1 capacity signal."
        ),
        "related_incidents": ["INC-012", "INC-019"],
    },
    {
        "id": "INC-015",
        "title": "SSL Certificate Expiry Causing Browser Warnings",
        "severity": "P2",
        "category": "Network",
        "tags": ["tls", "certificate", "cert-manager", "letsencrypt", "p2"],
        "description": (
            "At 00:01 UTC the wildcard certificate *.api.acme.com expired, causing browsers to "
            "block the customer dashboard with NET::ERR_CERT_DATE_INVALID. Mobile apps using "
            "certificate pinning continued to work, but the web dashboard was inaccessible to "
            "all customers for 22 minutes. Detected by SSL Labs synthetic check fired by the "
            "site-reliability bot at 00:04."
        ),
        "triage_steps": [
            "Verify the expiry from outside the network: echo | openssl s_client -servername api.acme.com -connect api.acme.com:443 2>/dev/null | openssl x509 -noout -dates",
            "List cert-manager Certificate objects: kubectl get certificate -A | grep wildcard-api-acme",
            "Describe the Certificate to see renewal failures: kubectl describe certificate wildcard-api-acme -n ingress",
            "Check the most recent CertificateRequest: kubectl get certificaterequest -n ingress --sort-by=.metadata.creationTimestamp | tail -5",
            "Tail cert-manager controller logs: kubectl logs -n cert-manager deployment/cert-manager --tail=500 | grep -i 'wildcard-api-acme'",
            "Inspect the ACME order: kubectl get challenges -A; kubectl describe challenge <name>",
            "Verify Route53 IAM permissions for the DNS01 solver SA: aws iam get-role-policy --role-name cert-manager-dns01 --policy-name route53-acme",
            "Confirm the IAM trust policy still permits the SA's OIDC issuer: aws iam get-role --role-name cert-manager-dns01",
        ],
        "root_cause": (
            "A quarterly IAM cleanup removed the Route53 ChangeResourceRecordSets permission "
            "from the cert-manager-dns01 role 47 days before expiry. Renewal attempts had "
            "been failing silently because cert-manager's failure events were sent to the "
            "ingress namespace and the team's Slack channel had been muted."
        ),
        "resolution_steps": [
            "Re-attach the missing IAM policy: aws iam attach-role-policy --role-name cert-manager-dns01 --policy-arn arn:aws:iam::1234:policy/route53-acme-write",
            "Force an immediate renewal: kubectl annotate certificate wildcard-api-acme -n ingress cert-manager.io/issue-temporary-certificate='true' --overwrite",
            "Or delete the Certificate to recreate: kubectl delete certificate wildcard-api-acme -n ingress && argocd app sync ingress",
            "Watch the ACME order complete: kubectl get challenge -n ingress -w",
            "Verify the new certificate is served: echo | openssl s_client -servername api.acme.com -connect api.acme.com:443 2>/dev/null | openssl x509 -noout -dates",
            "Add a Datadog monitor 'ssl.cert.expires_in < 30d' for every public hostname, paged to SRE",
            "Add a cert-manager-specific alert: cert_manager_certificate_ready_status == 0 for 1h",
            "Add IAM 'do-not-delete' tag on the cert-manager-dns01 role and document it in the cleanup runbook",
        ],
        "sop_reference": "SOP-NET-001",
        "mttr_minutes": 32,
        "lessons_learned": (
            "Cert expiry is one of the few outages with a known firing time, so monitoring "
            "should fail loudly at 30/14/7 days before expiry. Treat the renewal IAM role as "
            "tier-1 infrastructure that quarterly cleanups must skip."
        ),
        "related_incidents": [],
    },
    # ────────────────────────────────────────────────────────────────────────
    # CATEGORY 4 — APPLICATION ERRORS
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "INC-016",
        "title": "API Gateway 5xx Error Rate Spike to 40%",
        "severity": "P1",
        "category": "Application",
        "tags": ["api-gateway", "5xx", "kong", "circuit-breaker", "p1"],
        "description": (
            "Kong API gateway error rate jumped from a baseline of 0.2% to 40% within 90 "
            "seconds at 21:13 UTC. The dominant response was HTTP 503 with body "
            "'no Route matched with those values' from the upstream user-service. ~$120K of "
            "transactions failed in 13 minutes. Detected by Kong's Prometheus plugin and the "
            "platform SLO burn-rate alert."
        ),
        "triage_steps": [
            "Confirm the error class in Kong logs: kubectl logs -n gateway -l app=kong --tail=500 | grep '\"status\":5'",
            "Identify dominant upstream: kubectl logs -n gateway -l app=kong --tail=5000 | awk '/status\":5/ {print $0}' | jq -r .upstream_uri | sort | uniq -c | sort -rn | head",
            "Check user-service pod health: kubectl get pods -n platform -l app=user-service -o wide",
            "Inspect user-service logs for upstream errors: kubectl logs -n platform -l app=user-service --tail=300",
            "Verify DB connectivity from user-service: kubectl exec -it -n platform user-service-xxx -- nc -zv db-prod-1 5432",
            "Check user-service DB pool metrics: curl http://user-service:9090/metrics | grep db_pool",
            "List recent deploys: kubectl rollout history deployment/user-service -n platform",
            "Examine Kong upstream health check status: curl -s http://kong-admin:8001/upstreams/user-service/health | jq",
        ],
        "root_cause": (
            "user-service had been provisioned with a DB connection pool of 20, but the "
            "morning's Black-Friday traffic ramp saturated the pool. Requests beyond pool "
            "capacity timed out at 30s; Kong's upstream timeout (10s) tripped first, returning "
            "503 to clients. No circuit breaker was configured, so retries amplified load."
        ),
        "resolution_steps": [
            "Scale user-service horizontally: kubectl scale deployment user-service -n platform --replicas=12",
            "Raise the DB pool from 20 to 40 per pod via env var: kubectl set env deployment/user-service -n platform DB_POOL_MAX=40",
            "Add a Kong circuit breaker plugin on the upstream: curl -X POST http://kong-admin:8001/services/user-service/plugins --data 'name=circuit-breaker' --data 'config.threshold=0.5' --data 'config.window=10'",
            "Verify error rate falls below 1% in Grafana 'Kong — Production'",
            "Tune Kong upstream timeouts: connect_timeout=2000, send_timeout=5000, read_timeout=5000 (down from 10000 default)",
            "Add Prometheus alert: rate(kong_http_status{code=~'5..'}[2m]) / rate(kong_http_status[2m]) > 0.05",
            "Verify the DB primary did not also reach pool exhaustion (cross-check INC-001 runbook)",
            "Plan a load test in staging at 3x peak to validate the new pool/circuit-breaker config",
        ],
        "sop_reference": "SOP-APP-001",
        "mttr_minutes": 22,
        "lessons_learned": (
            "Every gateway → upstream edge must enforce a circuit breaker. DB pool sizing "
            "must be load-tested with a real-traffic profile; defaults from local dev are "
            "almost never right for production scale."
        ),
        "related_incidents": ["INC-001", "INC-009", "INC-019"],
    },
    {
        "id": "INC-017",
        "title": "Memory Leak in Java Service — Heap Exhaustion",
        "severity": "P1",
        "category": "Application",
        "tags": ["java", "jvm", "memory-leak", "heap-dump", "gc", "p1"],
        "description": (
            "Java 17 service 'pricing-engine' began experiencing 4–7 second GC pauses every "
            "8 minutes starting at 11:00 UTC. Heap usage climbed linearly at ~10MB/min "
            "regardless of traffic. After 90 minutes the first pod hit "
            "OutOfMemoryError: GC overhead limit exceeded and was restarted by Kubernetes. "
            "Detected via Prometheus jvm_memory_used_bytes alert and APM trace latencies."
        ),
        "triage_steps": [
            "Confirm GC pause duration: query Prometheus jvm_gc_pause_seconds_max{service='pricing-engine'} for the last 2 hours",
            "Take a live heap histogram (no full dump yet): kubectl exec -it pricing-engine-xxx -- jcmd 1 GC.class_histogram | head -40",
            "Identify the top growing class compared to a baseline: kubectl exec -it pricing-engine-xxx -- jcmd 1 GC.class_histogram > /tmp/histo-2.txt; diff /tmp/histo-1.txt /tmp/histo-2.txt",
            "Capture a full heap dump: kubectl exec -it pricing-engine-xxx -- jcmd 1 GC.heap_dump /tmp/heap.hprof",
            "Copy the dump out for analysis: kubectl cp pricing-engine-xxx:/tmp/heap.hprof ./heap.hprof",
            "Open in Eclipse MAT or VisualVM and run Leak Suspects Report",
            "Verify GC logs are enabled and parse with GCViewer: kubectl exec pricing-engine-xxx -- cat /var/log/jvm/gc.log",
            "Cross-check recent deploys: kubectl rollout history deployment/pricing-engine -n commerce",
        ],
        "root_cause": (
            "Release v3.1.0 added a ThreadLocal cache in the rule-evaluation hot path. The "
            "ExecutorService had been resized to 200 threads for performance, but ThreadLocal "
            "entries were never cleaned in a finally block. Each request added ~50KB of "
            "retained references to the thread that handled it, never released because the "
            "thread itself was never recycled."
        ),
        "resolution_steps": [
            "Roll back to the last stable release: kubectl rollout undo deployment/pricing-engine -n commerce --to-revision=63",
            "Capture heap dumps at restart and 30 minutes later from one canary pod to confirm leak is gone",
            "Open hotfix v3.1.1: wrap rule-evaluation in try / finally and call ruleContext.remove() on the ThreadLocal",
            "Add a static analysis rule (SpotBugs custom detector) flagging ThreadLocal.set without a matching remove in the same method",
            "Add Prometheus alert: rate(jvm_gc_pause_seconds_sum[5m]) > 0.5 for 10 minutes",
            "Configure JVM HeapDumpOnOutOfMemoryError=true and HeapDumpPath=/var/log/heapdumps with an automatic uploader to S3",
            "Run a 4-hour soak test in staging on v3.1.1 with 2x peak rps and verify heap is flat",
            "Deploy v3.1.1 to canary (1 replica) for 24h before fleet-wide rollout",
        ],
        "sop_reference": "SOP-APP-002",
        "mttr_minutes": 44,
        "lessons_learned": (
            "ThreadLocal in long-lived thread pools is a well-known leak vector and must be "
            "treated like a sharp tool. Soak tests at higher than peak rps for a multi-hour "
            "window will catch this class of leak before production does."
        ),
        "related_incidents": ["INC-006"],
    },
    {
        "id": "INC-018",
        "title": "Distributed Deadlock in Order Processing Service",
        "severity": "P1",
        "category": "Application",
        "tags": ["distributed-lock", "deadlock", "redis", "redlock", "p1"],
        "description": (
            "Between 22:30 and 23:14 UTC, the order-processor service produced a growing "
            "backlog of orders stuck in 'PROCESSING' state. By 22:55 the backlog was 2,400 "
            "orders and 6% of new orders were not progressing past PENDING. Distributed locks "
            "were observed in Redis with TTLs of 600s blocking each other. Detected by "
            "business-metric alert 'orders_stuck_total > 100'."
        ),
        "triage_steps": [
            "Confirm orders stuck count: SELECT count(*) FROM orders WHERE status='PROCESSING' AND updated_at < NOW() - INTERVAL '5 minutes';",
            "Check distributed locks held in Redis: redis-cli --scan --pattern 'lock:order:*' | head -50 ; for k in $(redis-cli --scan --pattern 'lock:order:*' | head -10); do echo $k; redis-cli ttl $k; redis-cli get $k; done",
            "Pull Jaeger traces for a stuck order ID: curl -s 'http://jaeger:16686/api/traces?service=order-processor&tag=order_id=12345' | jq",
            "Inspect log context for stuck orders: kubectl logs -n commerce -l app=order-processor --tail=500 | grep 'order_id=12345'",
            "Look for the locking pattern across services involved (order-service, inventory-service): grep -r 'redlock\\|distLock' services/order services/inventory",
            "Identify lock acquisition order: trace 'Span: acquire_lock' across the two services for the same order_id",
            "Check Redis cluster health: redis-cli -h redis-prod info replication; redis-cli -h redis-prod cluster info",
            "Inspect recent deploys to either service: kubectl rollout history deployment/order-processor deployment/inventory-service -n commerce",
        ],
        "root_cause": (
            "order-processor v8.2 changed the locking order from inventory→order to "
            "order→inventory to fix a perceived latency issue. inventory-service still locked "
            "inventory→order. Concurrent flows produced classic ABBA deadlock; the 10-minute "
            "Redis lock TTL meant deadlocked orders were stuck for up to 600s before timeout."
        ),
        "resolution_steps": [
            "Roll back order-processor: kubectl rollout undo deployment/order-processor -n commerce",
            "Release all stuck distributed locks safely: for k in $(redis-cli --scan --pattern 'lock:order:*'); do redis-cli del $k; done",
            "Re-queue stuck orders for processing: UPDATE orders SET status='PENDING', updated_at=NOW() WHERE status='PROCESSING' AND updated_at < NOW() - INTERVAL '5 minutes';",
            "Author canonical-order hotfix v8.2.1: always lock inventory before order across all services and document in services/common/locks/README.md",
            "Add a watchdog Sentinel that scans for 'lock:*' keys older than 60s and emits an alert with the key name and holder",
            "Reduce TTL on distributed locks from 600s to 30s; rely on heartbeat extension for legitimate long-running work",
            "Add a chaos test that runs 100 concurrent orders across both services and validates no deadlock detected over a 5-minute window",
            "Deploy v8.2.1 to canary for 2h, then fleet-wide",
        ],
        "sop_reference": "SOP-APP-001",
        "mttr_minutes": 36,
        "lessons_learned": (
            "Distributed locks must follow a globally documented acquisition order, like "
            "DB-level locks. Lock TTLs should be short and extended by heartbeat so a bug "
            "produces a fast failure, not a long silent stall."
        ),
        "related_incidents": ["INC-004"],
    },
    {
        "id": "INC-019",
        "title": "Cascading Timeout — Payment Service Dependency Chain",
        "severity": "P1",
        "category": "Application",
        "tags": ["cascading-failure", "timeout", "circuit-breaker", "payment", "p1"],
        "description": (
            "Payment provider Stripe experienced an undisclosed regional degradation at 15:08 "
            "UTC. p99 latency on POST /charges climbed from 240ms to 8.4s. Within 4 minutes, "
            "the failure cascaded: payment-service threads blocked → checkout-service threads "
            "blocked → cart-service threads blocked → public-api gateway exhausted upstream "
            "workers. 28% of customer requests timed out for 19 minutes. Detected by Datadog "
            "RUM 'page_load_time' p95 > 10s."
        ),
        "triage_steps": [
            "Identify the most-recent slow downstream from Jaeger: curl -s 'http://jaeger:16686/api/traces?service=public-api&limit=100&minDuration=5s' | jq -r '.data[].spans[] | select(.duration > 5000000) | .operationName' | sort | uniq -c | sort -rn",
            "Confirm payment-service latency: kubectl exec -it -n commerce payment-service-xxx -- curl -s http://localhost:9090/metrics | grep http_client_request_duration",
            "Test direct call to Stripe: kubectl exec -it -n commerce payment-service-xxx -- curl -w '@curl-fmt.txt' -o /dev/null -s https://api.stripe.com/v1/charges",
            "Check thread pool saturation: kubectl exec payment-service-xxx -- curl -s localhost:9090/metrics | grep tomcat_threads",
            "Check upstream Stripe status page: curl -s https://status.stripe.com/api/v2/status.json | jq",
            "Inspect resilience4j circuit-breaker state: kubectl exec payment-service-xxx -- curl localhost:9090/actuator/circuitbreakers",
            "Check public-api gateway upstream queue length: curl http://kong-admin:8001/upstreams/payment-service/targets/health",
            "Look at Datadog APM service map for service-level error rates",
        ],
        "root_cause": (
            "The payment-service HTTP client called Stripe with a 30-second socket timeout "
            "and no circuit breaker. Once Stripe degraded, every payment thread blocked for "
            "30s, exhausting the Tomcat pool (200 threads). Upstream services (checkout, "
            "cart, public-api) waited synchronously on payment-service, propagating the "
            "thread starvation up the call chain."
        ),
        "resolution_steps": [
            "Open a Resilience4j circuit breaker on the Stripe client: failure-rate-threshold=50%, sliding-window=20, wait-duration-in-open-state=30s",
            "Drop the Stripe HTTP timeout from 30s to 3s (Stripe's own p99 is normally <500ms)",
            "Add a fallback path: if circuit is open, enqueue the charge in 'payments_pending' table and return 202 Accepted to the caller",
            "Add a background worker that drains 'payments_pending' once Stripe recovers, with idempotency keys",
            "Deploy the hotfix v6.4.2 to canary for 30 minutes and observe error budget burn",
            "Apply the same circuit-breaker pattern to every external dependency: SendGrid, Twilio, FedEx, Stripe, Plaid",
            "Add a synthetic check that exercises the fallback path daily so it does not bit-rot",
            "Run a GameDay where Stripe is replaced with a chaos proxy injecting 5s latency, validate the cascade does not recur",
        ],
        "sop_reference": "SOP-APP-001",
        "mttr_minutes": 31,
        "lessons_learned": (
            "Every external dependency must be wrapped with a tight timeout (typically "
            "5–10x its p99) and a circuit breaker with a fallback. Synchronous calls all the "
            "way up a service graph are an outage waiting to happen."
        ),
        "related_incidents": ["INC-014", "INC-016"],
    },
    {
        "id": "INC-020",
        "title": "Redis Cache Stampede After TTL Expiry",
        "severity": "P2",
        "category": "Application",
        "tags": ["redis", "cache-stampede", "thundering-herd", "ttl", "p2"],
        "description": (
            "At 12:00 UTC sharp, the cache key 'homepage:feed:global' (TTL=3600s) expired. "
            "11,400 concurrent requests then queried the database simultaneously to "
            "recompute the feed; the recompute is a 3.2-second query joining 6 tables. "
            "Database CPU jumped to 100%, p99 latency on homepage requests climbed to 6s, "
            "and read-replica saturated for 90 seconds. Detected by Datadog "
            "'rds.cpu.maximum > 95'."
        ),
        "triage_steps": [
            "Identify the heavy query in pg_stat_statements: SELECT query, calls, mean_exec_time, max_exec_time FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 5;",
            "Confirm cache miss spike on Redis: redis-cli INFO stats | grep keyspace_misses",
            "Inspect application logs for the recompute fingerprint: kubectl logs -n web -l app=feed-api --tail=2000 | grep 'cache_miss key=homepage:feed:global'",
            "Reproduce the timing: redis-cli ttl homepage:feed:global; redis-cli object idletime homepage:feed:global",
            "Verify single-flight / mutex around recompute: grep -r 'singleflight\\|cache.GetOrCompute' services/feed",
            "Check the DB read-replica IOPS / CPU graph in CloudWatch for the same minute",
            "Confirm there is no probabilistic early refresh: grep -r 'probabilisticEarlyExpiration\\|XFetch' services/feed",
            "Capture the rps to GET /api/v1/homepage/feed in the 60 seconds around 12:00 UTC",
        ],
        "root_cause": (
            "The 'homepage:feed:global' cache used a hard TTL with no single-flight "
            "coordination and no probabilistic early refresh. Every front-end pod that "
            "missed the cache at 12:00:00.000 executed the expensive feed recompute, causing "
            "a classic thundering herd against the DB."
        ),
        "resolution_steps": [
            "Implement single-flight locking using a short-lived Redis SET NX EX 30 'recompute_lock' before running the expensive query; other callers wait and re-read the cache",
            "Add probabilistic early expiration (XFetch / β=1.0) so a small fraction of callers refresh ~30s before TTL, smoothing the rebuild over time",
            "Jitter the TTL by ±10% to avoid synchronised expiries across keys: SET key value EX (3600 + rand(-360, 360))",
            "Deploy a stale-while-revalidate pattern: if cache is between TTL and TTL+60s, return stale data and trigger an async refresh",
            "Add Prometheus metric 'feed_cache_recompute_concurrent_total' and alert if > 5 over 30s",
            "Pre-warm the cache 60s before the known expiry via a sidecar cron",
            "Add an end-to-end load test that triggers TTL expiry while sustaining peak rps, verify DB CPU stays under 70%",
            "Document the cache invalidation pattern in the platform engineering wiki",
        ],
        "sop_reference": "SOP-APP-001",
        "mttr_minutes": 41,
        "lessons_learned": (
            "Hot keys with hard TTLs are stampede magnets. Every cache layer above a "
            "non-trivial recompute must use single-flight, jittered TTLs, and "
            "stale-while-revalidate to absorb the rebuild."
        ),
        "related_incidents": ["INC-016"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # CATEGORY 5 — MESSAGE QUEUE
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "INC-021",
        "title": "Kafka Consumer Group Lag Exceeding 2 Million Messages",
        "severity": "P1",
        "category": "MessageQueue",
        "tags": ["kafka", "consumer-lag", "consumer-group", "throughput", "p1"],
        "description": (
            "Consumer group 'order-processor' on topic 'orders.created' (12 partitions) showed "
            "lag growing from a baseline of <500 to 2.18 million messages over 22 minutes. "
            "Downstream side-effects (inventory reserve, email confirmation, fraud check) "
            "stopped firing in real time. Detected by Burrow alert 'lag > 1000000' at 17:42 UTC."
        ),
        "triage_steps": [
            "Snapshot consumer-group lag: kafka-consumer-groups.sh --bootstrap-server kafka-1:9092 --describe --group order-processor",
            "Identify per-partition lag distribution to confirm whether one partition or all are behind",
            "List members and partition assignment: kafka-consumer-groups.sh --bootstrap-server kafka-1:9092 --describe --group order-processor --members --verbose",
            "Confirm consumer pod count is healthy: kubectl get pods -n commerce -l app=order-processor",
            "Check consumer pod CPU/memory: kubectl top pod -n commerce -l app=order-processor",
            "Tail consumer logs for processing errors or rebalances: kubectl logs -n commerce -l app=order-processor --tail=500 | grep -iE 'rebalance|error|exception'",
            "Check broker health: kafka-topics.sh --bootstrap-server kafka-1:9092 --describe --topic orders.created",
            "Inspect downstream backpressure: was the DB or inventory-service slow? curl http://order-processor:9090/metrics | grep process_duration",
        ],
        "root_cause": (
            "Two of the six order-processor pods were OOMKilled at 17:20 UTC. With only 4 "
            "pods, each had to handle 3 partitions instead of 2, and the per-pod thread pool "
            "could not keep up. The HPA was configured on CPU only, not on consumer lag, so "
            "scaling never reacted to the queueing problem."
        ),
        "resolution_steps": [
            "Immediately scale consumers to 12 pods (one per partition): kubectl scale deployment order-processor -n commerce --replicas=12",
            "Raise memory limits from 512Mi to 1Gi: kubectl set resources deployment/order-processor -n commerce --requests=memory=768Mi --limits=memory=1Gi",
            "Confirm partitions are rebalancing: kafka-consumer-groups.sh --describe --group order-processor",
            "Watch lag decrease — expect ~50K msgs/min drain rate per pod",
            "Add KEDA ScaledObject driven by Kafka lag: trigger on lagThreshold=10000, minReplicas=6, maxReplicas=24",
            "Add Burrow alert thresholds at lag 100K (warning) and 500K (page)",
            "Open postmortem ticket POST-2024-027 to revisit memory sizing of all stateful consumer pods",
            "Add a Grafana panel correlating pod restart count and per-group lag",
        ],
        "sop_reference": "SOP-MQ-001",
        "mttr_minutes": 33,
        "lessons_learned": (
            "Consumer-lag HPA is the correct scaling signal for Kafka workloads, not CPU. "
            "Run consumers at one-pod-per-partition steady state so a single pod failure "
            "does not double the load on its neighbours."
        ),
        "related_incidents": ["INC-022", "INC-025"],
    },
    {
        "id": "INC-022",
        "title": "RabbitMQ Queue Depth at 500K — Consumers Not Processing",
        "severity": "P1",
        "category": "MessageQueue",
        "tags": ["rabbitmq", "queue-depth", "consumer", "tls", "p1"],
        "description": (
            "Queue 'email.notifications' on the RabbitMQ 3.12 cluster (3 nodes) grew from a "
            "baseline of 200 messages to 503,000 over 38 minutes. 'Ready' messages climbed "
            "monotonically while 'Unacked' stayed at 0, indicating no consumer was actively "
            "processing. Critical password-reset emails were not delivered for ~40 minutes. "
            "Detected by 'rabbitmq.queue.messages_ready > 50000' alert."
        ),
        "triage_steps": [
            "Confirm queue depth: rabbitmqctl list_queues name messages messages_ready messages_unacknowledged consumers",
            "List consumers attached to the queue: rabbitmqctl list_consumers | grep email.notifications",
            "If zero consumers, identify the expected consumer deployment: kubectl get deployment email-worker -n platform",
            "Check worker pod status: kubectl get pods -n platform -l app=email-worker",
            "Inspect worker pod logs for connection errors: kubectl logs -n platform -l app=email-worker --tail=300",
            "Confirm RabbitMQ AMQP TLS endpoint: openssl s_client -connect rabbitmq.platform.svc.cluster.local:5671 -showcerts",
            "Check Kubernetes secret used by the worker: kubectl get secret rabbitmq-client-cert -n platform -o jsonpath='{.data.tls\\.crt}' | base64 -d | openssl x509 -noout -dates",
            "Tail RabbitMQ logs: kubectl logs -n rabbitmq rabbitmq-server-0 --tail=300 | grep -i 'error\\|tls'",
        ],
        "root_cause": (
            "The cert-manager-managed client certificate for the email-worker rotated at "
            "02:00 UTC but the deployment was not configured with a reloader. The worker "
            "kept the old certificate in memory and crashed on the next reconnect attempt "
            "with 'TLS handshake error: unknown certificate authority' because the CA "
            "intermediate had also been refreshed."
        ),
        "resolution_steps": [
            "Roll the worker deployment to pick up the new cert: kubectl rollout restart deployment email-worker -n platform",
            "Verify new pods establish AMQP TLS sessions: rabbitmqctl list_connections name peer_host user state",
            "Watch queue depth drain: rabbitmqctl list_queues name messages -- expect ~5K msgs/min drain rate",
            "Add the reloader annotation so future cert rotations trigger rolling restarts automatically: kubectl annotate deployment email-worker -n platform reloader.stakater.com/auto=true",
            "Install the stakater/reloader controller cluster-wide if not already present",
            "Add Prometheus alert: rabbitmq_queue_messages_ready > 10000 for 5m → PagerDuty",
            "Add a second alert: rabbitmq_queue_consumers == 0 for 1m on any queue with prior traffic",
            "Document the certificate-rotation dependency in the email-worker README",
        ],
        "sop_reference": "SOP-MQ-001",
        "mttr_minutes": 24,
        "lessons_learned": (
            "Certificates injected via secrets are only refreshed at pod start. Use a "
            "reloader controller or a sidecar that watches the secret mount path. Alert on "
            "consumer count, not just queue depth, to catch silent worker failures."
        ),
        "related_incidents": ["INC-015"],
    },
    {
        "id": "INC-023",
        "title": "Kafka Broker Leader Election Loop",
        "severity": "P2",
        "category": "MessageQueue",
        "tags": ["kafka", "leader-election", "zookeeper", "broker", "p2"],
        "description": (
            "Broker kafka-broker-2 in the 5-node production Kafka 3.5 cluster repeatedly lost "
            "and regained partition leadership every 35–60 seconds starting at 04:18 UTC. "
            "Partitions thrashed between leaders, producer p99 publish latency climbed from "
            "12ms to 480ms. No data loss but downstream lag grew. Detected by Cruise Control "
            "anomaly detector."
        ),
        "triage_steps": [
            "Identify the flapping broker: kafka-topics.sh --bootstrap-server kafka-1:9092 --describe | awk '{print $4}' | sort | uniq -c",
            "Watch the controller log for ReassignPartitions events: kubectl logs kafka-broker-1 -n kafka --tail=500 | grep -i 'leader election\\|UpdateMetadata'",
            "Check broker-2 specifically: kubectl logs kafka-broker-2 -n kafka --tail=500 | grep -iE 'session expired|zk|kafka.server'",
            "Verify ZooKeeper ensemble health: kubectl exec -n zookeeper zk-0 -- zkServer.sh status; echo ruok | nc zk-0 2181",
            "Capture ZK session timeouts: kubectl logs zk-0 -n zookeeper --tail=500 | grep -i 'expired session'",
            "Inspect broker-2 disk I/O: kubectl exec kafka-broker-2 -- iostat -xz 2 5",
            "Check Kafka log directory free space and write rate: kubectl exec kafka-broker-2 -- df -h /var/lib/kafka/data",
            "Review ZooKeeper tick / session settings: kubectl exec zk-0 -- cat /opt/zookeeper/conf/zoo.cfg | grep -E 'tickTime|sessionTimeout'",
        ],
        "root_cause": (
            "kafka-broker-2's data volume (gp2 EBS) was throttled because its burst credit "
            "had been fully consumed by a heavy retention-compaction sweep that started "
            "earlier. Sustained IOPS dropped to baseline 100 IOPS; broker fsyncs began "
            "blocking longer than the 6s ZooKeeper session timeout, causing the broker to "
            "lose its session and trigger a leader election."
        ),
        "resolution_steps": [
            "Migrate broker-2's data volume from gp2 to gp3 with provisioned 6,000 IOPS: aws ec2 modify-volume --volume-id vol-abc --volume-type gp3 --iops 6000",
            "Cordon broker-2 from new partition assignments via Cruise Control until migration completes",
            "Increase ZooKeeper session timeout from 6s to 18s temporarily: server.properties zookeeper.session.timeout.ms=18000 — apply via rolling broker restart",
            "Reassign partitions evenly across the cluster using Cruise Control rebalance proposal",
            "Verify broker-2 holds leadership stably for 30 minutes after migration",
            "Audit all Kafka data volumes for gp2 burst exhaustion risk: aws ec2 describe-volumes --filters Name=tag:role,Values=kafka-data --query 'Volumes[?VolumeType==\\`gp2\\`]'",
            "Migrate remaining gp2 volumes to gp3 in a rolling maintenance window",
            "Add CloudWatch alert: EBSIOBalance% < 30 for gp2 + KafkaController.LeaderElectionRate > 5/min",
        ],
        "sop_reference": "SOP-MQ-001",
        "mttr_minutes": 102,
        "lessons_learned": (
            "Burstable EBS volumes are dangerous for storage backing high-write services. "
            "Provisioned IOPS removes the entire failure mode. ZooKeeper session timeout "
            "must be tuned to accommodate worst-case disk latency."
        ),
        "related_incidents": ["INC-021"],
    },
    {
        "id": "INC-024",
        "title": "Dead Letter Queue Filling — Poison Message Detected",
        "severity": "P2",
        "category": "MessageQueue",
        "tags": ["kafka", "dlq", "poison-message", "schema", "p2"],
        "description": (
            "DLQ topic 'orders.created.DLQ' grew from 0 to 9,840 messages in 18 minutes "
            "starting 10:05 UTC. order-processor logged repeated "
            "'AvroDeserializationException: Cannot resolve schema id 421' for each message. "
            "Order processing latency was unaffected (DLQ caught the bad messages) but "
            "9,840 orders had not yet been processed downstream."
        ),
        "triage_steps": [
            "Confirm DLQ growth rate: kafka-consumer-groups.sh --describe --group orders-dlq-consumer --bootstrap-server kafka-1:9092",
            "Sample a poison message: kafka-console-consumer.sh --bootstrap-server kafka-1:9092 --topic orders.created.DLQ --max-messages 5 --property print.headers=true",
            "Identify schema id in the message header: each Avro message starts with magic byte 0x00 then a 4-byte schema id",
            "Query Confluent Schema Registry for that schema id: curl -s http://schema-registry:8081/schemas/ids/421 | jq",
            "List recent schema versions of the orders.created topic: curl -s http://schema-registry:8081/subjects/orders.created-value/versions | jq",
            "Diff schema version 12 vs 13: curl -s http://schema-registry:8081/subjects/orders.created-value/versions/13 | jq -r .schema | jq .",
            "Identify the producer that deployed the new schema: git log --since='2 days ago' --all -- schemas/orders/created.avsc",
            "Verify whether the new schema was registered with BACKWARD compatibility: curl http://schema-registry:8081/config/orders.created-value",
        ],
        "root_cause": (
            "Producer 'checkout-service v9.1.0' deployed a new orders.created schema (v13) "
            "that added a required 'fraud_score' field. Schema Registry was configured with "
            "compatibility=NONE for this subject, so the registration succeeded. The "
            "order-processor was still running with schema v12 and could not deserialize "
            "messages produced under v13."
        ),
        "resolution_steps": [
            "Roll back checkout-service to v9.0.4: kubectl rollout undo deployment checkout-service -n commerce",
            "Update Schema Registry compatibility for the subject: curl -X PUT http://schema-registry:8081/config/orders.created-value -d '{\"compatibility\": \"BACKWARD\"}' -H 'Content-Type: application/json'",
            "Author v9.1.1 that makes 'fraud_score' optional with default=0.0, ensuring BACKWARD compatibility",
            "Reprocess the DLQ messages once consumers are on the new schema: deploy a dlq-replayer Job that reads orders.created.DLQ and republishes to orders.created",
            "Confirm all 9,840 orders are processed downstream within 10 minutes",
            "Add CI check: every PR touching schemas/*.avsc must pass 'curl -X POST .../subjects/<subject>/versions -d {schema} ; verify HTTP 200' against a staging Schema Registry",
            "Add Prometheus alert: rate(kafka_consumer_records_consumed_total{topic=~'.*\\.DLQ'}[5m]) > 50",
            "Document the schema-evolution policy: all producer schema changes must be BACKWARD or FULL compatible by default",
        ],
        "sop_reference": "SOP-MQ-001",
        "mttr_minutes": 58,
        "lessons_learned": (
            "Schema Registry compatibility=NONE silently allows breaking changes; default "
            "should be BACKWARD or FULL for every topic. DLQ growth is a leading indicator "
            "of producer/consumer schema drift and deserves a tier-2 alert."
        ),
        "related_incidents": ["INC-021"],
    },
    {
        "id": "INC-025",
        "title": "Kafka Topic Partition Imbalance Causing Hot Brokers",
        "severity": "P3",
        "category": "MessageQueue",
        "tags": ["kafka", "partition-balance", "hot-broker", "p3"],
        "description": (
            "Routine capacity review on 2024-11-04 showed kafka-broker-1 sustaining 88% CPU "
            "and 72% network out, while broker-3 was at 22% CPU and broker-5 at 31%. No "
            "customer impact, but cluster headroom for Black Friday traffic was insufficient. "
            "Detected by weekly Cruise Control balance score report."
        ),
        "triage_steps": [
            "Pull broker CPU and network metrics from Prometheus for the last 7 days",
            "Confirm partition leader distribution: kafka-topics.sh --bootstrap-server kafka-1:9092 --describe | awk '{print $4}' | sort | uniq -c",
            "List the top-10 highest-throughput partitions: kafka-run-class.sh kafka.tools.JmxTool --object-name 'kafka.server:type=BrokerTopicMetrics,name=BytesInPerSec,topic=*' --reporting-interval 10000",
            "Generate a partition reassignment proposal via Cruise Control REST: curl 'http://cruise-control:9090/kafkacruisecontrol/load?json=true'",
            "Identify which topics carry the most traffic on broker-1 specifically",
            "Cross-check whether replica placement also shows the same skew: --describe and look at 'Replicas:' column",
            "Verify Cruise Control anomaly history: tail of the anomaly log shows preferred-leader-imbalance fired 4 times without action",
        ],
        "root_cause": (
            "A series of rolling broker restarts during the August upgrade left preferred "
            "leadership stuck on broker-1 for high-throughput topics. The "
            "auto.leader.rebalance.enable=true setting only triggers when imbalance exceeds "
            "leader.imbalance.per.broker.percentage (default 10%), and the skew never "
            "crossed the threshold for any single broker, even though one broker carried "
            "33% more partitions than the cluster average."
        ),
        "resolution_steps": [
            "Run a preferred-leader election: kafka-leader-election.sh --bootstrap-server kafka-1:9092 --election-type preferred --all-topic-partitions",
            "Verify CPU on broker-1 drops within 5 minutes: grafana dashboard 'Kafka — Broker CPU'",
            "Submit a Cruise Control rebalance proposal: curl -X POST 'http://cruise-control:9090/kafkacruisecontrol/rebalance?json=true&dryrun=true'",
            "Review the proposal and execute if balance score improvement is significant: curl -X POST 'http://cruise-control:9090/kafkacruisecontrol/rebalance?json=true'",
            "Lower auto-balance threshold to 5%: leader.imbalance.per.broker.percentage=5 in server.properties; rolling restart",
            "Enable Cruise Control self-healing for goal violations: capacityGoals.list and use_ready_default_goals=true",
            "Add a Grafana alert: max(broker_cpu) - min(broker_cpu) > 40 percentage points for 1 hour",
            "Document the rebalance procedure in the SRE runbook",
        ],
        "sop_reference": "SOP-MQ-001",
        "mttr_minutes": 165,
        "lessons_learned": (
            "Default Kafka rebalance thresholds are too lax for production clusters with "
            "heterogeneous topic traffic. Cruise Control should be in self-healing mode for "
            "all production Kafka clusters with a tight balance goal."
        ),
        "related_incidents": ["INC-023"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # CATEGORY 6 — CLOUD INFRASTRUCTURE
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "INC-026",
        "title": "AWS EC2 vCPU Limit Reached — Cannot Launch Instances",
        "severity": "P1",
        "category": "Cloud",
        "tags": ["aws", "ec2", "service-quota", "capacity", "p1"],
        "description": (
            "Auto Scaling Group 'web-frontend-asg' failed to launch 18 new m5.4xlarge "
            "instances during the 19:00 UTC traffic surge with error 'You have requested more "
            "vCPU capacity than your current vCPU limit of 384 allows for the instance "
            "bucket'. Available capacity dropped to 70% of expected, p95 latency rose to 2.1s, "
            "and ~7% of customer requests were dropped during peak."
        ),
        "triage_steps": [
            "Read the ASG activity history: aws autoscaling describe-scaling-activities --auto-scaling-group-name web-frontend-asg --max-records 20",
            "Confirm the error message: aws ec2 describe-instances → look for InstanceFailed status reason",
            "Check current vCPU usage by family: aws service-quotas list-service-quotas --service-code ec2 --query 'Quotas[?contains(QuotaName, \\`Standard\\`)]'",
            "Get the current Standard (A, C, D, H, I, M, R, T, Z) On-Demand vCPU limit: aws service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A",
            "Sum running vCPU for Standard family: aws ec2 describe-instances --filters 'Name=instance-state-name,Values=running' --query 'Reservations[*].Instances[*].[InstanceType]' --output text | sort | uniq -c",
            "Check if Spot or Reserved capacity is available as a fallback: aws ec2 describe-spot-price-history --instance-types m5.4xlarge --start-time $(date -u +%FT%TZ)",
            "Verify multi-AZ distribution: aws autoscaling describe-auto-scaling-groups --auto-scaling-group-name web-frontend-asg --query 'AutoScalingGroups[0].AvailabilityZones'",
            "Cross-reference recent infra changes that may have launched large fleets: git log --since '24 hours ago' -- terraform/",
        ],
        "root_cause": (
            "The 'Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances' vCPU "
            "quota for us-east-1 was at 384. A separate data-engineering team had launched "
            "a 60-node EMR cluster on m5.4xlarge (= 960 vCPU) earlier in the day. By the "
            "time the web-frontend-asg tried to scale, only ~64 vCPU were free, well short "
            "of the requested 288 vCPU (18 × 16)."
        ),
        "resolution_steps": [
            "Open an emergency service-quota increase request: aws service-quotas request-service-quota-increase --service-code ec2 --quota-code L-1216C47A --desired-value 2048",
            "Engage AWS Premium Support via the AWS console for prioritised approval (Business or higher support tier)",
            "While waiting, change the ASG's launch template to use a cheaper but still Standard-family instance (m6i.4xlarge) and add c5.4xlarge as a secondary type via mixed instance policy",
            "Switch the EMR cluster to spot pricing for non-critical workers to free On-Demand vCPU: aws emr modify-instance-fleet ...",
            "Once AWS approves the quota increase, retry the ASG scale-out: aws autoscaling set-desired-capacity --auto-scaling-group-name web-frontend-asg --desired-capacity 36",
            "Add a Lambda + CloudWatch rule that exports current vCPU utilisation against quota to Prometheus every 5 minutes",
            "Alert at 70% utilisation of any production-relevant quota",
            "Add quarterly capacity-planning review meeting that includes a quota audit",
        ],
        "sop_reference": "SOP-CLOUD-001",
        "mttr_minutes": 42,
        "lessons_learned": (
            "AWS quotas are shared across teams in the same account. Treat them as a shared "
            "resource with monitoring and runbook ownership. Multi-tenant accounts must use "
            "Resource Access Manager and Organisations Service Quotas for visibility."
        ),
        "related_incidents": ["INC-010"],
    },
    {
        "id": "INC-027",
        "title": "GCP IAM Permission Denied Blocking CI/CD Pipeline",
        "severity": "P2",
        "category": "Cloud",
        "tags": ["gcp", "iam", "cicd", "cloud-run", "service-account", "p2"],
        "description": (
            "All production deploys to GCP Cloud Run started failing at 09:11 UTC with 'PERMISSION_DENIED: "
            "Permission run.services.update denied on resource projects/acme-prod'. 14 release "
            "pipelines were blocked, holding up the 09:30 weekly release train. No customer "
            "impact yet but engineering velocity stopped."
        ),
        "triage_steps": [
            "Inspect the failing CI job log: gcloud builds log <build-id> --project=acme-cicd",
            "Identify the CI service account used: gcloud auth list (in the CI environment) or grep service_account_email cloudbuild.yaml",
            "Check that SA's current bindings on the target project: gcloud projects get-iam-policy acme-prod --flatten='bindings[].members' --filter='bindings.members:serviceAccount:ci-deployer@acme-cicd.iam.gserviceaccount.com'",
            "Use IAM Policy Troubleshooter for a representative call: gcloud policy-intelligence troubleshoot-policy projects/acme-prod --principal-email='ci-deployer@acme-cicd.iam.gserviceaccount.com' --permission=run.services.update",
            "Query Cloud Audit Logs for recent SetIamPolicy events on acme-prod: gcloud logging read 'resource.type=project AND protoPayload.methodName=SetIamPolicy AND timestamp>=\"2024-11-04T00:00:00Z\"' --project=acme-prod --limit=20 --format=json",
            "Identify the principal that performed the removal and the bindings removed",
            "Confirm whether the change came from a Terraform run or a manual console action via 'authenticationInfo.principalEmail'",
            "Check if the SA still has the role at the folder/organisation level (inherited): gcloud organizations get-iam-policy 1234567890",
        ],
        "root_cause": (
            "An automated quarterly IAM cleanup job (terraform-iam-cleaner) removed any IAM "
            "binding labelled 'last_used > 90d'. The ci-deployer SA had been recently "
            "rotated and its activity log on the run.services.update permission appeared "
            "older than 90 days in the audit data the cleanup job consulted. The cleaner "
            "deleted the run.developer role binding."
        ),
        "resolution_steps": [
            "Re-grant the missing role immediately: gcloud projects add-iam-policy-binding acme-prod --member='serviceAccount:ci-deployer@acme-cicd.iam.gserviceaccount.com' --role='roles/run.developer'",
            "Verify by re-running one of the failed CI jobs: gh workflow run release-prod.yml --ref main",
            "Pause the terraform-iam-cleaner job: terraform apply -var='iam_cleaner_enabled=false'",
            "Add an exclusion label 'iam-cleaner.acme.com/skip=true' to the ci-deployer SA bindings",
            "Modify the cleaner logic to look at the last 180 days and to exclude any binding tagged with the skip label",
            "Document the cleaner's behaviour and the skip label in the SRE wiki",
            "Add a synthetic deploy that runs against a sentinel Cloud Run service every 30 minutes and pages SRE on PERMISSION_DENIED",
            "Implement a 7-day soft-delete: the cleaner first removes via 'gcloud iam policy-deny' for 7 days before actual removal, giving a recovery window",
        ],
        "sop_reference": "SOP-CLOUD-001",
        "mttr_minutes": 51,
        "lessons_learned": (
            "Automation that removes IAM permissions must have an explicit allowlist or "
            "soft-delete grace period for production-critical service accounts. Build a "
            "synthetic that exercises each tier-1 permission so removals are detected "
            "within minutes, not at the next release."
        ),
        "related_incidents": ["INC-015"],
    },
    {
        "id": "INC-028",
        "title": "Azure Storage Account Throttling — 429 Errors",
        "severity": "P2",
        "category": "Cloud",
        "tags": ["azure", "storage-account", "throttling", "blob", "p2"],
        "description": (
            "Azure Storage account 'acmemediaprod' began returning 429 ServerBusy / "
            "TooManyRequests for ~12% of PUT blob requests at 14:30 UTC. Image uploads from "
            "the customer mobile app failed silently (background retry exhausted). Total of "
            "~38,000 upload attempts affected over a 47-minute window. Detected by Azure "
            "Monitor metric 'Transactions, ResponseType=ServerBusyError'."
        ),
        "triage_steps": [
            "Pull the throttling rate from Azure Monitor: az monitor metrics list --resource '/subscriptions/.../storageAccounts/acmemediaprod' --metric Transactions --filter \"ResponseType eq 'ServerBusyError'\" --interval PT1M",
            "Compare against ingress rate: same query with ResponseType eq 'Success'",
            "Identify the dominant operation: az monitor metrics list --resource ... --metric Transactions --aggregation Count --filter \"ApiName eq 'PutBlob'\"",
            "Check the account's scalability targets: standard general-purpose v2 caps at 20,000 req/s and 60 Gbps egress",
            "Inspect Storage Analytics logs for client IPs causing the spike: az storage blob download-batch --source '$logs' ... | jq '.[]|select(.statusCode>=429)'",
            "Identify the partitioning of the high-traffic container: az storage container show -n media --account-name acmemediaprod",
            "Cross-check with mobile-app release notes for a recent change to upload-image volume",
            "Look for absence of exponential backoff in the client SDK version: grep storage-blob package.json",
        ],
        "root_cause": (
            "A new feature in the mobile app v4.7 uploaded 4 thumbnail variants per photo, "
            "increasing PUT-blob requests 4.2× without a corresponding capacity increase. "
            "The 'media' container also concentrated traffic on a small set of partition "
            "key prefixes (date-based: '2024-11-04/...'), which routes to a single Azure "
            "Storage partition that has its own throughput cap."
        ),
        "resolution_steps": [
            "Issue an immediate config change in the mobile app feature flag to upload only 2 thumbnail variants until partitioning is fixed: LaunchDarkly toggle thumb_variants_count=2",
            "Add SDK-level exponential backoff with jitter for 429 responses: client.retry_policy = ExponentialRetry(initial_backoff=1, increment_base=2, retry_total=6)",
            "Re-partition new uploads by hashing the blob name prefix: new path is '<hash[0:2]>/<original-path>'",
            "Migrate the storage account to a Premium BlockBlob tier (sub-millisecond latency, higher TPS): az storage account create -n acmemediaprodv2 --sku Premium_LRS --kind BlockBlobStorage",
            "Set up storage account shards: route uploads round-robin across acmemediaprod, acmemediaprod2, acmemediaprod3 via a hash of the user-id",
            "Add Azure Monitor alert: percentage of 429 responses > 1% for 5 minutes → PagerDuty",
            "Add a Grafana panel: 'Storage Account Throttling Rate by Container'",
            "Re-enable the 4-thumbnail feature once sharding is in place and load-tested",
        ],
        "sop_reference": "SOP-CLOUD-001",
        "mttr_minutes": 89,
        "lessons_learned": (
            "Cloud storage accounts have per-account and per-partition TPS limits that are "
            "easy to hit with naïve naming. Design partition keys with a hash prefix and "
            "shard across multiple accounts for high-write workloads. Always implement "
            "exponential backoff with jitter for 429 responses."
        ),
        "related_incidents": [],
    },
    {
        "id": "INC-029",
        "title": "AWS S3 Bucket Policy Misconfiguration — Data Exposure Risk",
        "severity": "P1",
        "category": "Cloud",
        "tags": ["aws", "s3", "security", "bucket-policy", "data-exposure", "p1"],
        "description": (
            "AWS Macie alert at 03:14 UTC: bucket 'acme-customer-exports' detected as "
            "publicly readable. Bucket contained ~8,200 CSV exports with customer PII "
            "(name, email, phone). Block Public Access at the account level was DISABLED. "
            "Initial assessment: bucket was exposed for ~47 minutes; CloudTrail showed no "
            "public read activity during the window, but assume-breach response was initiated."
        ),
        "triage_steps": [
            "Confirm exposure: aws s3api get-bucket-policy-status --bucket acme-customer-exports",
            "Read the offending policy: aws s3api get-bucket-policy --bucket acme-customer-exports --query Policy --output text | jq",
            "List bucket ACLs: aws s3api get-bucket-acl --bucket acme-customer-exports",
            "Check account-level Block Public Access: aws s3control get-public-access-block --account-id 123456789012",
            "Pull CloudTrail events for the bucket in the last 24h: aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=acme-customer-exports --max-items 200",
            "Identify the principal that called PutBucketPolicy: jq '.Records[]|select(.eventName==\"PutBucketPolicy\") | {time:.eventTime, user:.userIdentity.arn, principal:.userIdentity.principalId, sourceIP:.sourceIPAddress}'",
            "Cross-reference with the Terraform PR history: git log --all -S 's3:*' --since='2 days ago' -- terraform/",
            "Query S3 server-access logs for any anonymous reads: search for 'arn:aws:iam::*:user/*' and Anonymous principal",
        ],
        "root_cause": (
            "Terraform PR #3204 'allow data-team read access' was merged with a typo: the "
            "principal was set to '*' instead of 'arn:aws:iam::123:role/data-team-read'. The "
            "PR reviewer approved without running 'terraform plan'. The CI pipeline did not "
            "have an OPA/Conftest policy to flag s3:* with Principal:*. Account-level Block "
            "Public Access had been disabled in 2022 to support a legacy partner integration "
            "that no longer existed."
        ),
        "resolution_steps": [
            "Block all public access at the bucket level immediately: aws s3api put-public-access-block --bucket acme-customer-exports --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true",
            "Replace the bucket policy with the correct ARN: aws s3api put-bucket-policy --bucket acme-customer-exports --policy file://fixed-policy.json",
            "Re-enable account-level Block Public Access: aws s3control put-public-access-block --account-id 123456789012 --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true",
            "Notify the legal/compliance and customer-trust teams within 30 minutes per the security incident-response playbook",
            "Open assume-breach forensics ticket SEC-2024-091: scan CloudTrail and S3 access logs across the entire 47-minute window for any anonymous GET/LIST",
            "Add a Conftest rego policy to terraform-plan CI: deny any aws_s3_bucket_policy with Principal:*",
            "Add Macie continuous evaluation on all production buckets with PII tags",
            "Quarterly review of every account-level Block Public Access override; remove if legacy use case no longer exists",
        ],
        "sop_reference": "SOP-CLOUD-001",
        "mttr_minutes": 19,
        "lessons_learned": (
            "Account-level Block Public Access must be ON by default for all production "
            "AWS accounts. Any exception is a tier-1 risk register item that must be "
            "reviewed quarterly. Policy-as-code (OPA/Conftest) gating Terraform plans is "
            "non-negotiable for IAM/S3 changes."
        ),
        "related_incidents": [],
    },
    {
        "id": "INC-030",
        "title": "Terraform State Lock — Concurrent Apply Blocked",
        "severity": "P3",
        "category": "Cloud",
        "tags": ["terraform", "state-lock", "dynamodb", "cicd", "p3"],
        "description": (
            "Engineer attempting to deploy infrastructure change at 16:45 UTC was blocked "
            "for 12 minutes with 'Error: Error acquiring the state lock'. The lock had been "
            "held by 'github-actions-runner-12' since 09:02 UTC (7h43m). No customer impact "
            "but the release pipeline was blocked and on-call paged. Detected by manual "
            "report from the platform engineer who ran 'terraform apply'."
        ),
        "triage_steps": [
            "Read the lock metadata from the Terraform error output: capture LockID, Path, Operation, Who, Version, Created",
            "Confirm the lock in DynamoDB: aws dynamodb get-item --table-name terraform-state-locks --key '{\"LockID\":{\"S\":\"acme-tf-state/prod/terraform.tfstate-md5\"}}'",
            "Identify the holder from the lock 'Who' field (typically user@hostname or runner ID)",
            "Check whether the CI job that took the lock is still running: gh run list --workflow=terraform-apply.yml --status=in_progress",
            "If the runner is gone, confirm via GitHub Actions API: gh api repos/acme/infra/actions/runs/<run-id> | jq .status,.conclusion",
            "Verify no concurrent legitimate apply by checking the on-call calendar and Slack #infra channel",
            "Take a backup of the current state before any unlock: aws s3 cp s3://acme-tf-state/prod/terraform.tfstate ./terraform.tfstate.backup-$(date +%FT%T)",
            "Validate that the state file is internally consistent: terraform state pull | jq .terraform_version,.lineage,.serial",
        ],
        "root_cause": (
            "A GitHub Actions runner was force-killed by the host VM at 09:02 UTC while "
            "holding the Terraform state lock. The runner did not have a trap on SIGTERM to "
            "release the lock cleanly. The DynamoDB lock has no TTL, so the stale entry "
            "persisted until manually cleared."
        ),
        "resolution_steps": [
            "Confirm the holder is truly dead (GitHub Actions run status = cancelled/failed, no process on the runner host)",
            "Run terraform force-unlock with the LockID from the error message: terraform force-unlock <LOCK_ID>",
            "Re-run the original terraform apply and verify it succeeds",
            "Update the CI workflow to trap SIGTERM and run 'terraform force-unlock -force <LockID>' on cleanup: add a trap in the runner's pre-job hook",
            "Add a DynamoDB TTL on the locks table: aws dynamodb update-time-to-live --table-name terraform-state-locks --time-to-live-specification 'Enabled=true,AttributeName=ExpiresAt'",
            "Wrap every Terraform apply in CI with a Slack notification on success/failure: webhook posts to #infra-deploys",
            "Add a CloudWatch alarm: locks held longer than 2 hours → notify SRE",
            "Document the force-unlock procedure with the exact safety checks in the SRE runbook",
        ],
        "sop_reference": "SOP-CLOUD-001",
        "mttr_minutes": 78,
        "lessons_learned": (
            "Distributed locks require clean release on process exit. Always trap SIGTERM "
            "in CI runners that acquire locks. Configure TTLs on lock tables so the worst "
            "case is automatic recovery within a known window."
        ),
        "related_incidents": [],
    },
]


SOPS: list[dict] = [
    # ────────────────────────────────────────────────────────────────────────
    # DATABASE
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "SOP-DB-001",
        "title": "Database Connection Pool Exhaustion Runbook",
        "version": "1.0",
        "applicability": (
            "All PostgreSQL primary/replica clusters fronted by PgBouncer, and MySQL clusters "
            "fronted by ProxySQL. Applies to incidents where new connections are refused or "
            "queued, including 'FATAL: sorry, too many clients already' (PG) and "
            "'ERROR 1040 (HY000): Too many connections' (MySQL)."
        ),
        "severity_trigger": "P2",
        "prerequisites": [
            "psql/mysql CLI installed on the bastion",
            "Read access to PgBouncer/ProxySQL admin console (DB pool admin role)",
            "kubectl access to the affected application namespaces",
            "Datadog 'PostgreSQL — Connections' and 'MySQL — Connections' dashboards bookmarked",
            "PagerDuty rotation for DBOC reachable",
        ],
        "steps": [
            "Validate the symptom: run `psql -h <host> -c \"SELECT count(*), state FROM pg_stat_activity GROUP BY state;\"` and confirm `state='active' + 'idle'` exceeds 80% of max_connections.",
            "On PgBouncer, run `SHOW POOLS;` and capture cl_active, cl_waiting, sv_active, sv_idle per database/user pool.",
            "Identify long idle-in-transaction sessions: `SELECT pid, NOW()-xact_start AS age, query FROM pg_stat_activity WHERE state='idle in transaction' ORDER BY age DESC LIMIT 10;`",
            "Correlate with deploys in the last 30 minutes: `kubectl rollout history` across all services that connect to this DB.",
            "If a single application is the dominant connection holder, scale down its replicas temporarily to relieve pressure: `kubectl scale deployment <app> --replicas=<n/2>`.",
            "Kill connections older than 5 minutes that are idle-in-transaction: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction' AND NOW()-xact_start > interval '5 minutes';`",
            "Raise PgBouncer default_pool_size by 50% as a temporary mitigation; reload with `pgbouncer -R /etc/pgbouncer/pgbouncer.ini`.",
            "Set role-level guardrails: `ALTER ROLE <role> SET statement_timeout = '60s'; ALTER ROLE <role> SET idle_in_transaction_session_timeout = '120s';`",
            "Verify metrics return to baseline: pool utilisation < 70% and 0 cl_waiting in PgBouncer.",
            "If exhaustion persists after the above steps, page DBOC for emergency primary failover.",
            "Open a postmortem ticket and capture: which app, root cause, mitigation applied, prevention.",
            "Restore default_pool_size after the offending app deploys a fix and is stable for 30 minutes.",
        ],
        "escalation_path": (
            "L1 SRE (15 min) → L2 DBOC on-call (PagerDuty 'database-oncall') → Principal DBA "
            "(if data corruption suspected) → VP Engineering (if customer impact > 30 min)."
        ),
        "owner": "Database Operations (DBOC)",
        "last_updated": "2024-10-22",
        "related_incidents": ["INC-001", "INC-003", "INC-004"],
    },
    {
        "id": "SOP-DB-002",
        "title": "Database Replication Lag Recovery Runbook",
        "version": "1.2",
        "applicability": (
            "MySQL 5.7/8.0 binlog/GTID replication, PostgreSQL 12+ streaming replication, "
            "and MongoDB replica sets where a secondary lags the primary by more than the "
            "configured SLO (typically 60 seconds for transactional, 300s for analytics)."
        ),
        "severity_trigger": "P2",
        "prerequisites": [
            "Read replica SSH or kubectl exec access",
            "MySQL/Postgres admin credentials",
            "Knowledge of which replicas are behind HAProxy/PgBouncer load balancers",
            "Permission to STOP REPLICA / pause WAL apply",
            "Access to backup snapshots for worst-case rebuild",
        ],
        "steps": [
            "Confirm lag and trend in monitoring: pmm-server / Datadog 'mysql.replication.seconds_behind_master' and ensure it is increasing, not noise.",
            "On the replica, capture full status: `SHOW REPLICA STATUS\\G` (MySQL) or `SELECT now()-pg_last_xact_replay_timestamp();` (PostgreSQL).",
            "Verify IO and SQL threads are running: `Replica_IO_Running`, `Replica_SQL_Running` both Yes (MySQL).",
            "Identify the in-flight transaction: `SELECT * FROM performance_schema.replication_applier_status_by_worker;` (MySQL) or `SELECT pid, query FROM pg_stat_activity WHERE backend_type='walreceiver' OR query LIKE '%replay%';` (PG).",
            "Inspect replica I/O: `iostat -xz 2 5` and confirm %util < 80% on the data volume.",
            "If the lag is caused by a single huge transaction, evaluate whether to wait it out, kill the source query, or rebuild the replica from a snapshot.",
            "For MySQL, enable parallel apply if not already on: `SET GLOBAL replica_parallel_type='LOGICAL_CLOCK'; SET GLOBAL replica_parallel_workers=8; SET GLOBAL replica_preserve_commit_order=ON;` then `STOP REPLICA SQL_THREAD; START REPLICA SQL_THREAD;`",
            "Remove the replica from the HAProxy backend so applications do not read stale data: `echo 'disable server mysql_replicas/<replica>' | socat - /var/run/haproxy.sock`",
            "Monitor catch-up: `pt-heartbeat --check h=<replica>` every 60 seconds.",
            "If catch-up is not progressing after 30 minutes, prepare a rebuild from the most recent backup snapshot.",
            "Re-add the replica to the HAProxy backend once lag < 5 seconds for 5 minutes.",
            "Document the root cause (long DML, missing parallel apply, IO saturation) and update the schema/migration template accordingly.",
        ],
        "escalation_path": (
            "L1 SRE → L2 DBOC on-call → Principal DBA for rebuild-from-snapshot decisions → "
            "VP Engineering if customer impact > 60 min or data inconsistency suspected."
        ),
        "owner": "Database Operations (DBOC)",
        "last_updated": "2024-09-10",
        "related_incidents": ["INC-002", "INC-005"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # KUBERNETES
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "SOP-K8S-001",
        "title": "Kubernetes Pod CrashLoopBackOff Runbook",
        "version": "2.1",
        "applicability": (
            "Any production Kubernetes deployment whose pods enter CrashLoopBackOff with "
            "exit codes 1, 137, 139, or 143. Covers OOMKilled, SIGSEGV, application "
            "self-shutdown, and failed readiness/liveness probes."
        ),
        "severity_trigger": "P1",
        "prerequisites": [
            "kubectl with read/write access to the affected namespace",
            "Permission to roll back deployments",
            "Access to the container registry (for image inspection)",
            "Grafana 'Kubernetes — Workloads' and 'JVM' dashboards",
            "Ability to run `kubectl debug` (Kubernetes 1.23+)",
        ],
        "steps": [
            "Confirm the symptom: `kubectl get pods -n <ns> -l app=<svc>` and note the RESTARTS count and STATUS.",
            "Describe a failing pod and capture the 'Last State' block, exit code, and 'Reason' (OOMKilled, Error, Completed): `kubectl describe pod <pod> -n <ns>`.",
            "Read previous-container logs (the running container's logs are usually the new attempt): `kubectl logs <pod> -n <ns> --previous --tail=200`.",
            "Classify the failure by exit code: 137 = OOMKilled, 139 = SIGSEGV, 143 = SIGTERM during startup, 1 = application exception.",
            "For OOMKilled (137): check memory limit vs language runtime (JVM heap, Node.js --max-old-space-size, Go GOMEMLIMIT). Adjust limit OR runtime config.",
            "For application exception (exit 1): inspect logs for stack trace, then check whether a config change (env var, configmap, secret) is missing or malformed.",
            "Verify image pull was successful and the image actually matches what was intended: `kubectl get pod <pod> -o jsonpath='{.spec.containers[0].image}'`.",
            "If the failure is correlated with a recent deploy, immediately roll back: `kubectl rollout undo deployment/<svc> -n <ns>`.",
            "If the rollback succeeds, capture before/after logs and image SHA for the postmortem.",
            "If the rollback fails or the prior revision is also unhealthy, scale to zero and engage the service owner: `kubectl scale deployment/<svc> -n <ns> --replicas=0`.",
            "Add a hotfix to the next image build and deploy to canary (1 pod) for 30 minutes before fleet-wide rollout.",
            "Update Prometheus alerts: `kube_pod_container_status_restarts_total > 5 in 10m` should already page; if not, add it.",
        ],
        "escalation_path": (
            "L1 SRE (5 min) → Service team owning the workload → Platform Engineering (if "
            "infrastructure cause suspected) → Director of Engineering (if customer impact > 15 min)."
        ),
        "owner": "Platform Engineering / SRE",
        "last_updated": "2024-11-01",
        "related_incidents": ["INC-006", "INC-008", "INC-009"],
    },
    {
        "id": "SOP-K8S-002",
        "title": "Kubernetes Node NotReady Recovery Runbook",
        "version": "1.4",
        "applicability": (
            "Production Kubernetes worker nodes that transition to NotReady or carry the "
            "taints node.kubernetes.io/disk-pressure, memory-pressure, pid-pressure, or "
            "network-unavailable. Covers EKS, GKE, AKS, and self-managed kubeadm clusters."
        ),
        "severity_trigger": "P2",
        "prerequisites": [
            "SSH or SSM Session Manager access to the node",
            "kubectl with permission to cordon/drain/uncordon nodes",
            "Cloud-provider IAM to terminate/replace node instances",
            "Knowledge of the node group's autoscaler / Karpenter configuration",
        ],
        "steps": [
            "Confirm node status: `kubectl get nodes -o wide` and identify which nodes are NotReady.",
            "Describe the offending node and read the Conditions block: `kubectl describe node <node>` (look for Ready=False, DiskPressure=True, MemoryPressure=True).",
            "SSH/SSM into the node and check disk: `df -h /var/lib/containerd /var/log /`.",
            "Check kubelet status: `sudo systemctl status kubelet` and `sudo journalctl -u kubelet -n 200 --no-pager`.",
            "Identify root cause: dangling images (`sudo crictl images | wc -l`), runaway logs (`sudo find /var/log/containers -size +500M`), memory leak (`sudo dmesg | grep -i killed`).",
            "Cordon the node to prevent new pods: `kubectl cordon <node>`.",
            "Free disk via image prune and log truncation: `sudo crictl rmi --prune` then `sudo find /var/log/containers -size +500M -exec truncate -s 100M {} +`.",
            "Restart kubelet: `sudo systemctl restart kubelet`.",
            "Wait for the node to return to Ready: `kubectl get nodes -w`.",
            "If the node does not recover within 10 minutes, drain and replace it: `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force` then terminate the instance in the cloud console (autoscaler will replace).",
            "Uncordon the recovered node: `kubectl uncordon <node>`.",
            "Patch the kubelet config across the fleet to prevent recurrence (tighter imageGCHighThresholdPercent, containerLogMaxSize, evictionHard thresholds) and add a CloudWatch / Stackdriver alert on the precursor signal (e.g. disk > 70%) so future incidents fire as warnings, not outages.",
        ],
        "escalation_path": (
            "L1 SRE → Platform Engineering on-call (if multiple nodes affected) → Cloud "
            "vendor support (if the underlying instance is unreachable) → VP Engineering "
            "(if cluster-wide capacity is at risk)."
        ),
        "owner": "Platform Engineering / SRE",
        "last_updated": "2024-08-18",
        "related_incidents": ["INC-007", "INC-010"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # NETWORK
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "SOP-NET-001",
        "title": "DNS Resolution Failure Runbook",
        "version": "1.3",
        "applicability": (
            "DNS resolution failures inside Kubernetes (CoreDNS), on AWS (Route53 Resolver / "
            "VPC DNS), on GCP (Cloud DNS), and for corporate domains served by internal "
            "BIND/Unbound. Triggered by widespread NXDOMAIN, SERVFAIL, or timeouts."
        ),
        "severity_trigger": "P1",
        "prerequisites": [
            "kubectl exec into a debug pod with dig/nslookup (nicolaka/netshoot)",
            "Read access to the CoreDNS configmap",
            "Permission to roll back configmap changes",
            "Awareness of the resolver chain (pod → CoreDNS → upstream)",
            "Access to ACM, Route53, and Cloud DNS consoles",
        ],
        "steps": [
            "Reproduce the failure from a known-good debug pod: `kubectl run -it --rm debug --image=nicolaka/netshoot --restart=Never -- bash -c 'dig +short kubernetes.default.svc.cluster.local; dig +short google.com'`",
            "If only internal names fail, suspect CoreDNS Corefile or its upstream chain.",
            "If only external names fail, suspect VPC DNS or upstream resolver.",
            "Check CoreDNS pod health: `kubectl get pods -n kube-system -l k8s-app=kube-dns`. Restart any non-Ready pods.",
            "Pull the live Corefile: `kubectl get configmap coredns -n kube-system -o yaml > /tmp/coredns-live.yaml` and diff against the Git source of truth.",
            "If the Corefile differs, roll back: `kubectl edit configmap coredns -n kube-system` (restore from Git) then `kubectl rollout restart deployment coredns -n kube-system`.",
            "Verify the kube-dns Service has endpoints: `kubectl get endpoints kube-dns -n kube-system`.",
            "Inspect upstream resolver reachability from the node: `dig @10.0.0.2 example.com +short`.",
            "Check VPC DHCP options set for the correct DNS server IPs.",
            "For TLS-protected DNS (DoT/DoH), verify the certificate is still valid: `openssl s_client -connect <dns-server>:853`.",
            "Validate Route53 private hosted zone associations: `aws route53 list-hosted-zones-by-vpc --vpc-id <vpc> --vpc-region <region>`.",
            "Once fixed, add a CI/CD policy guardrail that requires SRE approval for any Corefile change touching the 'forward' directive.",
        ],
        "escalation_path": (
            "L1 SRE → Network Engineering on-call (PagerDuty 'network-oncall') → Cloud "
            "vendor support if DNS infrastructure is suspected → VP Engineering for "
            "customer-facing outages > 10 minutes."
        ),
        "owner": "Network Engineering / SRE",
        "last_updated": "2024-10-05",
        "related_incidents": ["INC-011", "INC-012", "INC-015"],
    },
    {
        "id": "SOP-NET-002",
        "title": "Load Balancer Health Check Failure Runbook",
        "version": "1.1",
        "applicability": (
            "AWS Application/Network Load Balancers, GCP Cloud Load Balancers, Azure "
            "Application Gateway, and on-prem F5/Citrix ADC. Triggered when target groups "
            "show unhealthy targets, frontend returns 502/503/504, or health-check error rates spike."
        ),
        "severity_trigger": "P2",
        "prerequisites": [
            "AWS/GCP/Azure CLI authenticated with the relevant account",
            "kubectl read access to the workload namespace",
            "Knowledge of the target health-check endpoint (/healthz, /ready, etc.)",
            "Permission to modify security groups / firewall rules",
        ],
        "steps": [
            "Query target health for AWS: `aws elbv2 describe-target-health --target-group-arn <arn>` and capture per-target Reason.",
            "Identify if the failure is universal (all targets) or partial (some targets): partial usually indicates a node-level problem; universal indicates a config or network-layer issue.",
            "From inside the cluster, curl the target pod directly: `kubectl run debug --image=nicolaka/netshoot --rm -it -- curl -v http://<pod-ip>:<port>/healthz`.",
            "Compare the health-check path the LB uses vs. what the application actually serves: AWS console → Target Group → Health checks tab.",
            "Check the security group / firewall: confirm inbound rule allows LB → target on the health-check port. `aws ec2 describe-security-groups --group-ids <sg-id>`.",
            "Inspect recent Terraform / IaC changes: `terraform plan` against the current state to detect drift.",
            "Tail LB access logs (S3 for ALB, Cloud Logging for GCP, Storage for Azure) and filter on 5xx target responses.",
            "If the failure correlates with a recent deploy, roll back the upstream workload first: `kubectl rollout undo deployment/<svc> -n <ns>`.",
            "If the failure correlates with an IaC change, revert that change in Git and re-apply.",
            "Restore the inbound security group / firewall rule if missing: `aws ec2 authorize-security-group-ingress --group-id <sg> --protocol tcp --port <port> --source-group <lb-sg>`.",
            "Watch target health return to healthy within one health-check interval: `aws elbv2 describe-target-health --target-group-arn <arn> --query 'TargetHealthDescriptions[].TargetHealth.State'`.",
            "Add an external synthetic check (Datadog Synthetics or Pingdom) that hits the LB DNS every 30 seconds.",
        ],
        "escalation_path": (
            "L1 SRE → Service team owning the upstream workload (if app-level cause) → "
            "Network Engineering (if LB or SG/firewall cause) → Cloud vendor support."
        ),
        "owner": "Network Engineering / SRE",
        "last_updated": "2024-07-19",
        "related_incidents": ["INC-013", "INC-014"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # APPLICATION
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "SOP-APP-001",
        "title": "High 5xx Error Rate Runbook",
        "version": "2.0",
        "applicability": (
            "Any HTTP service or API gateway (Kong, Envoy, AWS API Gateway, NGINX) whose "
            "5xx error rate exceeds 1% over a 5-minute window. Covers gateway-level 502/503/504 "
            "and upstream 500/501/502."
        ),
        "severity_trigger": "P1",
        "prerequisites": [
            "Datadog / Grafana access for service-level error rate dashboards",
            "kubectl read access for the upstream workloads",
            "Jaeger or APM tooling for distributed tracing",
            "Ability to scale deployments and toggle feature flags",
            "Access to circuit breaker / rate limiter configuration",
        ],
        "steps": [
            "Confirm the spike in monitoring: open Datadog 'Service — Errors' and identify which service and which endpoint dominate.",
            "Classify the error: 502/504 = upstream failure or timeout; 503 = service unavailable / no backends; 500 = unhandled exception.",
            "Pull a sample failed request trace from Jaeger and identify the slowest or failing span.",
            "Check the upstream workload health: `kubectl get pods -n <ns> -l app=<upstream>` and watch for restarts.",
            "Tail upstream logs: `kubectl logs -n <ns> -l app=<upstream> --tail=200 | grep -iE 'error|exception|timeout'`.",
            "Verify recent deploys in the call chain: `kubectl rollout history` for each service touched by the failed trace.",
            "If a deploy is correlated with the spike, immediately roll back: `kubectl rollout undo deployment/<svc> -n <ns>`.",
            "If the upstream is healthy but saturated, scale it: `kubectl scale deployment/<svc> -n <ns> --replicas=<2x>`.",
            "If a downstream dependency is degraded (DB, external API), engage that team and consider enabling a feature flag fallback.",
            "Ensure every external dependency has a circuit breaker with a tight timeout: review `resilience4j` / `Hystrix` / `Polly` config and raise the failure-rate-threshold low if necessary.",
            "Once error rate drops below 0.5% for 10 minutes, run the same load profile in staging to verify the fix.",
            "File a postmortem with: root cause, mitigation, prevention, and updates to any alerting thresholds.",
        ],
        "escalation_path": (
            "L1 SRE → Service team owning the failing endpoint → Platform Engineering "
            "(infra cause) → VP Engineering (if customer impact > 15 min)."
        ),
        "owner": "Service team + SRE",
        "last_updated": "2024-11-08",
        "related_incidents": ["INC-016", "INC-018", "INC-019", "INC-020"],
    },
    {
        "id": "SOP-APP-002",
        "title": "Memory Leak Detection and Mitigation Runbook",
        "version": "1.2",
        "applicability": (
            "Long-running services (JVM, .NET, Node.js, Go) whose RSS or heap usage grows "
            "monotonically over a period of hours or days without recovering between GC "
            "cycles. Symptoms include rising GC pause times, OOMKilled pods, or steadily "
            "increasing latency."
        ),
        "severity_trigger": "P1",
        "prerequisites": [
            "kubectl exec into the affected pod",
            "jcmd / dotnet-dump / node --inspect / pprof installed in the image or via `kubectl debug`",
            "Eclipse MAT, VisualVM, dotnet-dump analyze, or pprof for offline analysis",
            "S3/GCS bucket for heap-dump upload",
            "Read access to Prometheus jvm_* / process_resident_memory_bytes metrics",
        ],
        "steps": [
            "Confirm the leak: query Prometheus over 24 hours for the relevant memory metric — JVM: `jvm_memory_used_bytes{area='heap'}`; Go: `go_memstats_heap_alloc_bytes`; Node: `nodejs_heap_size_used_bytes`.",
            "Look for a sawtooth that fails to return to baseline after GC — a true leak shows the troughs rising over time.",
            "Capture a baseline class histogram (JVM): `kubectl exec -it <pod> -- jcmd 1 GC.class_histogram > /tmp/histo-baseline.txt`.",
            "Wait 30–60 minutes and capture a second histogram, then diff: `diff /tmp/histo-baseline.txt /tmp/histo-now.txt | head -40`.",
            "Identify the top growing class/struct and inspect references via Eclipse MAT (JVM) or pprof (Go) on a full heap dump.",
            "Take a full heap dump: JVM: `jcmd 1 GC.heap_dump /tmp/heap.hprof`; Go: `curl http://<pod>:6060/debug/pprof/heap > heap.pprof`; Node: `kill -USR2 <pid>`.",
            "Copy the dump out: `kubectl cp <ns>/<pod>:/tmp/heap.hprof ./heap.hprof` then upload to S3 for the postmortem.",
            "Run Leak Suspects Report in Eclipse MAT or `go tool pprof -alloc_objects heap.pprof` to identify dominators.",
            "Mitigate by rolling back the suspected commit/release while a fix is authored: `kubectl rollout undo deployment/<svc> -n <ns>`.",
            "Add a JVM HeapDumpOnOutOfMemoryError flag + uploader so future OOMs leave forensics: `-XX:HeapDumpPath=/var/log/heapdumps/`.",
            "Ship the fix in a hotfix release and run a 4-hour staging soak test at 2× peak rps with heap monitoring before fleet-wide deploy.",
            "Add a Prometheus alert on `jvm_gc_pause_seconds_sum` derivative and a long-window memory-growth alert.",
        ],
        "escalation_path": (
            "L1 SRE → Service team owning the workload → Performance/JVM SME (if available) "
            "→ Director of Engineering (if customer impact escalates)."
        ),
        "owner": "Service team + SRE Performance Working Group",
        "last_updated": "2024-09-30",
        "related_incidents": ["INC-006", "INC-017"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # MESSAGE QUEUE
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "SOP-MQ-001",
        "title": "Kafka Consumer Lag Runbook",
        "version": "1.5",
        "applicability": (
            "Apache Kafka 2.8+ and Confluent Platform 7+ consumer groups whose lag exceeds "
            "the configured SLO. Covers backlog growth, consumer crashes, rebalances, "
            "partition imbalance, and DLQ growth."
        ),
        "severity_trigger": "P1",
        "prerequisites": [
            "kafka-consumer-groups.sh / kcat / Confluent CLI installed",
            "kubectl access to the consumer workload",
            "Burrow or Cruise Control dashboards",
            "Permission to scale the consumer deployment (HPA or manual)",
            "Schema Registry credentials (if Avro/Protobuf)",
        ],
        "steps": [
            "Confirm lag: `kafka-consumer-groups.sh --bootstrap-server <kafka> --describe --group <group>` and identify if a single partition or all partitions are behind.",
            "Capture group membership and partition assignment: `--members --verbose` flag on the same command.",
            "Check consumer pod health: `kubectl get pods -n <ns> -l app=<consumer>` and `kubectl top pod`.",
            "If pods are missing or unhealthy, restart and confirm they rejoin the group: watch for `Successfully joined group` in pod logs.",
            "If lag is uniform across partitions, the consumer is throughput-bound — scale horizontally to match partition count (one pod per partition).",
            "If lag is on a single partition, the issue is per-partition: a slow consumer for a key, a hot partition, or a DLQ-bound message holding the offset.",
            "Inspect downstream backpressure: DB pool exhaustion, downstream HTTP timeouts. Cross-reference with SOP-DB-001 and SOP-APP-001.",
            "For poison messages (deserialization or processing exceptions), confirm the DLQ topic is configured and growing — see INC-024 for schema-evolution issues.",
            "Add KEDA ScaledObject driven by Kafka lag if not present: `triggers: type=kafka, lagThreshold=10000` so future spikes auto-scale.",
            "If broker-side issue (leader election loop, ISR shrink), engage the platform team and follow Kafka cluster runbook.",
            "Verify lag drains: `kafka-consumer-groups.sh --describe --group <group>` every minute until lag returns to baseline.",
            "Document the root cause and the scaling/backpressure changes in the consumer's README and postmortem.",
        ],
        "escalation_path": (
            "L1 SRE → Service team owning the consumer → Platform Engineering (Kafka brokers) "
            "→ Confluent / vendor support (if broker-side issue)."
        ),
        "owner": "Streaming Platform Team",
        "last_updated": "2024-10-28",
        "related_incidents": ["INC-021", "INC-022", "INC-023", "INC-024", "INC-025"],
    },
    # ────────────────────────────────────────────────────────────────────────
    # CLOUD
    # ────────────────────────────────────────────────────────────────────────
    {
        "id": "SOP-CLOUD-001",
        "title": "AWS Resource Limit Runbook",
        "version": "1.0",
        "applicability": (
            "AWS resource limits (vCPU, EBS volume count/storage, ENI, ELB, NAT Gateway, "
            "S3 bucket count) and analogous quotas on GCP and Azure. Triggered when "
            "Terraform/CloudFormation/Pulumi runs fail with quota errors or when ASGs cannot "
            "launch instances."
        ),
        "severity_trigger": "P2",
        "prerequisites": [
            "AWS CLI authenticated with the relevant account",
            "AWS Service Quotas read/write permission for L1 SRE",
            "AWS Business or Enterprise support tier (for fast quota approval)",
            "Quota dashboard in Grafana exporting `aws_servicequotas_utilisation` metric",
            "Knowledge of which quotas are 'soft' (raisable) vs. 'hard' (architectural)",
        ],
        "steps": [
            "Identify the exact quota that was breached from the error message — e.g. `L-1216C47A` for EC2 Standard vCPU.",
            "Confirm the current limit and usage: `aws service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A`.",
            "Compute current consumption: e.g. `aws ec2 describe-instances --filters 'Name=instance-state-name,Values=running' --query 'Reservations[].Instances[].InstanceType' --output text | sort | uniq -c`.",
            "Submit a quota increase request via CLI: `aws service-quotas request-service-quota-increase --service-code <svc> --quota-code <code> --desired-value <n>`.",
            "Open a Premium Support case to fast-track approval if business impact is active.",
            "While waiting on approval, mitigate by: (a) reducing demand (kill non-critical instances), (b) shifting to spot or different instance family that does not share the quota, (c) distributing across regions or accounts.",
            "Verify mitigation: re-run the failed Terraform/ASG operation.",
            "Once the quota is raised, validate: `aws service-quotas get-service-quota --service-code <svc> --quota-code <code>` (NewLimit field).",
            "Tag all resources consuming the quota with team / cost-center labels so future planning is straightforward.",
            "Add (or update) a CloudWatch alarm that exports current utilisation against the limit and fires at 70% and 85%.",
            "Add the quota to the quarterly capacity-planning review document.",
            "For high-blast-radius cleanup automations (IAM, EBS, security groups), add a 7-day soft-delete or skip-label mechanism — see INC-027 for the canonical example.",
        ],
        "escalation_path": (
            "L1 SRE → Cloud Platform team → AWS Technical Account Manager (TAM) → VP "
            "Infrastructure if the quota cannot be raised in time."
        ),
        "owner": "Cloud Platform Engineering",
        "last_updated": "2024-11-04",
        "related_incidents": ["INC-026", "INC-027", "INC-028", "INC-029", "INC-030"],
    },
]


REAL_REFERENCES: list[dict] = [
    {
        "id": "REF-001",
        "title": "Google SRE Book — Incident Management Chapter Summary",
        "source": "Google SRE Book (sre.google/sre-book)",
        "document_type": "reference",
        "category": "SRE Methodology",
        "tags": ["sre", "incident-management", "google", "postmortem"],
        "content": (
            "Google's Site Reliability Engineering defines incident management as a "
            "structured response to service disruptions. Key principles: "
            "1) Incident Command: Designate a single Incident Commander (IC) responsible "
            "for coordination, not technical resolution. "
            "2) Operational Work vs Incident Response: Engineers should spend no more than "
            "50% of time on operational/toil work. "
            "3) Postmortem Culture: Blameless postmortems focus on systemic causes, not "
            "individual fault. Required for all P1 incidents. "
            "4) Error Budgets: Each service has an error budget based on SLO. When budget "
            "is exhausted, reliability work takes priority. "
            "5) Toil Reduction: Automate repetitive operational tasks to reduce MTTR and "
            "free engineers for higher-value work. "
            "MTTR Impact: Teams following this framework report 40-60% reduction in mean "
            "time to resolution."
        ),
        "mttr_impact": "40-60% reduction",
        "key_concepts": [
            "incident command",
            "blameless postmortem",
            "error budget",
            "SLO",
            "toil reduction",
        ],
    },
    {
        "id": "REF-002",
        "title": "AWS Well-Architected Framework — Reliability Pillar",
        "source": "AWS Documentation (docs.aws.amazon.com)",
        "document_type": "reference",
        "category": "Cloud Infrastructure",
        "tags": ["aws", "reliability", "well-architected", "cloud"],
        "content": (
            "The AWS Well-Architected Reliability Pillar defines best practices for "
            "building resilient cloud infrastructure: "
            "1) Automatic Recovery: Use CloudWatch alarms and Auto Scaling to recover "
            "automatically without human intervention. "
            "2) Test Recovery Procedures: Use chaos engineering (AWS Fault Injection "
            "Simulator) to validate recovery procedures. "
            "3) Scale Horizontally: Replace large resources with multiple smaller "
            "resources to reduce single points of failure. "
            "4) Stop Guessing Capacity: Use Auto Scaling to match supply with demand "
            "automatically. "
            "5) Manage Change Through Automation: Use CloudFormation/CDK for "
            "infrastructure changes to reduce human error. "
            "Common Incident Patterns: "
            "- EC2 instance failure: Use ASG with multi-AZ deployment. "
            "- RDS failover: Enable Multi-AZ, test failover monthly. "
            "- S3 data issues: Enable versioning and cross-region replication. "
            "- IAM permission errors: Use IAM Access Analyzer proactively."
        ),
        "mttr_impact": "Automated recovery reduces MTTR by 70-90%",
        "key_concepts": [
            "auto scaling",
            "multi-AZ",
            "chaos engineering",
            "CloudWatch",
            "infrastructure as code",
        ],
    },
    {
        "id": "REF-003",
        "title": "Kubernetes Production Incident Patterns",
        "source": "CNCF SIG Observability + community post-mortems",
        "document_type": "reference",
        "category": "Kubernetes",
        "tags": ["kubernetes", "k8s", "production", "patterns", "cncf"],
        "content": (
            "Common Kubernetes production incident patterns and resolutions. "
            "OOMKilled Incidents: Root cause is memory limits set too low or memory leak "
            "in app. Detection: kubectl get events | grep OOMKill. Resolution: increase "
            "memory limits, add VPA, fix memory leak. Prevention: set requests=limits "
            "for memory in production. "
            "CrashLoopBackOff Incidents: Root cause is application startup failure, "
            "missing config, dependency unavailable. Detection: kubectl describe pod "
            "<name> | grep -A5 Events. Resolution: check logs via kubectl logs <pod> "
            "--previous. Prevention: add readiness/liveness probes and init containers. "
            "Node NotReady Incidents: Root cause is kubelet failure, disk pressure, or "
            "network partition. Detection: kubectl get nodes, kubectl describe node "
            "<name>. Resolution: SSH to node and check kubelet via systemctl status "
            "kubelet. Prevention: node auto-repair (GKE), node monitoring alerts. "
            "etcd Performance Issues: Root cause is high latency or disk I/O saturation. "
            "Detection: etcdctl endpoint health, check etcd metrics. Resolution: defrag "
            "etcd, move to SSD, reduce snapshot frequency. "
            "MTTR benchmarks: P1 K8s incidents average 23 minutes with documented "
            "runbooks vs 67 minutes without."
        ),
        "mttr_impact": "Runbooks reduce K8s incident MTTR by 65%",
        "key_concepts": [
            "OOMKilled",
            "CrashLoopBackOff",
            "etcd",
            "kubelet",
            "node pressure",
        ],
    },
    {
        "id": "REF-004",
        "title": "PostgreSQL Performance Incident Runbook — Community",
        "source": "PostgreSQL Wiki + pganalyze.com documentation",
        "document_type": "reference",
        "category": "Database",
        "tags": ["postgresql", "database", "performance", "runbook"],
        "content": (
            "PostgreSQL incident response reference guide. "
            "Connection Pool Exhaustion: Check via SELECT count(*), state FROM "
            "pg_stat_activity GROUP BY state; check max connections via SHOW "
            "max_connections; kill idle connections via SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity WHERE state = 'idle' AND query_start < NOW() - "
            "INTERVAL '10 minutes'; long term: deploy PgBouncer connection pooler. "
            "Lock Contention and Deadlocks: find blocking queries via SELECT pid, query, "
            "wait_event_type, wait_event FROM pg_stat_activity WHERE wait_event IS NOT "
            "NULL; kill blocker via SELECT pg_cancel_backend(<pid>); enable deadlock "
            "logging by setting log_lock_waits = on. "
            "Replication Lag: check lag via SELECT now() - "
            "pg_last_xact_replay_timestamp(); check slot lag via SELECT slot_name, "
            "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) FROM "
            "pg_replication_slots; resolution: check replica disk I/O and network "
            "bandwidth. "
            "Slow Queries: enable shared_preload_libraries = 'pg_stat_statements'; find "
            "slow queries via SELECT query, mean_exec_time, calls FROM "
            "pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10; add missing "
            "indexes by using EXPLAIN ANALYZE on slow queries."
        ),
        "mttr_impact": "Documented SQL runbooks reduce DB MTTR by 50%",
        "key_concepts": [
            "pg_stat_activity",
            "connection pooling",
            "replication lag",
            "deadlock",
            "PgBouncer",
        ],
    },
    {
        "id": "REF-005",
        "title": "Kafka Operations Runbook — Confluent Documentation",
        "source": "Confluent Documentation (docs.confluent.io)",
        "document_type": "reference",
        "category": "Message Queue",
        "tags": ["kafka", "confluent", "consumer-lag", "operations"],
        "content": (
            "Apache Kafka operational incident guide. "
            "Consumer Lag Investigation: check lag via kafka-consumer-groups.sh "
            "--bootstrap-server <broker>:9092 --describe --group <group-id>; find slow "
            "consumers by looking for LAG column greater than 100000; check consumer "
            "throughput via kafka-consumer-perf-test.sh; resolution: scale consumer "
            "instances, check GC pauses, increase max.poll.records. "
            "Broker Issues: check broker health via kafka-broker-api-versions.sh "
            "--bootstrap-server <broker>:9092; find under-replicated partitions via "
            "kafka-topics.sh --describe --under-replicated-partitions; resolution: "
            "check disk space, network, and restart broker gracefully. "
            "Topic Partition Imbalance: check via kafka-topics.sh --describe --topic "
            "<topic>; rebalance via kafka-preferred-replica-election.sh; prevention: "
            "set auto.leader.rebalance.enable=true. "
            "Dead Letter Queue (DLQ) Handling: inspect DLQ messages via "
            "kafka-console-consumer.sh --topic <dlq-topic> --from-beginning "
            "--max-messages 10; identify poison messages by checking message headers "
            "for error info; resolution: fix consumer logic, replay from DLQ after fix. "
            "MTTR data: Kafka incidents with documented runbooks resolve 3x faster than "
            "undocumented ones."
        ),
        "mttr_impact": "3x faster resolution with documented runbooks",
        "key_concepts": [
            "consumer lag",
            "dead letter queue",
            "partition rebalance",
            "under-replicated",
            "kafka-consumer-groups",
        ],
    },
]


ALL_DOCUMENTS: list[dict] = INCIDENTS + SOPS + REAL_REFERENCES


def get_all_documents() -> list[dict]:
    """Return all incidents, SOPs, and real-world references for knowledge base ingestion."""
    return ALL_DOCUMENTS


def get_incidents() -> list[dict]:
    """Return incident records only."""
    return INCIDENTS


def get_sops() -> list[dict]:
    """Return SOP records only."""
    return SOPS


def get_references() -> list[dict]:
    """Return real-world reference records only."""
    return REAL_REFERENCES
