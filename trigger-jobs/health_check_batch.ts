import { client, logger } from "@trigger.dev/sdk/v3";
import { sql } from "@vercel/postgres";

export const healthCheckBatch = client.defineJob({
  id: "health-check-batch",
  name: "Daily Health Check Batch",
  version: "1.0.0",
  trigger: client.intervals.daily({
    startsAt: new Date("2024-01-01T02:00:00Z"), // 2 AM UTC daily
  }),
  run: async (payload, io) => {
    logger.info("Starting daily health check batch...");

    // 1. Get all active clients and their providers
    const clients = await io.runTask("fetch-active-clients", async () => {
      const result = await sql`
        SELECT DISTINCT c.id, c.name
        FROM clients c
        JOIN providers p ON p.client_id = c.id
        WHERE p.is_active = TRUE AND c.is_active = TRUE
        ORDER BY c.id
      `;
      return result.rows;
    });

    logger.info(`Found ${clients.length} active clients`);

    let totalChecks = 0;
    let successCount = 0;
    let failureCount = 0;

    // 2. For each client, check all active endpoints
    for (const client_row of clients) {
      const endpoints = await io.runTask(
        `fetch-endpoints-${client_row.id}`,
        async () => {
          const result = await sql`
            SELECT
              p.provider_id,
              p.name,
              p.base_url,
              p.client_id,
              pe.id as endpoint_id,
              pe.path,
              pe.method,
              pe.timeout_ms,
              pe.is_critical,
              cbc.error_threshold_pct,
              cbc.window_seconds,
              ps.guaranteed_uptime_pct
            FROM providers p
            JOIN provider_endpoints pe ON pe.provider_id = p.provider_id
            LEFT JOIN circuit_breaker_configs cbc ON cbc.provider_id = p.provider_id
            LEFT JOIN provider_slas ps ON ps.provider_id = p.provider_id
            WHERE p.is_active = TRUE AND p.client_id = $1
            ORDER BY p.provider_id, pe.id
          `;
          return result.rows;
        },
        { icon: "🔍", noop: false }
      );

      logger.info(
        `[${client_row.name}] Checking ${endpoints.length} endpoints`
      );

      // 3. Execute health checks in parallel batches
      const batchSize = 10;
      for (let i = 0; i < endpoints.length; i += batchSize) {
        const batch = endpoints.slice(i, i + batchSize);

        const batchResults = await Promise.all(
          batch.map((ep) =>
            io.runTask(
              `health-check-${ep.provider_id}-${ep.endpoint_id}`,
              async () => {
                const url = `${ep.base_url}${ep.path}`;
                const startTime = Date.now();

                try {
                  const response = await fetch(url, {
                    method: ep.method,
                    timeout: ep.timeout_ms,
                    headers: {
                      "User-Agent": "IntegrationHealthMonitor/1.0",
                      "X-Check-ID": `health-${Date.now()}`,
                    },
                  });

                  const latencyMs = Date.now() - startTime;
                  const success = response.ok;
                  const errorCategory = success
                    ? null
                    : response.status >= 500
                      ? "server_error"
                      : response.status >= 400
                        ? "client_error"
                        : "unknown";

                  return {
                    provider_id: ep.provider_id,
                    client_id: ep.client_id,
                    endpoint: url,
                    method: ep.method,
                    timestamp: new Date().toISOString(),
                    response_status_code: response.status,
                    latency_ms: latencyMs,
                    success,
                    error_category: errorCategory,
                    is_critical: ep.is_critical,
                  };
                } catch (error) {
                  const latencyMs = Date.now() - startTime;
                  return {
                    provider_id: ep.provider_id,
                    client_id: ep.client_id,
                    endpoint: url,
                    method: ep.method,
                    timestamp: new Date().toISOString(),
                    response_status_code: 0,
                    latency_ms: latencyMs,
                    success: false,
                    error_category: "timeout",
                    is_critical: ep.is_critical,
                  };
                }
              },
              { noop: false }
            )
          )
        );

        // 4. Log results to Supabase
        await io.runTask(
          `log-batch-results-${client_row.id}-${i}`,
          async () => {
            for (const result of batchResults) {
              await sql`
                INSERT INTO api_call_events (
                  provider_id, client_id, endpoint, method, timestamp,
                  response_status_code, latency_ms, success, error_category
                ) VALUES (
                  ${result.provider_id}, ${result.client_id}, ${result.endpoint},
                  ${result.method}, ${result.timestamp}, ${result.response_status_code},
                  ${result.latency_ms}, ${result.success}, ${result.error_category}
                )
              `;

              if (result.success) {
                successCount++;
              } else {
                failureCount++;
              }

              // Critical provider failures get logged immediately
              if (result.is_critical && !result.success) {
                logger.warn(
                  `CRITICAL PROVIDER DOWN: ${result.provider_id} (${result.endpoint})`
                );
              }
            }
            totalChecks += batchResults.length;
          }
        );
      }

      // 5. Generate health snapshots per provider
      await io.runTask(`generate-snapshots-${client_row.id}`, async () => {
        const providers = [
          ...new Set(endpoints.map((ep) => ep.provider_id)),
        ];

        for (const provider_id of providers) {
          const stats = await sql`
            SELECT
              COUNT(*) as total_requests,
              SUM(CASE WHEN success = TRUE THEN 1 ELSE 0 END) as successful_requests,
              SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as failed_requests,
              PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms) as p50_ms,
              PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95_ms,
              PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms) as p99_ms,
              MAX(latency_ms) as max_ms
            FROM api_call_events
            WHERE provider_id = ${provider_id}
              AND client_id = ${client_row.id}
              AND timestamp >= NOW() - INTERVAL '24 hours'
          `;

          const row = stats.rows[0];
          const errorRatePct =
            row.total_requests > 0
              ? ((row.failed_requests || 0) / row.total_requests) * 100
              : 0;

          // Determine health status
          let healthStatus = "healthy";
          if (errorRatePct > 15) {
            healthStatus = "unhealthy";
          } else if (errorRatePct > 5) {
            healthStatus = "degraded";
          }

          // Check against SLA max response time
          const sla = await sql`
            SELECT max_response_time_ms FROM provider_slas
            WHERE provider_id = ${provider_id}
          `;
          if (sla.rows.length > 0) {
            const slaMs = sla.rows[0].max_response_time_ms;
            if ((row.p95_ms || 0) > slaMs) {
              healthStatus = healthStatus === "healthy" ? "degraded" : "unhealthy";
            }
          }

          // Insert snapshot
          await sql`
            INSERT INTO health_snapshots (
              provider_id, client_id, snapshot_at, window_seconds,
              latency_p50_ms, latency_p95_ms, latency_p99_ms, latency_max_ms,
              total_requests, successful_requests, failed_requests,
              error_rate_pct, circuit_state, health_status, requests_per_minute
            ) VALUES (
              ${provider_id}, ${client_row.id}, NOW(), 86400,
              ${row.p50_ms || 0}, ${row.p95_ms || 0}, ${row.p99_ms || 0}, ${row.max_ms || 0},
              ${row.total_requests}, ${row.successful_requests}, ${row.failed_requests},
              ${errorRatePct}, 'closed', ${healthStatus},
              ${((row.total_requests || 0) / 1440).toFixed(2)}
            )
          `;
        }
      });
    }

    logger.info(
      `Daily health check complete: ${totalChecks} checks (${successCount} success, ${failureCount} failures)`
    );

    return {
      timestamp: new Date().toISOString(),
      totalChecks,
      successCount,
      failureCount,
      successRate: ((successCount / totalChecks) * 100).toFixed(2) + "%",
    };
  },
});
