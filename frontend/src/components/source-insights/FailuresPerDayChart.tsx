import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import type { DailyFailureCount } from "@/types";

interface FailuresPerDayChartProps {
  data: DailyFailureCount[];
}

export function FailuresPerDayChart({ data }: FailuresPerDayChartProps) {
  if (data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8 text-center">
        No fetch failures in this period.
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis
          dataKey="day"
          tick={{ fontSize: 12 }}
          tickFormatter={(v: string) => {
            const d = new Date(v + "T00:00:00Z");
            return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
          }}
        />
        <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
        <Tooltip
          labelFormatter={(v) => {
            const d = new Date(String(v) + "T00:00:00Z");
            return d.toLocaleDateString(undefined, {
              year: "numeric",
              month: "long",
              day: "numeric",
              timeZone: "UTC",
            });
          }}
          formatter={(value) => [String(value), "Failures"]}
        />
        <Bar dataKey="failure_count" fill="hsl(var(--destructive))" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
