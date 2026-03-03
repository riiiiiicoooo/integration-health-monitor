/**
 * Integration Health Monitor — Dashboard
 *
 * PM-authored reference implementation. This is the single-pane-of-glass
 * view that replaced "check each provider's status page manually."
 *
 * Before this existed, diagnosing an integration issue required:
 * 1. Checking 6+ provider status pages
 * 2. Grepping application logs
 * 3. Asking engineers "is anything broken?"
 * 4. Cross-referencing support ticket spikes
 *
 * After: glance at the dashboard. Red = something's wrong. Click for details.
 *
 * Built with synthetic data for portfolio demonstration. In production,
 * this consumes the API endpoints served by the Python modules.
 */

import { useState, useEffect, useCallback } from "react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  Legend,
} from "recharts";

// ============================================================================
// SYNTHETIC DATA
// ============================================================================

const PROVIDERS = [
  {
    id: "stripe",
    name: "Stripe",
    category: "Financial Connectivity",
    blastRadius: "P0",
    circuitState: "closed",
    health: "healthy",
    errorRate: 0.3,
    p50: 780,
    p95: 1020,
    requests: 12840,
    webhookDelivery: 99.94,
    uptimePct: 99.99,
    slaGuarantee: 99.99,
    slaStatus: "compliant",
    score: 96,
  },
  {
    id: "kyc_provider",
    name: "KYC Provider",
    category: "Identity Verification",
    blastRadius: "P1",
    circuitState: "closed",
    health: "degraded",
    errorRate: 4.8,
    p50: 5200,
    p95: 11200,
    requests: 3420,
    webhookDelivery: 97.1,
    uptimePct: 99.71,
    slaGuarantee: 99.95,
    slaStatus: "breached",
    score: 62,
  },
  {
    id: "plaid",
    name: "Plaid",
    category: "Financial Connectivity",
    blastRadius: "P1",
    circuitState: "closed",
    health: "healthy",
    errorRate: 1.2,
    p50: 1180,
    p95: 2400,
    requests: 5100,
    webhookDelivery: 98.8,
    uptimePct: 99.91,
    slaGuarantee: 99.9,
    slaStatus: "compliant",
    score: 84,
  },
  {
    id: "credit_bureau",
    name: "Credit Bureau",
    category: "Compliance & Risk",
    blastRadius: "P0",
    circuitState: "closed",
    health: "healthy",
    errorRate: 0.1,
    p50: 880,
    p95: 1400,
    requests: 2200,
    webhookDelivery: null,
    uptimePct: 99.98,
    slaGuarantee: 99.99,
    slaStatus: "compliant",
    score: 94,
  },
  {
    id: "twilio",
    name: "Twilio",
    category: "Communication",
    blastRadius: "P1",
    circuitState: "closed",
    health: "healthy",
    errorRate: 0.6,
    p50: 720,
    p95: 1600,
    requests: 8900,
    webhookDelivery: 99.2,
    uptimePct: 99.96,
    slaGuarantee: 99.95,
    slaStatus: "compliant",
    score: 89,
  },
  {
    id: "sendgrid",
    name: "SendGrid",
    category: "Communication",
    blastRadius: "P3",
    circuitState: "closed",
    health: "healthy",
    errorRate: 0.2,
    p50: 280,
    p95: 600,
    requests: 15200,
    webhookDelivery: 98.5,
    uptimePct: 99.95,
    slaGuarantee: 99.95,
    slaStatus: "compliant",
    score: 87,
  },
];

const generateLatencyTrend = (baseP50, baseP95, variance, degrading) => {
  return Array.from({ length: 24 }, (_, i) => {
    const hour = `${String(i).padStart(2, "0")}:00`;
    const drift = degrading ? i * (variance * 0.4) : 0;
    const p50 = Math.max(100, baseP50 + (Math.random() - 0.5) * variance + drift);
    const p95 = Math.max(200, baseP95 + (Math.random() - 0.5) * variance * 2.5 + drift * 2);
    return { hour, p50: Math.round(p50), p95: Math.round(p95) };
  });
};

const generateWebhookTrend = (baseRate, dropWindow) => {
  return Array.from({ length: 24 }, (_, i) => {
    const hour = `${String(i).padStart(2, "0")}:00`;
    const inDrop = dropWindow && i >= dropWindow[0] && i <= dropWindow[1];
    const rate = inDrop
      ? baseRate - 15 - Math.random() * 10
      : baseRate - Math.random() * 2;
    return { hour, rate: Math.round(Math.min(100, Math.max(0, rate)) * 10) / 10 };
  });
};

const LATENCY_DATA = {
  stripe: generateLatencyTrend(780, 1020, 200, false),
  kyc_provider: generateLatencyTrend(3800, 8500, 1500, true),
  plaid: generateLatencyTrend(1180, 2400, 400, false),
  credit_bureau: generateLatencyTrend(880, 1400, 150, false),
  twilio: generateLatencyTrend(720, 1600, 300, false),
  sendgrid: generateLatencyTrend(280, 600, 100, false),
};

const WEBHOOK_DATA = {
  stripe: generateWebhookTrend(99.9, null),
  kyc_provider: generateWebhookTrend(97.5, [14, 17]),
  plaid: generateWebhookTrend(99.0, [8, 9]),
  twilio: generateWebhookTrend(99.3, null),
  sendgrid: generateWebhookTrend(98.5, null),
};

const FUNNEL_DATA = [
  { step: "Phone Verification", reached: 1000, completed: 952, dropRate: 4.8, apiCorrelated: 22 },
  { step: "Identity Verification", reached: 952, completed: 798, dropRate: 16.2, apiCorrelated: 83 },
  { step: "Bank Linking", reached: 798, completed: 746, dropRate: 6.5, apiCorrelated: 41 },
  { step: "Credit Check", reached: 746, completed: 716, dropRate: 4.0, apiCorrelated: 15 },
  { step: "Disbursement", reached: 716, completed: 672, dropRate: 6.1, apiCorrelated: 28 },
];

const INCIDENTS = [
  {
    id: "INC-0047",
    provider: "KYC Provider",
    type: "Latency Degradation",
    severity: "P1",
    status: "monitoring",
    detected: "14:23",
    duration: "2h 37m",
    flows: ["user_onboarding"],
  },
];

// ============================================================================
// THEME
// ============================================================================

const COLORS = {
  bg: "#0a0e17",
  surface: "#111827",
  surfaceRaised: "#1a2233",
  border: "#1e2d3d",
  borderSubtle: "#162030",
  text: "#e2e8f0",
  textMuted: "#8899aa",
  textDim: "#556677",

  healthy: "#10b981",
  healthyMuted: "#065f46",
  degraded: "#f59e0b",
  degradedMuted: "#78350f",
  unhealthy: "#ef4444",
  unhealthyMuted: "#7f1d1d",
  unknown: "#6b7280",

  p0: "#ef4444",
  p1: "#f59e0b",
  p2: "#3b82f6",
  p3: "#6b7280",

  accent: "#38bdf8",
  accentDim: "#0c4a6e",
  chartLine1: "#38bdf8",
  chartLine2: "#f59e0b",
  chartArea: "rgba(56, 189, 248, 0.08)",
};

// ============================================================================
// COMPONENTS
// ============================================================================

const StatusDot = ({ status }) => {
  const color = {
    healthy: COLORS.healthy,
    degraded: COLORS.degraded,
    unhealthy: COLORS.unhealthy,
  }[status] || COLORS.unknown;

  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        backgroundColor: color,
        boxShadow: `0 0 6px ${color}60`,
      }}
    />
  );
};

const Badge = ({ children, variant = "default" }) => {
  const styles = {
    p0: { bg: COLORS.unhealthyMuted, color: COLORS.unhealthy, border: `1px solid ${COLORS.unhealthy}40` },
    p1: { bg: COLORS.degradedMuted, color: COLORS.degraded, border: `1px solid ${COLORS.degraded}40` },
    p2: { bg: "#1e3a5f", color: COLORS.p2, border: `1px solid ${COLORS.p2}40` },
    p3: { bg: "#1f2937", color: COLORS.p3, border: `1px solid ${COLORS.p3}40` },
    healthy: { bg: COLORS.healthyMuted, color: COLORS.healthy, border: `1px solid ${COLORS.healthy}40` },
    degraded: { bg: COLORS.degradedMuted, color: COLORS.degraded, border: `1px solid ${COLORS.degraded}40` },
    unhealthy: { bg: COLORS.unhealthyMuted, color: COLORS.unhealthy, border: `1px solid ${COLORS.unhealthy}40` },
    default: { bg: COLORS.surfaceRaised, color: COLORS.textMuted, border: `1px solid ${COLORS.border}` },
    compliant: { bg: COLORS.healthyMuted, color: COLORS.healthy, border: `1px solid ${COLORS.healthy}40` },
    breached: { bg: COLORS.unhealthyMuted, color: COLORS.unhealthy, border: `1px solid ${COLORS.unhealthy}40` },
    at_risk: { bg: COLORS.degradedMuted, color: COLORS.degraded, border: `1px solid ${COLORS.degraded}40` },
    monitoring: { bg: "#1e3a5f", color: COLORS.accent, border: `1px solid ${COLORS.accent}40` },
  };
  const s = styles[variant] || styles.default;

  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        backgroundColor: s.bg,
        color: s.color,
        border: s.border,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      }}
    >
      {children}
    </span>
  );
};

const Card = ({ children, style = {} }) => (
  <div
    style={{
      backgroundColor: COLORS.surface,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 8,
      padding: 20,
      ...style,
    }}
  >
    {children}
  </div>
);

const SectionTitle = ({ children, subtitle }) => (
  <div style={{ marginBottom: 16 }}>
    <h2
      style={{
        fontSize: 13,
        fontWeight: 600,
        color: COLORS.textMuted,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        margin: 0,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      }}
    >
      {children}
    </h2>
    {subtitle && (
      <p style={{ fontSize: 12, color: COLORS.textDim, margin: "4px 0 0 0" }}>
        {subtitle}
      </p>
    )}
  </div>
);

const MetricValue = ({ value, unit, label, status }) => {
  const color = status
    ? { healthy: COLORS.healthy, degraded: COLORS.degraded, unhealthy: COLORS.unhealthy }[status] || COLORS.text
    : COLORS.text;

  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 28, fontWeight: 700, color, fontFamily: "'JetBrains Mono', monospace", lineHeight: 1 }}>
        {value}
        {unit && <span style={{ fontSize: 13, fontWeight: 400, color: COLORS.textMuted, marginLeft: 2 }}>{unit}</span>}
      </div>
      <div style={{ fontSize: 11, color: COLORS.textDim, marginTop: 4, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </div>
    </div>
  );
};

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload || !payload.length) return null;
  return (
    <div
      style={{
        backgroundColor: COLORS.surfaceRaised,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 6,
        padding: "8px 12px",
        fontSize: 12,
        color: COLORS.text,
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      <div style={{ color: COLORS.textMuted, marginBottom: 4 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>
          {p.name}: {typeof p.value === "number" ? p.value.toLocaleString() : p.value}
          {p.name?.includes("ms") || p.dataKey?.includes("p5") || p.dataKey?.includes("p9") ? "ms" : ""}
          {p.dataKey === "rate" ? "%" : ""}
        </div>
      ))}
    </div>
  );
};

// ============================================================================
// MAIN DASHBOARD
// ============================================================================

export default function Dashboard() {
  const [selectedProvider, setSelectedProvider] = useState(null);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [activeView, setActiveView] = useState("overview");

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 60000);
    return () => clearInterval(timer);
  }, []);

  const healthyCount = PROVIDERS.filter((p) => p.health === "healthy").length;
  const degradedCount = PROVIDERS.filter((p) => p.health === "degraded").length;
  const unhealthyCount = PROVIDERS.filter((p) => p.health === "unhealthy").length;
  const activeIncidents = INCIDENTS.length;

  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "webhooks", label: "Webhooks" },
    { id: "funnel", label: "Onboarding Funnel" },
    { id: "scorecard", label: "Provider Scorecard" },
  ];

  return (
    <div
      style={{
        backgroundColor: COLORS.bg,
        color: COLORS.text,
        minHeight: "100vh",
        fontFamily: "'Inter', -apple-system, sans-serif",
        fontSize: 14,
      }}
    >
      <link
        href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />

      {/* Header */}
      <div
        style={{
          borderBottom: `1px solid ${COLORS.border}`,
          padding: "16px 24px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div>
            <h1
              style={{
                fontSize: 16,
                fontWeight: 700,
                margin: 0,
                letterSpacing: "-0.01em",
              }}
            >
              Integration Health Monitor
            </h1>
            <span style={{ fontSize: 11, color: COLORS.textDim }}>
              Lending Client — 6 providers, 3 flows
            </span>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{ display: "flex", gap: 12 }}>
            <span style={{ color: COLORS.healthy, fontSize: 13, fontWeight: 500 }}>
              ● {healthyCount} healthy
            </span>
            {degradedCount > 0 && (
              <span style={{ color: COLORS.degraded, fontSize: 13, fontWeight: 500 }}>
                ● {degradedCount} degraded
              </span>
            )}
            {unhealthyCount > 0 && (
              <span style={{ color: COLORS.unhealthy, fontSize: 13, fontWeight: 500 }}>
                ● {unhealthyCount} unhealthy
              </span>
            )}
          </div>
          <div
            style={{
              fontSize: 12,
              color: COLORS.textMuted,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            {currentTime.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </div>
        </div>
      </div>

      {/* Active Incident Banner */}
      {activeIncidents > 0 && (
        <div
          style={{
            backgroundColor: COLORS.degradedMuted,
            borderBottom: `1px solid ${COLORS.degraded}30`,
            padding: "10px 24px",
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: 13,
          }}
        >
          <span style={{ color: COLORS.degraded, fontWeight: 600 }}>⚠ ACTIVE INCIDENT</span>
          <span style={{ color: COLORS.text }}>
            {INCIDENTS[0].id} — {INCIDENTS[0].provider}: {INCIDENTS[0].type}
          </span>
          <Badge variant={INCIDENTS[0].severity.toLowerCase()}>{INCIDENTS[0].severity}</Badge>
          <Badge variant={INCIDENTS[0].status}>{INCIDENTS[0].status}</Badge>
          <span style={{ color: COLORS.textMuted, marginLeft: "auto", fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
            Duration: {INCIDENTS[0].duration}
          </span>
        </div>
      )}

      {/* Tabs */}
      <div
        style={{
          borderBottom: `1px solid ${COLORS.border}`,
          padding: "0 24px",
          display: "flex",
          gap: 0,
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveView(tab.id)}
            style={{
              padding: "12px 20px",
              fontSize: 13,
              fontWeight: activeView === tab.id ? 600 : 400,
              color: activeView === tab.id ? COLORS.accent : COLORS.textMuted,
              background: "none",
              border: "none",
              borderBottom: activeView === tab.id ? `2px solid ${COLORS.accent}` : "2px solid transparent",
              cursor: "pointer",
              transition: "all 0.15s",
              fontFamily: "inherit",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ padding: 24 }}>
        {activeView === "overview" && <OverviewTab selectedProvider={selectedProvider} onSelectProvider={setSelectedProvider} />}
        {activeView === "webhooks" && <WebhooksTab />}
        {activeView === "funnel" && <FunnelTab />}
        {activeView === "scorecard" && <ScorecardTab />}
      </div>
    </div>
  );
}

// ============================================================================
// OVERVIEW TAB
// ============================================================================

function OverviewTab({ selectedProvider, onSelectProvider }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Provider Cards Grid */}
      <SectionTitle subtitle="Click a provider for detailed metrics">Provider Status</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
        {PROVIDERS.map((p) => (
          <Card
            key={p.id}
            style={{
              cursor: "pointer",
              border: selectedProvider === p.id ? `1px solid ${COLORS.accent}` : `1px solid ${COLORS.border}`,
              transition: "border-color 0.15s",
            }}
          >
            <div onClick={() => onSelectProvider(selectedProvider === p.id ? null : p.id)}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <StatusDot status={p.health} />
                  <span style={{ fontWeight: 600, fontSize: 14 }}>{p.name}</span>
                </div>
                <Badge variant={p.blastRadius.toLowerCase()}>{p.blastRadius}</Badge>
              </div>

              <div style={{ fontSize: 11, color: COLORS.textDim, marginBottom: 12 }}>{p.category}</div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                <div>
                  <div style={{ fontSize: 11, color: COLORS.textDim }}>Error Rate</div>
                  <div
                    style={{
                      fontSize: 16,
                      fontWeight: 600,
                      color: p.errorRate > 5 ? COLORS.unhealthy : p.errorRate > 2 ? COLORS.degraded : COLORS.healthy,
                      fontFamily: "'JetBrains Mono', monospace",
                    }}
                  >
                    {p.errorRate}%
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: COLORS.textDim }}>P95 Latency</div>
                  <div
                    style={{
                      fontSize: 16,
                      fontWeight: 600,
                      color: p.p95 > 5000 ? COLORS.degraded : COLORS.text,
                      fontFamily: "'JetBrains Mono', monospace",
                    }}
                  >
                    {p.p95 >= 1000 ? `${(p.p95 / 1000).toFixed(1)}s` : `${p.p95}ms`}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: COLORS.textDim }}>Requests</div>
                  <div style={{ fontSize: 16, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>
                    {(p.requests / 1000).toFixed(1)}k
                  </div>
                </div>
              </div>
            </div>
          </Card>
        ))}
      </div>

      {/* Selected Provider Detail */}
      {selectedProvider && (
        <Card>
          <SectionTitle subtitle="24-hour latency trend">
            {PROVIDERS.find((p) => p.id === selectedProvider)?.name} — Latency
          </SectionTitle>
          <div style={{ height: 240 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={LATENCY_DATA[selectedProvider]} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="p50fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={COLORS.chartLine1} stopOpacity={0.15} />
                    <stop offset="100%" stopColor={COLORS.chartLine1} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={COLORS.borderSubtle} />
                <XAxis dataKey="hour" tick={{ fontSize: 10, fill: COLORS.textDim }} tickLine={false} axisLine={{ stroke: COLORS.border }} interval={3} />
                <YAxis tick={{ fontSize: 10, fill: COLORS.textDim }} tickLine={false} axisLine={false} width={50} tickFormatter={(v) => `${v}ms`} />
                <Tooltip content={<CustomTooltip />} />
                <Area type="monotone" dataKey="p50" stroke={COLORS.chartLine1} fill="url(#p50fill)" strokeWidth={1.5} name="p50" dot={false} />
                <Line type="monotone" dataKey="p95" stroke={COLORS.chartLine2} strokeWidth={1.5} strokeDasharray="4 3" name="p95" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {/* Flow Health */}
      <Card>
        <SectionTitle subtitle="Compound reliability across dependency chains">User Flow Health</SectionTitle>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {[
            {
              name: "User Onboarding",
              deps: ["Twilio", "KYC Provider", "Plaid", "Credit Bureau", "Stripe"],
              chainUptime: 99.55,
              healthy: false,
              bottleneck: "KYC Provider (P95: 11.2s)",
            },
            {
              name: "Loan Disbursement",
              deps: ["Credit Bureau", "Stripe"],
              chainUptime: 99.97,
              healthy: true,
              bottleneck: null,
            },
            {
              name: "Payment Collection",
              deps: ["Stripe"],
              chainUptime: 99.99,
              healthy: true,
              bottleneck: null,
            },
          ].map((flow) => (
            <div
              key={flow.name}
              style={{
                padding: 14,
                borderRadius: 6,
                backgroundColor: COLORS.surfaceRaised,
                border: `1px solid ${COLORS.borderSubtle}`,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <StatusDot status={flow.healthy ? "healthy" : "degraded"} />
                  <span style={{ fontWeight: 600, fontSize: 14 }}>{flow.name}</span>
                </div>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 14,
                    fontWeight: 600,
                    color: flow.chainUptime >= 99.9 ? COLORS.healthy : COLORS.degraded,
                  }}
                >
                  {flow.chainUptime}% chain uptime
                </span>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                {flow.deps.map((dep, i) => (
                  <span key={dep} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span
                      style={{
                        fontSize: 11,
                        padding: "2px 8px",
                        borderRadius: 4,
                        backgroundColor: dep === "KYC Provider" ? COLORS.degradedMuted : COLORS.bg,
                        color: dep === "KYC Provider" ? COLORS.degraded : COLORS.textMuted,
                        border: `1px solid ${dep === "KYC Provider" ? `${COLORS.degraded}30` : COLORS.border}`,
                        fontFamily: "'JetBrains Mono', monospace",
                      }}
                    >
                      {dep}
                    </span>
                    {i < flow.deps.length - 1 && <span style={{ color: COLORS.textDim, fontSize: 10 }}>→</span>}
                  </span>
                ))}
              </div>
              {flow.bottleneck && (
                <div style={{ fontSize: 11, color: COLORS.degraded, marginTop: 8 }}>
                  ⚠ Bottleneck: {flow.bottleneck}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

// ============================================================================
// WEBHOOKS TAB
// ============================================================================

function WebhooksTab() {
  const webhookProviders = PROVIDERS.filter((p) => p.webhookDelivery !== null);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Delivery Rates */}
      <SectionTitle subtitle="Webhook delivery reliability per provider (last 24h)">Delivery Rates</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
        {webhookProviders.map((p) => (
          <Card key={p.id}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{p.name}</span>
              <StatusDot status={p.webhookDelivery >= 99 ? "healthy" : p.webhookDelivery >= 95 ? "degraded" : "unhealthy"} />
            </div>
            <div
              style={{
                fontSize: 28,
                fontWeight: 700,
                fontFamily: "'JetBrains Mono', monospace",
                color: p.webhookDelivery >= 99 ? COLORS.healthy : p.webhookDelivery >= 95 ? COLORS.degraded : COLORS.unhealthy,
                marginBottom: 4,
              }}
            >
              {p.webhookDelivery}%
            </div>
            <div style={{ fontSize: 11, color: COLORS.textDim }}>delivery rate</div>
          </Card>
        ))}
      </div>

      {/* Webhook Trend Charts */}
      {["kyc_provider", "plaid", "stripe"].map((pid) => {
        const provider = PROVIDERS.find((p) => p.id === pid);
        return (
          <Card key={pid}>
            <SectionTitle subtitle="Hourly delivery rate">{provider.name} — Webhook Delivery</SectionTitle>
            <div style={{ height: 180 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={WEBHOOK_DATA[pid]} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id={`whfill-${pid}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={COLORS.healthy} stopOpacity={0.15} />
                      <stop offset="100%" stopColor={COLORS.healthy} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={COLORS.borderSubtle} />
                  <XAxis dataKey="hour" tick={{ fontSize: 10, fill: COLORS.textDim }} tickLine={false} axisLine={{ stroke: COLORS.border }} interval={3} />
                  <YAxis domain={[70, 100]} tick={{ fontSize: 10, fill: COLORS.textDim }} tickLine={false} axisLine={false} width={40} tickFormatter={(v) => `${v}%`} />
                  <Tooltip content={<CustomTooltip />} />
                  {/* Threshold line at 95% */}
                  <Line type="monotone" dataKey={() => 95} stroke={COLORS.unhealthy} strokeWidth={1} strokeDasharray="6 4" dot={false} name="95% threshold" />
                  <Area type="monotone" dataKey="rate" stroke={COLORS.healthy} fill={`url(#whfill-${pid})`} strokeWidth={2} name="Delivery %" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Card>
        );
      })}

      {/* DLQ Status */}
      <Card>
        <SectionTitle subtitle="Events that exhausted all retries">Dead Letter Queue</SectionTitle>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20 }}>
          <MetricValue value="3" label="Unresolved" status="degraded" />
          <MetricValue value="41" label="Resolved (30d)" status="healthy" />
          <MetricValue value="6.2" unit="hrs" label="Oldest Unresolved" />
        </div>
      </Card>
    </div>
  );
}

// ============================================================================
// FUNNEL TAB
// ============================================================================

function FunnelTab() {
  const completionRate = ((672 / 1000) * 100).toFixed(1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Top Metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        <Card><MetricValue value="1,000" label="Sessions Started (24h)" /></Card>
        <Card><MetricValue value="672" label="Completed" status="healthy" /></Card>
        <Card><MetricValue value={`${completionRate}%`} label="Completion Rate" /></Card>
        <Card><MetricValue value="1" label="Active Bottleneck" status="degraded" /></Card>
      </div>

      {/* Funnel Visualization */}
      <Card>
        <SectionTitle subtitle="Step-by-step conversion with API correlation">Onboarding Funnel (Last 24h)</SectionTitle>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {FUNNEL_DATA.map((step, i) => {
            const widthPct = (step.completed / 1000) * 100;
            const isBottleneck = step.dropRate > 12;
            const barColor = isBottleneck ? COLORS.degraded : COLORS.accent;

            return (
              <div key={step.step} style={{ display: "flex", alignItems: "center", gap: 16, padding: "10px 0" }}>
                <div style={{ width: 200, fontSize: 13, color: isBottleneck ? COLORS.degraded : COLORS.text, fontWeight: isBottleneck ? 600 : 400 }}>
                  {i + 1}. {step.step}
                </div>
                <div style={{ flex: 1, position: "relative", height: 28 }}>
                  <div style={{ position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: COLORS.surfaceRaised, borderRadius: 4 }} />
                  <div
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      bottom: 0,
                      width: `${widthPct}%`,
                      backgroundColor: barColor,
                      borderRadius: 4,
                      opacity: 0.25,
                      transition: "width 0.5s ease",
                    }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      bottom: 0,
                      display: "flex",
                      alignItems: "center",
                      paddingLeft: 10,
                      fontSize: 12,
                      fontWeight: 600,
                      fontFamily: "'JetBrains Mono', monospace",
                      color: COLORS.text,
                    }}
                  >
                    {step.completed}/{step.reached}
                  </div>
                </div>
                <div style={{ width: 60, textAlign: "right", fontFamily: "'JetBrains Mono', monospace", fontSize: 13, color: isBottleneck ? COLORS.degraded : COLORS.textMuted }}>
                  -{step.dropRate}%
                </div>
                <div style={{ width: 90 }}>
                  {step.apiCorrelated > 50 ? (
                    <Badge variant="unhealthy">{step.apiCorrelated}% API</Badge>
                  ) : step.apiCorrelated > 25 ? (
                    <Badge variant="degraded">{step.apiCorrelated}% API</Badge>
                  ) : (
                    <Badge>{step.apiCorrelated}% API</Badge>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      {/* Bottleneck Analysis */}
      <Card
        style={{
          border: `1px solid ${COLORS.degraded}30`,
          backgroundColor: `${COLORS.degradedMuted}40`,
        }}
      >
        <SectionTitle>Bottleneck Detected: Identity Verification</SectionTitle>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
          <div>
            <div style={{ fontSize: 13, color: COLORS.text, lineHeight: 1.7 }}>
              <p style={{ margin: "0 0 12px 0" }}>
                Drop-off rate at Identity Verification is <strong style={{ color: COLORS.degraded }}>16.2%</strong> — 
                over 2x the average step drop-off rate of 7.5%.
              </p>
              <p style={{ margin: "0 0 12px 0" }}>
                <strong style={{ color: COLORS.degraded }}>83% of drop-offs correlate with API issues</strong>, 
                primarily latency. The KYC Provider's P95 latency of 11.2 seconds exceeds the 8-second 
                tolerance threshold.
              </p>
              <p style={{ margin: 0, color: COLORS.textMuted }}>
                Recommendation: Fix the integration before optimizing UX. Adjusting the timeout and 
                enabling the manual review fallback could recover an estimated 8-10% in completion rate.
              </p>
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div style={{ padding: 12, backgroundColor: COLORS.surface, borderRadius: 6 }}>
                <MetricValue value="154" label="Users Dropped" status="degraded" />
              </div>
              <div style={{ padding: 12, backgroundColor: COLORS.surface, borderRadius: 6 }}>
                <MetricValue value="128" label="API-Related" status="unhealthy" />
              </div>
              <div style={{ padding: 12, backgroundColor: COLORS.surface, borderRadius: 6 }}>
                <MetricValue value="11.2" unit="s" label="API P95 Latency" status="degraded" />
              </div>
              <div style={{ padding: 12, backgroundColor: COLORS.surface, borderRadius: 6 }}>
                <MetricValue value="~90" label="Recoverable Users" status="healthy" />
              </div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

// ============================================================================
// SCORECARD TAB
// ============================================================================

function ScorecardTab() {
  const sortedProviders = [...PROVIDERS].sort((a, b) => a.score - b.score);

  const gradeColor = (score) => {
    if (score >= 90) return COLORS.healthy;
    if (score >= 75) return COLORS.accent;
    if (score >= 60) return COLORS.degraded;
    return COLORS.unhealthy;
  };

  const gradeLabel = (score) => {
    if (score >= 90) return "Excellent";
    if (score >= 75) return "Good";
    if (score >= 60) return "Concerning";
    return "Unacceptable";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Score Cards */}
      <SectionTitle subtitle="Composite scores based on uptime, incidents, latency, webhook delivery, support, and DX">
        Provider Health Scores (90-Day)
      </SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
        {sortedProviders.map((p) => (
          <Card key={p.id}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{p.name}</div>
                <Badge variant={p.slaStatus}>{p.slaStatus.replace("_", " ")}</Badge>
              </div>
              <div style={{ textAlign: "right" }}>
                <div
                  style={{
                    fontSize: 36,
                    fontWeight: 700,
                    fontFamily: "'JetBrains Mono', monospace",
                    color: gradeColor(p.score),
                    lineHeight: 1,
                  }}
                >
                  {p.score}
                </div>
                <div style={{ fontSize: 11, color: gradeColor(p.score), fontWeight: 600 }}>{gradeLabel(p.score)}</div>
              </div>
            </div>

            {/* Score bar */}
            <div style={{ height: 4, backgroundColor: COLORS.bg, borderRadius: 2, marginBottom: 16 }}>
              <div
                style={{
                  height: "100%",
                  width: `${p.score}%`,
                  backgroundColor: gradeColor(p.score),
                  borderRadius: 2,
                  transition: "width 0.5s ease",
                }}
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: COLORS.textDim }}>Uptime</span>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", color: p.uptimePct >= p.slaGuarantee ? COLORS.healthy : COLORS.unhealthy }}>
                  {p.uptimePct}%
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: COLORS.textDim }}>SLA</span>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", color: COLORS.textMuted }}>{p.slaGuarantee}%</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: COLORS.textDim }}>Error Rate</span>
                <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{p.errorRate}%</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: COLORS.textDim }}>P95</span>
                <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                  {p.p95 >= 1000 ? `${(p.p95 / 1000).toFixed(1)}s` : `${p.p95}ms`}
                </span>
              </div>
              {p.webhookDelivery !== null && (
                <div style={{ display: "flex", justifyContent: "space-between", gridColumn: "span 2" }}>
                  <span style={{ color: COLORS.textDim }}>Webhook Delivery</span>
                  <span style={{ fontFamily: "'JetBrains Mono', monospace", color: p.webhookDelivery >= 99 ? COLORS.healthy : COLORS.degraded }}>
                    {p.webhookDelivery}%
                  </span>
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>

      {/* Migration Triggers */}
      <Card
        style={{
          border: `1px solid ${COLORS.degraded}30`,
          backgroundColor: `${COLORS.degradedMuted}40`,
        }}
      >
        <SectionTitle>Migration Evaluation: KYC Provider</SectionTitle>
        <div style={{ fontSize: 13, lineHeight: 1.7, color: COLORS.text }}>
          <p style={{ margin: "0 0 12px 0" }}>
            The KYC Provider has triggered migration evaluation criteria based on sustained performance issues:
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
            {[
              { label: "SLA breaches (6 months)", value: "1 breach (99.71% vs 99.95%)", triggered: true },
              { label: "Incidents (90 days)", value: "4 incidents", triggered: true },
              { label: "Composite score", value: "62/100 (Concerning)", triggered: false },
              { label: "Latency trend", value: "Worsening (+18% over 90d)", triggered: true },
              { label: "Unacknowledged incidents", value: "2 not on status page", triggered: true },
              { label: "Webhook delivery", value: "97.1% (below 99% SLA)", triggered: true },
            ].map((trigger) => (
              <div
                key={trigger.label}
                style={{
                  padding: "8px 12px",
                  borderRadius: 4,
                  backgroundColor: COLORS.surface,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontSize: 12,
                }}
              >
                <div>
                  <span style={{ color: trigger.triggered ? COLORS.unhealthy : COLORS.healthy, marginRight: 8 }}>
                    {trigger.triggered ? "✗" : "✓"}
                  </span>
                  <span style={{ color: COLORS.textMuted }}>{trigger.label}</span>
                </div>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", color: trigger.triggered ? COLORS.degraded : COLORS.textMuted, fontSize: 11 }}>
                  {trigger.value}
                </span>
              </div>
            ))}
          </div>
          <p style={{ margin: 0, color: COLORS.degraded, fontWeight: 500 }}>
            Recommendation: Escalate to provider's account team. Begin evaluating alternatives for Q2 QBR.
          </p>
        </div>
      </Card>
    </div>
  );
}
