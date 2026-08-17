import { useId } from "react";

export type Series = { name: string; values: number[]; color: string; dashed?: boolean; tooltips?: string[] };

function bounds(series: Series[]) {
  const values = series.flatMap((item) => item.values).filter(Number.isFinite);
  if (!values.length) return { min: 0, max: 1, range: 1 };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = (max - min || Math.abs(max) || 1) * .18;
  return { min: min - padding, max: max + padding, range: max - min + padding * 2 };
}

export function LineChart({ labels, series, yLabel, metricDirection = "unknown" }: { labels: string[]; series: Series[]; yLabel?: string; metricDirection?: "higher" | "lower" | "unknown" }) {
  const clip = useId().replace(/:/g, "");
  const { min, max, range } = bounds(series);
  const width = 560; const height = 230; const left = 54; const right = 20; const top = 24; const bottom = 40;
  const x = (index: number) => labels.length === 1 ? (left + width - right) / 2 : left + index / (labels.length - 1) * (width - left - right);
  const y = (value: number) => top + (max - value) / range * (height - top - bottom);
  const ticks = Array.from({ length: 5 }, (_, index) => max - index / 4 * range);
  const directionLabel = metricDirection === "higher" ? "数值越高越好" : metricDirection === "lower" ? "数值越低越好" : "优化方向未在实验计划中声明";
  return <div className="chart-wrap"><div className="chart-semantics" title="指标方向来自实验计划；变化量按该方向判断改善或下降。">{directionLabel}</div><svg className="metric-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${yLabel ?? "指标"}性能演化，${directionLabel}`}>
    <defs><clipPath id={clip}><rect x={left} y={top} width={width - left - right} height={height - top - bottom}/></clipPath></defs>
    {ticks.map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="chart-grid"/><text x={left - 8} y={y(tick) + 4} textAnchor="end" className="chart-axis">{Math.abs(tick) >= 100 ? tick.toFixed(0) : Math.abs(tick) >= 1 ? tick.toFixed(1) : tick.toFixed(3)}</text></g>)}
    {labels.map((label, index) => <text key={label + index} x={x(index)} y={height - 14} textAnchor="middle" className="chart-axis">{label}</text>)}
    {yLabel ? <text transform={`translate(14 ${height / 2}) rotate(-90)`} textAnchor="middle" className="chart-axis chart-label">{yLabel}</text> : null}
    <g clipPath={`url(#${clip})`}>{series.map((item) => { const path = item.values.map((value, index) => `${index ? "L" : "M"}${x(index)} ${y(value)}`).join(" "); return <g key={item.name}><path d={path} fill="none" stroke={item.color} strokeWidth="2" strokeDasharray={item.dashed ? "6 5" : undefined}/>{item.values.map((value, index) => <circle key={index} cx={x(index)} cy={y(value)} r="3.5" fill={item.color}><title>{item.tooltips?.[index] ?? `${labels[index]}：${item.name} = ${value}；${directionLabel}`}</title></circle>)}</g>; })}</g>
  </svg><div className="chart-legend">{series.map((item) => <span key={item.name}><i style={{ background: item.color }}/>{item.name}</span>)}</div></div>;
}

export function ContributionChart({ rows }: { rows: Array<{ label: string; value?: number; failed?: boolean }> }) {
  const max = Math.max(...rows.map((row) => Math.abs(row.value ?? 0)), 1);
  return <div className="contribution-chart">{rows.map((row) => <div className={row.failed ? "failed" : ""} key={row.label}><span title={row.label}>{row.label}</span><div><i style={{ width: `${Math.max(2, Math.abs(row.value ?? 0) / max * 80)}%` }}/></div><strong>{row.failed ? "工程失败" : row.value === undefined ? "—" : `${row.value >= 0 ? "+" : ""}${row.value.toFixed(3)}`}</strong></div>)}</div>;
}
