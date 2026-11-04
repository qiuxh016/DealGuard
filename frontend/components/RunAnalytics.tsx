"use client";

import { useMemo } from "react";
import { localizedDecision, useLanguage } from "./LanguageProvider";

type RunRow = {
  decision: string | null;
  created_at: string;
  duration_ms: number | null;
};

const decisions = [
  { key: "ALLOW", color: "#15927f" },
  { key: "REVIEW", color: "#d18a3c" },
  { key: "DENY", color: "#cc6157" },
  { key: "NEEDS_INPUT", color: "#6378d8" },
];

export function RunAnalytics({ rows }: { rows: RunRow[] }) {
  const { locale, t } = useLanguage();
  const analytics = useMemo(() => {
    const end = new Date();
    end.setHours(0, 0, 0, 0);
    const days = Array.from({ length: 7 }, (_, index) => {
      const date = new Date(end);
      date.setDate(end.getDate() - (6 - index));
      return date;
    });
    const daily = days.map((date) => {
      const start = date.getTime();
      const next = start + 24 * 60 * 60 * 1000;
      const count = rows.reduce((total, row) => {
        const timestamp = new Date(row.created_at).getTime();
        return total + (timestamp >= start && timestamp < next ? 1 : 0);
      }, 0);
      return { date, count };
    });
    const counts = decisions.map(({ key, color }) => ({
      key,
      color,
      count: rows.reduce((total, row) => total + (row.decision === key ? 1 : 0), 0),
    }));
    const decisionTotal = counts.reduce((sum, item) => sum + item.count, 0);
    const durations = rows.map((row) => Number(row.duration_ms)).filter((value) => Number.isFinite(value) && value >= 0).sort((a, b) => a - b);
    return { daily, counts, max: Math.max(1, ...daily.map((item) => item.count)), decisionTotal, allowRate: decisionTotal ? (counts.find((item) => item.key === "ALLOW")?.count ?? 0) / decisionTotal * 100 : 0, medianDuration: durations.length ? durations[Math.floor((durations.length - 1) / 2)] : null };
  }, [rows]);

  const points = analytics.daily.map((item, index) => {
    const x = 32 + index * 106;
    const y = 150 - item.count / analytics.max * 104;
    return `${x},${y}`;
  }).join(" ");

  return <section className="dg-run-analytics" aria-label={t("运行数据分析", "Run analytics")}>
    <div className="dg-run-analytics-heading">
      <div><span className="dg-run-eyebrow">DEALGUARD · INSIGHTS</span><h2>{t("运行数据分析", "Run analytics")}</h2><p>{t("基于当前加载记录统计", "Based on the currently loaded records")} · {rows.length}</p></div>
      <span>{t("最近 7 天", "Last 7 days")}</span>
    </div>
    <div className="dg-run-kpis"><article><span>{t("已加载运行", "Loaded runs")}</span><strong>{rows.length.toLocaleString()}</strong><small>{t("当前筛选结果", "Current result set")}</small></article><article><span>{t("允许率", "Allow rate")}</span><strong>{analytics.decisionTotal ? `${analytics.allowRate.toFixed(0)}%` : "—"}</strong><small>{t("基于已判定记录", "Of decided runs")}</small></article><article><span>{t("中位耗时", "Median latency")}</span><strong>{analytics.medianDuration === null ? "—" : `${Math.round(analytics.medianDuration).toLocaleString()} ms`}</strong><small>{t("有耗时数据的运行", "Runs with duration data")}</small></article></div>
    <div className="dg-run-analytics-grid">
      <article className="dg-run-chart-card">
        <div className="dg-run-chart-heading"><div><h3>{t("每日询盘运行量", "Daily inquiry runs")}</h3><p>{t("最近七天的运行趋势", "Run trend over the last seven days")}</p></div><strong>{analytics.daily.reduce((total, item) => total + item.count, 0)} <small>{t("次", "runs")}</small></strong></div>
        <svg className="dg-run-line-chart" viewBox="0 0 700 204" role="img" aria-label={t("最近七天每日运行记录数量折线图", "Line chart of run counts over the last seven days")}>
          <defs><linearGradient id="runChartArea" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#4669e8" /><stop offset="100%" stopColor="#4669e8" stopOpacity="0" /></linearGradient></defs>
          <line x1="24" y1="150" x2="676" y2="150" />
          <line x1="24" y1="98" x2="676" y2="98" />
          <line x1="24" y1="46" x2="676" y2="46" />
          <polygon points={`32,150 ${points} 668,150`} className="dg-run-chart-area" />
          <polyline points={points} />
          {analytics.daily.map((item, index) => {
            const x = 32 + index * 106;
            const y = 150 - item.count / analytics.max * 104;
            return <g key={item.date.toISOString()}><circle cx={x} cy={y} r="4"><title>{`${item.date.toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", { month: "short", day: "numeric" })}: ${item.count}`}</title></circle><text x={x} y="188" textAnchor="middle">{item.date.toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", { weekday: "short" })}</text><text className="dg-run-chart-value" x={x} y={Math.max(y - 12, 30)} textAnchor="middle">{item.count}</text></g>;
          })}
        </svg>
        {analytics.daily.every((item) => item.count === 0) && <p className="dg-run-chart-empty">{t("最近 7 天没有运行记录", "No runs in the last seven days")}</p>}
      </article>
      <article className="dg-run-chart-card">
        <div className="dg-run-chart-title"><h3>{t("决策构成", "Decision mix")}</h3><strong>{analytics.decisionTotal}</strong></div>
        <div className="dg-run-decision-bars">{analytics.counts.map((item) => {
          const percent = analytics.decisionTotal ? item.count / analytics.decisionTotal * 100 : 0;
          return <div className="dg-run-decision-row" key={item.key}><div><span>{localizedDecision(item.key, locale)}</span><strong>{item.count}</strong></div><div className="dg-run-decision-track"><i style={{ width: `${percent}%`, background: item.color }} /></div></div>;
        })}</div>
      </article>
    </div>
  </section>;
}
