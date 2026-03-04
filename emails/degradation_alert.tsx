import React from "react";
import {
  Body,
  Button,
  Container,
  Head,
  Hr,
  Html,
  Img,
  Link,
  Preview,
  Row,
  Section,
  Text,
} from "@react-email/components";

interface DegradationAlertProps {
  incidentId: string;
  providerName: string;
  providerId: string;
  severity: "p0" | "p1" | "p2" | "p3";
  anomalyType: string;
  currentValue: number;
  baselineValue: number;
  thresholdValue: number;
  affectedFlows: string[];
  blastRadius: string;
  estimatedUsersAffected: number;
  detectedAt: string;
  dashboardUrl: string;
  slackChannelUrl: string;
}

const severityColors = {
  p0: "#E63946",
  p1: "#F77F00",
  p2: "#FCBF49",
  p3: "#06A77D",
};

const severityLabels = {
  p0: "CRITICAL",
  p1: "MAJOR",
  p2: "MINOR",
  p3: "INFO",
};

export const DegradationAlertEmail: React.FC<DegradationAlertProps> = ({
  incidentId,
  providerName,
  providerId,
  severity,
  anomalyType,
  currentValue,
  baselineValue,
  thresholdValue,
  affectedFlows,
  blastRadius,
  estimatedUsersAffected,
  detectedAt,
  dashboardUrl,
  slackChannelUrl,
}) => {
  const severityColor = severityColors[severity];
  const severityLabel = severityLabels[severity];
  const varianceFromBaseline = (
    ((currentValue - baselineValue) / baselineValue) *
    100
  ).toFixed(1);

  return (
    <Html>
      <Head />
      <Preview>
        {severityLabel}: {providerName} API degradation detected
      </Preview>
      <Body style={main}>
        <Container style={container}>
          {/* Header */}
          <Section style={headerSection}>
            <Row>
              <div
                style={{
                  backgroundColor: severityColor,
                  padding: "16px",
                  borderRadius: "8px 8px 0 0",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div style={{ color: "white" }}>
                  <Text
                    style={{
                      margin: "0 0 4px 0",
                      fontSize: "12px",
                      fontWeight: "bold",
                      letterSpacing: "1px",
                    }}
                  >
                    {severityLabel}
                  </Text>
                  <Text
                    style={{
                      margin: "0",
                      fontSize: "20px",
                      fontWeight: "bold",
                    }}
                  >
                    Integration Incident Detected
                  </Text>
                </div>
                <div
                  style={{
                    fontSize: "32px",
                    color: "white",
                  }}
                >
                  ⚠️
                </div>
              </div>
            </Row>
          </Section>

          {/* Incident Summary */}
          <Section style={contentSection}>
            <Text style={heading2}>Incident Details</Text>
            <table style={summaryTable}>
              <tbody>
                <tr>
                  <td style={summaryLabel}>Incident ID</td>
                  <td style={summaryValue}>{incidentId}</td>
                </tr>
                <tr>
                  <td style={summaryLabel}>Provider</td>
                  <td style={summaryValue}>
                    {providerName} <code style={codeStyle}>{providerId}</code>
                  </td>
                </tr>
                <tr>
                  <td style={summaryLabel}>Anomaly Type</td>
                  <td style={summaryValue}>{anomalyType}</td>
                </tr>
                <tr>
                  <td style={summaryLabel}>Severity</td>
                  <td style={summaryValue}>
                    <span
                      style={{
                        backgroundColor: severityColor,
                        color: "white",
                        padding: "4px 8px",
                        borderRadius: "4px",
                        fontWeight: "bold",
                        fontSize: "12px",
                      }}
                    >
                      {severityLabel}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td style={summaryLabel}>Detected At</td>
                  <td style={summaryValue}>
                    {new Date(detectedAt).toLocaleString()}
                  </td>
                </tr>
              </tbody>
            </table>
          </Section>

          {/* Metrics */}
          <Section style={contentSection}>
            <Text style={heading2}>Performance Metrics</Text>
            <table style={metricsTable}>
              <tbody>
                <tr>
                  <td style={metricCell}>
                    <div style={metricValue}>{currentValue.toFixed(2)}</div>
                    <div style={metricLabel}>Current {anomalyType}</div>
                  </td>
                  <td style={metricCell}>
                    <div style={metricValue}>{baselineValue.toFixed(2)}</div>
                    <div style={metricLabel}>Baseline {anomalyType}</div>
                  </td>
                  <td style={metricCell}>
                    <div style={metricValue}>{thresholdValue.toFixed(2)}</div>
                    <div style={metricLabel}>Threshold</div>
                  </td>
                  <td style={metricCell}>
                    <div
                      style={{
                        ...metricValue,
                        color: varianceFromBaseline > 0 ? "#E63946" : "#06A77D",
                      }}
                    >
                      {varianceFromBaseline}%
                    </div>
                    <div style={metricLabel}>Variance</div>
                  </td>
                </tr>
              </tbody>
            </table>
          </Section>

          {/* Impact */}
          <Section style={contentSection}>
            <Text style={heading2}>Impact Assessment</Text>
            <table style={impactTable}>
              <tbody>
                <tr>
                  <td style={impactLabel}>Affected Flows:</td>
                  <td style={impactValue}>{affectedFlows.join(", ")}</td>
                </tr>
                <tr>
                  <td style={impactLabel}>Blast Radius:</td>
                  <td style={impactValue}>
                    <span style={{ fontWeight: "bold" }}>{blastRadius}</span>
                  </td>
                </tr>
                <tr>
                  <td style={impactLabel}>Estimated Users Affected:</td>
                  <td style={impactValue}>
                    {estimatedUsersAffected.toLocaleString()}
                  </td>
                </tr>
              </tbody>
            </table>
          </Section>

          {/* Action Items */}
          <Section style={contentSection}>
            <Text style={heading2}>Recommended Actions</Text>
            {severity === "p0" && (
              <div style={actionBox}>
                <Text style={{ margin: "0 0 8px 0", fontWeight: "bold" }}>
                  🚨 IMMEDIATE ACTION REQUIRED
                </Text>
                <ul style={actionList}>
                  <li>Page on-call engineer immediately</li>
                  <li>Check provider status page for updates</li>
                  <li>Activate fallback provider if available</li>
                  <li>Prepare customer communication</li>
                </ul>
              </div>
            )}
            {severity === "p1" && (
              <div style={actionBox}>
                <Text style={{ margin: "0 0 8px 0", fontWeight: "bold" }}>
                  ⚠️ HIGH PRIORITY
                </Text>
                <ul style={actionList}>
                  <li>Notify engineering team</li>
                  <li>Evaluate fallback options</li>
                  <li>Monitor closely for escalation</li>
                  <li>Prepare incident response plan</li>
                </ul>
              </div>
            )}
            {(severity === "p2" || severity === "p3") && (
              <div style={actionBox}>
                <Text style={{ margin: "0 0 8px 0", fontWeight: "bold" }}>
                  ℹ️ Monitoring
                </Text>
                <ul style={actionList}>
                  <li>Incident is being monitored automatically</li>
                  <li>
                    You will be alerted if severity increases or duration
                    extends
                  </li>
                  <li>Review details in dashboard for insights</li>
                </ul>
              </div>
            )}
          </Section>

          {/* Quick Links */}
          <Section style={contentSection}>
            <Hr style={divider} />
            <div style={linksContainer}>
              <Button style={buttonPrimary} href={dashboardUrl}>
                View in Dashboard
              </Button>
              <Button style={buttonSecondary} href={slackChannelUrl}>
                Discuss on Slack
              </Button>
            </div>
          </Section>

          {/* Footer */}
          <Section style={footer}>
            <Text style={footerText}>
              This is an automated alert from Integration Health Monitor.
              <br />
              <Link href={dashboardUrl} style={footerLink}>
                Manage alerts and preferences
              </Link>
            </Text>
          </Section>
        </Container>
      </Body>
    </Html>
  );
};

export default DegradationAlertEmail;

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
  padding: "32px 32px",
  borderBottom: "1px solid #e5e7eb",
};

const heading2 = {
  fontSize: "16px",
  fontWeight: "bold",
  color: "#1f2937",
  margin: "0 0 16px 0",
};

const summaryTable = {
  width: "100%",
  borderCollapse: "collapse" as const,
};

const summaryLabel = {
  padding: "12px 0",
  fontWeight: "bold",
  color: "#6b7280",
  fontSize: "14px",
  width: "140px",
  borderBottom: "1px solid #f3f4f6",
};

const summaryValue = {
  padding: "12px 16px",
  color: "#1f2937",
  fontSize: "14px",
  borderBottom: "1px solid #f3f4f6",
};

const codeStyle = {
  backgroundColor: "#f3f4f6",
  padding: "2px 6px",
  borderRadius: "3px",
  fontSize: "12px",
  fontFamily: "monospace",
  marginLeft: "8px",
};

const metricsTable = {
  width: "100%",
  borderCollapse: "collapse" as const,
};

const metricCell = {
  padding: "16px",
  textAlign: "center" as const,
  borderRight: "1px solid #f3f4f6",
  flex: 1,
};

const metricValue = {
  fontSize: "24px",
  fontWeight: "bold",
  color: "#1f2937",
  margin: "0 0 8px 0",
};

const metricLabel = {
  fontSize: "12px",
  color: "#6b7280",
  margin: "0",
};

const impactTable = {
  width: "100%",
  borderCollapse: "collapse" as const,
};

const impactLabel = {
  padding: "12px 0",
  fontWeight: "bold",
  color: "#6b7280",
  fontSize: "14px",
  width: "180px",
  borderBottom: "1px solid #f3f4f6",
};

const impactValue = {
  padding: "12px 16px",
  color: "#1f2937",
  fontSize: "14px",
  borderBottom: "1px solid #f3f4f6",
};

const actionBox = {
  backgroundColor: "#f9fafb",
  padding: "16px",
  borderRadius: "6px",
  borderLeft: "4px solid #f77f00",
  marginBottom: "16px",
};

const actionList = {
  margin: "0",
  paddingLeft: "20px",
  color: "#1f2937",
  fontSize: "14px",
};

const linksContainer = {
  display: "flex",
  gap: "12px",
  justifyContent: "center",
  padding: "24px 0",
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

const buttonSecondary = {
  backgroundColor: "#e5e7eb",
  color: "#1f2937",
  padding: "12px 24px",
  borderRadius: "6px",
  textDecoration: "none",
  fontWeight: "bold",
  fontSize: "14px",
  display: "inline-block",
};

const divider = {
  borderTop: "1px solid #e5e7eb",
  margin: "0",
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
