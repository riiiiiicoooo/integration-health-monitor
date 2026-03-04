import { client, logger } from "@trigger.dev/sdk/v3";
import { sql } from "@vercel/postgres";

interface ProviderScorecard {
  provider_id: string;
  provider_name: string;
  client_id: string;
  uptime_pct: number;
  sla_target_pct: number;
  sla_status: "compliant" | "at_risk" | "breached";
  incident_count_30d: number;
  mean_time_to_resolve_minutes: number;
  cost_per_call_usd: number;
  total_cost_30d: number;
  p95_latency_ms: number;
  latency_trend: "stable" | "improving" | "degrading";
  renewal_recommendation: "renew" | "renegotiate" | "replace";
  composite_score: number;
  grade: "excellent" | "good" | "concerning" | "unacceptable";
}

export const scorecardGeneration = client.defineJob({
  id: "scorecard-generation",
  name: "Monthly Provider Scorecard Generation",
  version: "1.0.0",
  trigger: client.intervals.monthly({
    startsAt: new Date("2024-01-01T01:00:00Z"), // 1 AM UTC on 1st of month
  }),
  run: async (payload, io) => {
    logger.info("Starting monthly scorecard generation...");

    const scorecardDate = new Date();
    const periodEnd = new Date(
      scorecardDate.getFullYear(),
      scorecardDate.getMonth(),
      1
    );
    const periodStart = new Date(
      scorecardDate.getFullYear(),
      scorecardDate.getMonth() - 1,
      1
    );

    logger.info(
      `Generating scorecards for period: ${periodStart.toISOString()} to ${periodEnd.toISOString()}`
    );

    // 1. Get all active clients and providers
    const providers = await io.runTask("fetch-providers", async () => {
      const result = await sql`
        SELECT
          p.provider_id,
          p.name,
          p.client_id,
          p.blast_radius,
          s.guaranteed_uptime_pct,
          s.max_response_time_ms
        FROM providers p
        JOIN provider_slas s ON s.provider_id = p.provider_id
        WHERE p.is_active = TRUE
        ORDER BY p.client_id, p.provider_id
      `;
      return result.rows;
    });

    logger.info(`Found ${providers.length} providers to score`);

    const scorecards: ProviderScorecard[] = [];

    // 2. Calculate metrics for each provider
    for (const provider of providers) {
      const metrics = await io.runTask(
        `calculate-metrics-${provider.provider_id}`,
        async () => {
          // Uptime & SLA compliance
          const uptimeResult = await sql`
            SELECT
              SUM(available_min) as available_minutes,
              SUM(total_minutes) as total_minutes,
              COUNT(*) as incident_count
            FROM uptime_records
            WHERE provider_id = ${provider.provider_id}
              AND client_id = ${provider.client_id}
              AND period_start >= ${periodStart.toISOString()}
              AND period_end <= ${periodEnd.toISOString()}
          `;

          const uptimeRow = uptimeResult.rows[0];
          const actualUptimePct =
            uptimeRow.total_minutes > 0
              ? ((uptimeRow.available_minutes || 0) /
                  uptimeRow.total_minutes) *
                100
              : 100;

          // Cost per call
          const costResult = await sql`
            SELECT
              SUM(total_cost) as total_cost_30d,
              SUM(total_calls) as total_calls_30d
            FROM cost_records
            WHERE provider_id = ${provider.provider_id}
              AND client_id = ${provider.client_id}
              AND period_start >= ${periodStart.toISOString()}
              AND period_end <= ${periodEnd.toISOString()}
          `;

          const costRow = costResult.rows[0];
          const costPerCall =
            costRow.total_calls_30d > 0
              ? costRow.total_cost_30d / costRow.total_calls_30d
              : 0;

          // Latency trend
          const latencyResult = await sql`
            SELECT
              l1.p95_ms as current_p95,
              LAG(l1.p95_ms) OVER (ORDER BY l1.period_start) as prev_p95
            FROM latency_records l1
            WHERE provider_id = ${provider.provider_id}
              AND client_id = ${provider.client_id}
              AND l1.period_start >= ${new Date(periodStart.getTime() - 86400000 * 7).toISOString()}
              AND l1.period_end <= ${periodEnd.toISOString()}
            ORDER BY l1.period_start DESC
            LIMIT 2
          `;

          const latencyRow = latencyResult.rows[0];
          let latencyTrend: "stable" | "improving" | "degrading" = "stable";
          if (latencyRow && latencyRow.prev_p95) {
            const changePercent =
              ((latencyRow.current_p95 - latencyRow.prev_p95) /
                latencyRow.prev_p95) *
              100;
            if (changePercent > 10) {
              latencyTrend = "degrading";
            } else if (changePercent < -10) {
              latencyTrend = "improving";
            }
          }

          // Mean time to resolve
          const mtrResult = await sql`
            SELECT
              AVG(EXTRACT(EPOCH FROM (resolved_at - detected_at)) / 60) as avg_mtr_minutes
            FROM incidents
            WHERE provider_id = ${provider.provider_id}
              AND client_id = ${provider.client_id}
              AND status = 'resolved'
              AND detected_at >= ${new Date(periodStart.getTime() - 86400000 * 30).toISOString()}
              AND resolved_at IS NOT NULL
          `;

          const mtrRow = mtrResult.rows[0];
          const meanTimeToResolve = mtrRow.avg_mtr_minutes || 0;

          return {
            uptime_pct: Math.round(actualUptimePct * 100) / 100,
            incident_count: uptimeRow.incident_count || 0,
            cost_per_call: Math.round(costPerCall * 10000) / 10000,
            total_cost_30d: Math.round(costRow.total_cost_30d * 100) / 100,
            latency_trend: latencyTrend,
            p95_latency_ms: latencyRow?.current_p95 || 0,
            mean_time_to_resolve: meanTimeToResolve,
          };
        },
        { noop: false }
      );

      // 3. Calculate composite score and grade
      const slaCompliance =
        metrics.uptime_pct >= provider.guaranteed_uptime_pct
          ? "compliant"
          : metrics.uptime_pct >= provider.guaranteed_uptime_pct - 0.1
            ? "at_risk"
            : ("breached" as const);

      // Scoring: SLA (40%) + Incident rate (20%) + Cost (15%) + Latency (15%) + Trend (10%)
      let score = 100;

      // SLA compliance
      if (slaCompliance === "compliant") {
        score -= 0;
      } else if (slaCompliance === "at_risk") {
        score -= 20;
      } else {
        score -= 40;
      }

      // Incident count (lower is better)
      const incidentPenalty = Math.min(metrics.incident_count * 3, 20);
      score -= incidentPenalty;

      // Cost (penalize if high cost per call)
      if (metrics.cost_per_call > 0.1) {
        score -= Math.min((metrics.cost_per_call / 0.1) * 10, 15);
      }

      // Latency (penalize if exceeds SLA)
      if (metrics.p95_latency_ms > provider.max_response_time_ms) {
        const overage =
          (metrics.p95_latency_ms / provider.max_response_time_ms - 1) * 100;
        score -= Math.min(overage, 15);
      }

      // Trend
      if (metrics.latency_trend === "degrading") {
        score -= 10;
      } else if (metrics.latency_trend === "improving") {
        score += 5;
      }

      score = Math.max(0, Math.min(100, score));

      let grade: "excellent" | "good" | "concerning" | "unacceptable";
      if (score >= 90) {
        grade = "excellent";
      } else if (score >= 75) {
        grade = "good";
      } else if (score >= 50) {
        grade = "concerning";
      } else {
        grade = "unacceptable";
      }

      let recommendation: "renew" | "renegotiate" | "replace";
      if (grade === "excellent" && slaCompliance === "compliant") {
        recommendation = "renew";
      } else if (
        grade === "concerning" ||
        slaCompliance === "breached" ||
        metrics.incident_count > 5
      ) {
        recommendation = "replace";
      } else {
        recommendation = "renegotiate";
      }

      const scorecard: ProviderScorecard = {
        provider_id: provider.provider_id,
        provider_name: provider.name,
        client_id: provider.client_id,
        uptime_pct: metrics.uptime_pct,
        sla_target_pct: provider.guaranteed_uptime_pct,
        sla_status: slaCompliance,
        incident_count_30d: metrics.incident_count,
        mean_time_to_resolve_minutes: metrics.mean_time_to_resolve,
        cost_per_call_usd: metrics.cost_per_call,
        total_cost_30d: metrics.total_cost_30d,
        p95_latency_ms: metrics.p95_latency_ms,
        latency_trend: metrics.latency_trend,
        renewal_recommendation: recommendation,
        composite_score: score,
        grade: grade,
      };

      scorecards.push(scorecard);

      logger.info(
        `Scorecard: ${provider.name} - Score ${score.toFixed(1)}/100 (${grade}) - Recommendation: ${recommendation}`
      );
    }

    // 4. Send scorecard emails
    const clientGrouped = scorecards.reduce(
      (acc, card) => {
        if (!acc[card.client_id]) {
          acc[card.client_id] = [];
        }
        acc[card.client_id].push(card);
        return acc;
      },
      {} as Record<string, ProviderScorecard[]>
    );

    for (const [clientId, cards] of Object.entries(clientGrouped)) {
      await io.runTask(
        `send-scorecard-email-${clientId}`,
        async () => {
          // In production, this would use Resend to send HTML emails
          logger.info(`Would send scorecard email to client ${clientId}`);
          logger.info(
            `Scorecard summary: ${cards.length} providers, ${cards.filter((c) => c.sla_status === "breached").length} SLA breaches`
          );
        }
      );
    }

    logger.info(`Scorecard generation complete: ${scorecards.length} scorecards`);

    return {
      timestamp: new Date().toISOString(),
      period: {
        start: periodStart.toISOString(),
        end: periodEnd.toISOString(),
      },
      totalScorecards: scorecards.length,
      bySLAStatus: {
        compliant: scorecards.filter((s) => s.sla_status === "compliant").length,
        at_risk: scorecards.filter((s) => s.sla_status === "at_risk").length,
        breached: scorecards.filter((s) => s.sla_status === "breached").length,
      },
      byGrade: {
        excellent: scorecards.filter((s) => s.grade === "excellent").length,
        good: scorecards.filter((s) => s.grade === "good").length,
        concerning: scorecards.filter((s) => s.grade === "concerning").length,
        unacceptable: scorecards.filter((s) => s.grade === "unacceptable")
          .length,
      },
    };
  },
});
