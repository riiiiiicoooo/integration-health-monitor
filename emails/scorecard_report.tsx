import React from "react";
import {
  Body,
  Button,
  Container,
  Head,
  Hr,
  Html,
  Link,
  Preview,
  Row,
  Section,
  Table,
  Td,
  Text,
  Tr,
} from "@react-email/components";

interface ScorecardItem {
  providerName: string;
  uptime_pct: number;
  sla_target_pct: number;
  sla_status: "compliant" | "at_risk" | "breached";
  incident_count_30d: number;
  mean_time_to_resolve_minutes: number;
  composite_score: number;
  grade: "excellent" | "good" | "concerning" | "unacceptable";
  renewal_recommendation: "renew" | "renegotiate" | "replace";
  p95_latency_ms: number;
  cost_per_call_usd: number;
}

interface ScorecardReportProps {
  clientName: string;
  period: {
    start: string;
    end: string;
  };
  scorecards: ScorecardItem[];
  dashboardUrl: string;
  executiveSummary: {
    overallGrade: string;
    criticalFindings: string[];
    recommendations: string[];
  };
}

const gradeColors = {
  excellent: "#06A77D",
  good: "#26C281",
  concerning: "#FCBF49",
  unacceptable: "#E63946",
};

const gradeEmojis = {
  excellent: "⭐⭐⭐⭐⭐",
  good: "⭐⭐⭐⭐",
  concerning: "⭐⭐",
  unacceptable: "⭐",
};

export const ScorecardReportEmail: React.FC<ScorecardReportProps> = ({
  clientName,
  period,
  scorecards,
  dashboardUrl,
  executiveSummary,
}) => {
  const totalProviders = scorecards.length;
  const excellentCount = scorecards.filter((s) => s.grade === "excellent")
    .length;
  const goodCount = scorecards.filter((s) => s.grade === "good").length;
  const concerningCount = scorecards.filter((s) => s.grade === "concerning")
    .length;
  const unacceptableCount = scorecards.filter(
    (s) => s.grade === "unacceptable"
  ).length;
  const breachCount = scorecards.filter((s) => s.sla_status === "breached")
    .length;
  const totalIncidents = scorecards.reduce(
    (sum, s) => sum + s.incident_count_30d,
    0
  );
  const avgMTR =
    scorecards.reduce((sum, s) => sum + s.mean_time_to_resolve_minutes, 0) /
    scorecards.length;

  const startDate = new Date(period.start);
  const endDate = new Date(period.end);

  return (
    <Html>
      <Head />
      <Preview>
        {clientName} - Provider Scorecard Report for{" "}
        {startDate.toLocaleDateString()}
      </Preview>
      <Body style={main}>
        <Container style={container}>
          {/* Header */}
          <Section style={headerSection}>
            <Row>
              <div
                style={{
                  backgroundColor: "#1f2937",
                  padding: "40px 32px",
                  color: "white",
                  textAlign: "center",
                }}
              >
                <Text style={{ margin: "0 0 8px 0", fontSize: "12px" }}>
                  Monthly Provider Scorecard Report
                </Text>
                <Text style={{ margin: "0 0 16px 0", fontSize: "28px" }}>
                  {clientName}
                </Text>
                <Text style={{ margin: "0", fontSize: "14px", opacity: 0.8 }}>
                  {startDate.toLocaleDateString()} –{" "}
                  {endDate.toLocaleDateString()}
                </Text>
              </div>
            </Row>
          </Section>

          {/* Executive Summary */}
          <Section style={contentSection}>
            <Text style={heading2}>Executive Summary</Text>
            <table style={summaryStatsTable}>
              <tbody>
                <tr>
                  <td style={summaryStatCell}>
                    <div style={summaryStatValue}>{totalProviders}</div>
                    <div style={summaryStatLabel}>Active Providers</div>
                  </td>
                  <td style={summaryStatCell}>
                    <div style={summaryStatValue}>{totalIncidents}</div>
                    <div style={summaryStatLabel}>Incidents (30d)</div>
                  </td>
                  <td style={summaryStatCell}>
                    <div style={summaryStatValue}>{breachCount}</div>
                    <div style={summaryStatLabel}>SLA Breaches</div>
                  </td>
                  <td style={summaryStatCell}>
                    <div style={summaryStatValue}>
                      {avgMTR.toFixed(0)} min
                    </div>
                    <div style={summaryStatLabel}>Avg MTTR</div>
                  </td>
                </tr>
              </tbody>
            </table>

            <Hr style={divider} />

            <Text style={heading3}>Key Findings</Text>
            <ul style={findingsList}>
              {executiveSummary.criticalFindings.map((finding, idx) => (
                <li key={idx} style={findingItem}>
                  {finding}
                </li>
              ))}
            </ul>

            <Text style={heading3}>Recommendations</Text>
            <ul style={findingsList}>
              {executiveSummary.recommendations.map((rec, idx) => (
                <li key={idx} style={recommendationItem}>
                  {rec}
                </li>
              ))}
            </ul>
          </Section>

          {/* Grade Distribution */}
          <Section style={contentSection}>
            <Text style={heading2}>Overall Grade Distribution</Text>
            <table style={gradeDistTable}>
              <tbody>
                <tr>
                  <td style={gradeDistCell}>
                    <div
                      style={{
                        ...gradeDistValue,
                        color: gradeColors.excellent,
                      }}
                    >
                      {excellentCount}
                    </div>
                    <div style={gradeDistLabel}>Excellent</div>
                  </td>
                  <td style={gradeDistCell}>
                    <div
                      style={{ ...gradeDistValue, color: gradeColors.good }}
                    >
                      {goodCount}
                    </div>
                    <div style={gradeDistLabel}>Good</div>
                  </td>
                  <td style={gradeDistCell}>
                    <div
                      style={{
                        ...gradeDistValue,
                        color: gradeColors.concerning,
                      }}
                    >
                      {concerningCount}
                    </div>
                    <div style={gradeDistLabel}>Concerning</div>
                  </td>
                  <td style={gradeDistCell}>
                    <div
                      style={{
                        ...gradeDistValue,
                        color: gradeColors.unacceptable,
                      }}
                    >
                      {unacceptableCount}
                    </div>
                    <div style={gradeDistLabel}>Unacceptable</div>
                  </td>
                </tr>
              </tbody>
            </table>
          </Section>

          {/* Detailed Scorecards */}
          <Section style={contentSection}>
            <Text style={heading2}>Provider Details</Text>
            {scorecards.map((scorecard, idx) => (
              <div key={idx} style={scorecardBox}>
                <div style={scorecardHeader}>
                  <div>
                    <Text style={scorecardTitle}>{scorecard.providerName}</Text>
                    <Text style={scorecardGrade}>
                      {gradeEmojis[scorecard.grade]} {scorecard.grade}
                    </Text>
                  </div>
                  <div
                    style={{
                      fontSize: "32px",
                      fontWeight: "bold",
                      color: gradeColors[scorecard.grade],
                    }}
                  >
                    {scorecard.composite_score.toFixed(0)}
                  </div>
                </div>

                <table style={detailTable}>
                  <tbody>
                    <tr>
                      <td style={detailLabelCell}>Uptime (Actual)</td>
                      <td style={detailValueCell}>
                        <span
                          style={{
                            color:
                              scorecard.uptime_pct >= scorecard.sla_target_pct
                                ? "#06A77D"
                                : "#E63946",
                            fontWeight: "bold",
                          }}
                        >
                          {scorecard.uptime_pct.toFixed(3)}%
                        </span>
                      </td>
                      <td style={detailLabelCell}>SLA Target</td>
                      <td style={detailValueCell}>
                        {scorecard.sla_target_pct.toFixed(3)}%
                      </td>
                    </tr>
                    <tr>
                      <td style={detailLabelCell}>SLA Status</td>
                      <td style={detailValueCell}>
                        <span
                          style={{
                            backgroundColor:
                              scorecard.sla_status === "compliant"
                                ? "#DCFCE7"
                                : scorecard.sla_status === "at_risk"
                                  ? "#FEF3C7"
                                  : "#FEE2E2",
                            color:
                              scorecard.sla_status === "compliant"
                                ? "#166534"
                                : scorecard.sla_status === "at_risk"
                                  ? "#92400E"
                                  : "#991B1B",
                            padding: "4px 8px",
                            borderRadius: "4px",
                            fontSize: "12px",
                            fontWeight: "bold",
                          }}
                        >
                          {scorecard.sla_status.toUpperCase()}
                        </span>
                      </td>
                      <td style={detailLabelCell}>Incidents (30d)</td>
                      <td style={detailValueCell}>
                        {scorecard.incident_count_30d}
                      </td>
                    </tr>
                    <tr>
                      <td style={detailLabelCell}>Avg. Latency (p95)</td>
                      <td style={detailValueCell}>
                        {scorecard.p95_latency_ms.toFixed(0)}ms
                      </td>
                      <td style={detailLabelCell}>Cost per Call</td>
                      <td style={detailValueCell}>
                        ${scorecard.cost_per_call_usd.toFixed(4)}
                      </td>
                    </tr>
                    <tr>
                      <td style={detailLabelCell}>Mean Time to Resolve</td>
                      <td style={detailValueCell}>
                        {scorecard.mean_time_to_resolve_minutes.toFixed(0)}{" "}
                        minutes
                      </td>
                      <td style={detailLabelCell}>Recommendation</td>
                      <td style={detailValueCell}>
                        <span
                          style={{
                            backgroundColor:
                              scorecard.renewal_recommendation === "renew"
                                ? "#DCFCE7"
                                : scorecard.renewal_recommendation === "renegotiate"
                                  ? "#FEF3C7"
                                  : "#FEE2E2",
                            color:
                              scorecard.renewal_recommendation === "renew"
                                ? "#166534"
                                : scorecard.renewal_recommendation === "renegotiate"
                                  ? "#92400E"
                                  : "#991B1B",
                            padding: "4px 8px",
                            borderRadius: "4px",
                            fontSize: "12px",
                            fontWeight: "bold",
                          }}
                        >
                          {scorecard.renewal_recommendation.toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            ))}
          </Section>

          {/* Next Steps */}
          <Section style={contentSection}>
            <Text style={heading2}>Next Steps</Text>
            <div style={nextStepsBox}>
              <Text style={{ margin: "0 0 12px 0", fontWeight: "bold" }}>
                For providers with concerning or unacceptable grades:
              </Text>
              <ul style={stepslist}>
                <li style={stepItem}>
                  Schedule vendor business reviews (QBRs) this month
                </li>
                <li style={stepItem}>
                  Request detailed incident post-mortems and remediation plans
                </li>
                <li style={stepItem}>
                  Evaluate fallback or replacement options
                </li>
                <li style={stepItem}>
                  Review and update SLAs based on actual performance
                </li>
              </ul>
            </div>
          </Section>

          {/* CTA */}
          <Section style={contentSection}>
            <Hr style={divider} />
            <div style={{ textAlign: "center", padding: "24px 0" }}>
              <Button style={buttonPrimary} href={dashboardUrl}>
                View Interactive Dashboard
              </Button>
            </div>
          </Section>

          {/* Footer */}
          <Section style={footer}>
            <Text style={footerText}>
              This report is generated automatically each month by Integration
              Health Monitor.
              <br />
              For questions or to customize reports,{" "}
              <Link href={dashboardUrl} style={footerLink}>
                contact your admin
              </Link>
            </Text>
          </Section>
        </Container>
      </Body>
    </Html>
  );
};

export default ScorecardReportEmail;

// Styles
const main = {
  backgroundColor: "#f3f4f6",
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "Roboto", "Oxygen", "Ubuntu", "Cantarell", "Fira Sans", "Droid Sans", "Helvetica Neue", sans-serif',
};

const container = {
  backgroundColor: "#ffffff",
  margin: "0 auto",
  padding: "20px 0",
  marginBottom: "64px",
  borderRadius: "4px",
  overflow: "hidden" as const,
};

const headerSection = {
  width: "100%",
};

const contentSection = {
  padding: "32px",
  borderBottom: "1px solid #e5e7eb",
};

const heading2 = {
  fontSize: "18px",
  fontWeight: "bold",
  color: "#1f2937",
  margin: "0 0 20px 0",
};

const heading3 = {
  fontSize: "14px",
  fontWeight: "bold",
  color: "#1f2937",
  margin: "16px 0 12px 0",
};

const summaryStatsTable = {
  width: "100%",
  borderCollapse: "collapse" as const,
  marginBottom: "24px",
};

const summaryStatCell = {
  padding: "16px",
  textAlign: "center" as const,
  borderRight: "1px solid #f3f4f6",
  flex: 1,
};

const summaryStatValue = {
  fontSize: "24px",
  fontWeight: "bold",
  color: "#1f2937",
  margin: "0 0 8px 0",
};

const summaryStatLabel = {
  fontSize: "12px",
  color: "#6b7280",
  margin: "0",
};

const findingsList = {
  margin: "0",
  paddingLeft: "20px",
  color: "#1f2937",
  fontSize: "14px",
};

const findingItem = {
  margin: "8px 0",
  lineHeight: "1.6",
};

const recommendationItem = {
  margin: "8px 0",
  lineHeight: "1.6",
  color: "#0f766e",
};

const gradeDistTable = {
  width: "100%",
  borderCollapse: "collapse" as const,
};

const gradeDistCell = {
  padding: "20px",
  textAlign: "center" as const,
  borderRight: "1px solid #f3f4f6",
  flex: 1,
};

const gradeDistValue = {
  fontSize: "32px",
  fontWeight: "bold",
  margin: "0 0 8px 0",
};

const gradeDistLabel = {
  fontSize: "12px",
  color: "#6b7280",
  margin: "0",
  fontWeight: "bold" as const,
};

const scorecardBox = {
  backgroundColor: "#f9fafb",
  padding: "20px",
  borderRadius: "8px",
  marginBottom: "16px",
  border: "1px solid #e5e7eb",
};

const scorecardHeader = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  marginBottom: "16px",
  paddingBottom: "16px",
  borderBottom: "1px solid #e5e7eb",
};

const scorecardTitle = {
  fontSize: "16px",
  fontWeight: "bold",
  color: "#1f2937",
  margin: "0 0 4px 0",
};

const scorecardGrade = {
  fontSize: "14px",
  color: "#6b7280",
  margin: "0",
};

const detailTable = {
  width: "100%",
  borderCollapse: "collapse" as const,
  fontSize: "13px",
};

const detailLabelCell = {
  padding: "8px 12px",
  color: "#6b7280",
  fontWeight: "bold" as const,
  width: "35%",
  borderBottom: "1px solid #e5e7eb",
};

const detailValueCell = {
  padding: "8px 12px",
  color: "#1f2937",
  borderBottom: "1px solid #e5e7eb",
  width: "15%",
};

const nextStepsBox = {
  backgroundColor: "#f0f9ff",
  padding: "16px",
  borderRadius: "6px",
  borderLeft: "4px solid #0284c7",
};

const stepslist = {
  margin: "0",
  paddingLeft: "20px",
  color: "#1f2937",
  fontSize: "14px",
};

const stepItem = {
  margin: "6px 0",
  lineHeight: "1.5",
};

const divider = {
  borderTop: "1px solid #e5e7eb",
  margin: "0",
};

const buttonPrimary = {
  backgroundColor: "#3b82f6",
  color: "#ffffff",
  padding: "12px 24px",
  borderRadius: "6px",
  textDecoration: "none",
  fontWeight: "bold",
  fontSize: "14px",
  display: "inline-block",
};

const footer = {
  padding: "32px 32px",
  backgroundColor: "#f9fafb",
};

const footerText = {
  color: "#6b7280",
  fontSize: "12px",
  margin: "0",
  lineHeight: "1.6",
};

const footerLink = {
  color: "#3b82f6",
  textDecoration: "underline",
};
