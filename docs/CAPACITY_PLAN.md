# Integration Health Monitor — Capacity Plan

**Last Updated:** March 2026
**Baseline Workload:** 50 active integrations, 10K provider health checks/day, 5K webhooks/day

---

## Current State (50 integrations)

### Infrastructure

| Component | Current | Headroom | Notes |
|-----------|---------|----------|-------|
| **Health check engine (Python)** | 2 x t3.xlarge (4 CPU, 16GB) | ~50% CPU (serial checks, not parallel) | Checks 50 providers every 2 min = 25 checks/min |
| **Webhook ingest (FastAPI)** | 2 x t3.large (2 CPU, 8GB) | ~30% CPU utilization | Async; can handle 100+ webhooks/sec |
| **PostgreSQL (provider state + incidents)** | 1 x r6g.xlarge (4 CPU, 32GB) | 35% storage, 40% CPU | Provider table: 5GB; incidents: 2GB; baselines: 1GB |
| **Redis cache** | 1 x r6g.large (2 CPU, 16GB) | 45% memory | Scorecards, baselines, provider state cached |
| **Kafka (webhook log + event streaming)** | Single broker (m5.large) | 60% disk | Webhook events and provider events archived |

### Cost

| Category | Monthly | Annual |
|----------|---------|--------|
| Compute (health check + webhooks) | $2.5K | $30K |
| Database (RDS) | $1.4K | $17K |
| Cache (Redis) | $0.5K | $6K |
| Streaming (Kafka) | $0.6K | $7K |
| Storage (S3 for events) | $0.3K | $4K |
| **Total** | **$5.3K** | **$64K** |

### Performance Baseline

| Metric | Value | SLO |
|--------|-------|-----|
| MTTI (P0 providers) | 95 seconds | <120 seconds ✓ |
| Incident detection accuracy | 97.5% | 98% ✓ |
| Webhook delivery gap detection | 50/50 providers monitored | 100% ✓ |
| API availability | 99.2% | 99% ✓ |
| Scorecard generation latency (p95) | 1.2 seconds | — |

### What Breaks First at Current Load

1. **Health check latency** — 50 providers checked serially every 2 min takes ~90 seconds; peak times hit 120 seconds, hitting MTTI SLO limit
2. **PostgreSQL write throughput** — Incident creation + baseline updates + webhook ingestion compete for write lock; at peak hours, write latency exceeds 500ms
3. **Webhook processing backlog** — If a single provider sends 1000 webhooks in 30 seconds (e.g., Plaid reconnecting), queue depth grows; 30+ sec processing lag before detection
4. **Scorecard compute** — Generating all 50 scorecards takes 3-5 seconds; if query overlaps with health checks, database CPU spikes to 90%+

---

## 2x Scenario (100 integrations, 20K checks/day, 10K webhooks/day)

### What Changes

- **Provider base:** Enterprise customers bringing in more integrations (finance platforms integrate with 5-10 providers each)
- **Request mix:** More complex checks (deeper health assessment: latency percentiles, not just error rate)
- **Webhook volume:** Some providers (Stripe, Plaid) scale linearly; others (SendGrid) scale super-linearly with customer growth

### Infrastructure Changes

| Component | 1x → 2x | Action | Timeline |
|-----------|---------|--------|----------|
| **Health check engine** | 2 → 4 instances | Parallel checking (not serial); reduce MTTI from 90s to 45s | Month 1 |
| **PostgreSQL** | 1 primary → 1 primary + 2 read replicas | Health check queries go to replicas; write-heavy incident creation stays on primary | Month 1 |
| **Webhook processing** | 2 → 4 FastAPI servers | Horizontal scale; can handle 200+ webhooks/sec concurrently | Week 1 |
| **Kafka** | Single broker → 2-broker cluster | Mirror topic across replicas; prevent data loss if broker fails | Month 2 |
| **Redis** | 1 x r6g.large → 1 x r6g.xlarge | Cache hit rate expected to drop from 70% to 55% due to more providers | Month 1 |

### Cost Impact

| Category | 1x | 2x | Delta | % increase |
|----------|----|----|-------|-----------|
| Compute | $2.5K | $4.2K | +$1.7K | +68% |
| Database | $1.4K | $3.1K | +$1.7K | +121% |
| Cache | $0.5K | $0.8K | +$0.3K | +60% |
| Streaming | $0.6K | $1.2K | +$0.6K | +100% |
| Storage | $0.3K | $0.5K | +$0.2K | +67% |
| **Total** | **$5.3K** | **$9.8K** | **+$4.5K** | **+85%** |

### Performance at 2x

| Metric | 1x Baseline | 2x Expected | Status |
|--------|------------|-------------|--------|
| MTTI (P0 providers) | 95s | 55s | Well within SLO ✓ |
| Incident detection accuracy | 97.5% | 97% | Slight degradation (more noise); acceptable |
| Webhook processing latency (p95) | 5s | 8s | Acceptable; still sub-10s |
| Scorecard generation latency (p95) | 1.2s | 2.1s | Acceptable; still <5s |

### What Breaks First at 2x

1. **PostgreSQL primary write bottleneck** — Incident creation + baseline updates cluster on same table; write lock contention grows; latency hits 800ms+
2. **Cache hit rate drops** — More unique provider/customer combinations; cache can only hold so many; eviction increases from 10/min to 50+/min
3. **Webhook processing queue depth** — During Plaid bulk webhook delivery (5K+ webhooks in 2 min), processing lag reaches 30+ seconds; detection delayed
4. **Scorecard compute cost** — Generating 100 scorecards takes 8-10 seconds; overlaps with peak health check window

### Scaling Triggers for 2x

- **Health check latency > 2 min:** Parallelize checks further (launch check jobs concurrently instead of sequentially)
- **PostgreSQL write latency > 500ms:** Shard writes across time-based partitions (writes to `incidents_2026_03_16` vs. `incidents_2026_03_17`)
- **Webhook processing queue > 100 events:** Autoscale webhook processors (+2 instances)
- **Cache hit rate < 50%:** Upgrade Redis to r6g.2xlarge or implement smarter eviction policy
- **Scorecard generation > 5s:** Pre-compute scorecard deltas (only recalculate changed providers) instead of full recalculation

---

## 10x Scenario (500 integrations, 100K checks/day, 50K webhooks/day)

### Market Reality at 10x

- **Adoption:** Major enterprises (financial, healthcare, e-commerce) using IHM for mission-critical integrations
- **Integration depth:** Customers integrate with 10-50 providers each; multi-provider workflows become critical
- **Webhook load:** Major providers (Stripe, Plaid) sending 50K+ webhooks/day during peak hours
- **Regulatory scrutiny:** Auditors asking "How do you know Stripe didn't go down?" — scorecard becomes contractual evidence

### What's Fundamentally Broken at 10x

1. **Health check architecture doesn't scale** — Checking 500 providers every 2 min serially is impossible. Even parallelized (200 concurrent checks), each check takes 20-50ms (network + provider response time); latency = 50ms * 500 = 25 seconds minimum just for one round. MTTI SLO becomes unachievable.

2. **Webhook processing becomes bottleneck** — 50K webhooks/day during peak hours = 1000+ webhooks/min or 16+ per second sustained. Current Kafka topic can handle it, but processing each webhook (validate, insert into database) takes 10-20ms; at scale, database becomes bottleneck.

3. **Scorecard computation explodes** — Computing 500 scorecards with 6 metrics each requires 3000 database queries. Current approach takes 30-40 seconds; overlaps with health check window; causes contention.

4. **Storage explosion** — 500K provider state records, 100K+ incidents/month, 50K webhooks/day * 365 = 18M webhook records/year; database grows to 100GB+ (indexing/query performance degrades).

### Architectural Changes Needed for 10x

| Problem | 1x/2x Solution | 10x Solution |
|---------|---|---|
| **Health check latency** | Parallel checks (Python multiprocessing) | Distributed health check architecture (multiple regions, check provider from closest region) + pre-warmed connection pools |
| **Webhook processing** | Kafka topic per provider | Kafka topic per provider with 10+ partitions; parallel stream processing (Flink) |
| **Scorecard compute** | Full recalculation every 5 min | Pre-computed rolling metrics (incremental updates) + caching with 1-hour TTL |
| **Database scalability** | Single PostgreSQL + read replicas | Database sharding (shard by provider_id or customer_id) or move to OLAP warehouse (BigQuery) for analytics |
| **Incident storage** | Single `incidents` table | Partition by date (incidents_2026_03_*); archive old partitions to S3 |

### Cost at 10x (Realistic Projection)

| Category | 1x | 10x | Ratio |
|----------|----|----|-------|
| Compute (health checks + webhooks) | $2.5K | $18K | 7.2x |
| Database (distributed) | $1.4K | $15K | 10.7x |
| Cache (larger Redis) | $0.5K | $3K | 6x |
| Streaming (multi-region Kafka) | $0.6K | $8K | 13.3x |
| Storage (OLAP warehouse) | $0.3K | $12K | 40x |
| **Total** | **$5.3K** | **$56K** | **10.6x** |

**Cost scales linearly (10.6x cost for 10x volume) due to database sharding and warehouse costs.**

---

## Capacity Planning Roadmap

| Quarter | Trigger Level | Action | Investment |
|---------|---|---|---|
| Q2 2026 | Monitor 2x | Pre-stage 2x infrastructure (additional compute, DB read replicas) | $2K infrastructure |
| Q3 2026 | Approach 2x (75 integrations) | Enable parallel health checks; shard webhook processing | $6K infrastructure + 200 eng hours |
| Q4 2026 | Hit 2x (100 integrations) | Full 2x operational; scorecard caching deployed | Ongoing ops |
| Q1 2027 | Plan 5x (250 integrations) | Database sharding evaluation; OLAP warehouse POC | 300 eng hours |
| Q2 2027+ | 5x+ territory | Execute 10x roadmap; multi-region health checks | $40K infra + 1000 eng hours over 6 months |

