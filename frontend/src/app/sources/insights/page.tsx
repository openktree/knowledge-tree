"use client";

import { useMemo, useState } from "react";
import { useSourceInsights } from "@/hooks/useSourceInsights";
import { InsightsSummaryCards } from "@/components/source-insights/InsightsSummaryCards";
import { FailuresPerDayChart } from "@/components/source-insights/FailuresPerDayChart";
import { TopFailedDomainsTable } from "@/components/source-insights/TopFailedDomainsTable";
import { CommonErrorsTable } from "@/components/source-insights/CommonErrorsTable";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type TimeRange = "7d" | "30d" | "90d" | "all";

const RANGE_LABELS: Record<TimeRange, string> = {
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
  all: "All time",
};

function rangeToSince(range: TimeRange): string | undefined {
  if (range === "all") return undefined;
  const days = parseInt(range);
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString();
}

export default function SourceInsightsPage() {
  const [range, setRange] = useState<TimeRange>("30d");
  const since = useMemo(() => rangeToSince(range), [range]);

  const { data, isLoading, error } = useSourceInsights(since);

  if (isLoading) {
    return (
      <div className="p-6">
        <p className="text-muted-foreground">Loading source insights...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <p className="text-destructive">{error}</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Source Insights</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Fetch health and failure analysis
          </p>
        </div>
        <div className="flex gap-1 rounded-lg border p-1">
          {(Object.keys(RANGE_LABELS) as TimeRange[]).map((key) => (
            <button
              key={key}
              onClick={() => setRange(key)}
              className={`px-3 py-1 text-sm rounded-md transition-colors ${
                range === key
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {RANGE_LABELS[key]}
            </button>
          ))}
        </div>
      </div>

      {data && (
        <>
          <InsightsSummaryCards data={data} />

          <Card>
            <CardHeader>
              <CardTitle>Failures Per Day</CardTitle>
            </CardHeader>
            <CardContent>
              <FailuresPerDayChart data={data.failures_per_day} />
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Top Failed Domains</CardTitle>
              </CardHeader>
              <CardContent>
                <TopFailedDomainsTable data={data.top_failed_domains} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Common Error Messages</CardTitle>
              </CardHeader>
              <CardContent>
                <CommonErrorsTable data={data.common_errors} />
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
