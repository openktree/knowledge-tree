"use client";

import { useMemo, useState } from "react";
import { useUsageSummary, useUsageByModel, useUsageByConversation } from "@/hooks/useUsage";
import { UsageSummaryCards } from "@/components/usage/UsageSummaryCards";
import { UsageByModelTable } from "@/components/usage/UsageByModelTable";
import { UsageByTaskTable } from "@/components/usage/UsageByTaskTable";
import { UsageByConversationTable } from "@/components/usage/UsageByConversationTable";
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

export default function UsagePage() {
  const [range, setRange] = useState<TimeRange>("30d");
  const since = useMemo(() => rangeToSince(range), [range]);

  const summary = useUsageSummary(since);
  const byModel = useUsageByModel(since);
  const byConversation = useUsageByConversation(since);

  if (summary.isLoading) {
    return (
      <div className="p-6">
        <p className="text-muted-foreground">Loading usage data...</p>
      </div>
    );
  }

  if (summary.error) {
    return (
      <div className="p-6">
        <p className="text-destructive">{summary.error}</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Token Usage</h1>
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

      {summary.data && <UsageSummaryCards data={summary.data} />}

      <Card>
        <CardHeader>
          <CardTitle>Usage by Model</CardTitle>
        </CardHeader>
        <CardContent>
          {byModel.isLoading ? (
            <p className="text-muted-foreground">Loading...</p>
          ) : byModel.error ? (
            <p className="text-destructive">{byModel.error}</p>
          ) : (
            <UsageByModelTable data={byModel.data} />
          )}
        </CardContent>
      </Card>

      {summary.data && summary.data.by_task.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Usage by Task</CardTitle>
          </CardHeader>
          <CardContent>
            <UsageByTaskTable data={summary.data.by_task} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Usage by Conversation</CardTitle>
        </CardHeader>
        <CardContent>
          {byConversation.isLoading ? (
            <p className="text-muted-foreground">Loading...</p>
          ) : byConversation.error ? (
            <p className="text-destructive">{byConversation.error}</p>
          ) : (
            <UsageByConversationTable data={byConversation.data} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
