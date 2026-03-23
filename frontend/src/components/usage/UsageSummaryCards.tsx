"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { UsageSummaryResponse } from "@/types";

interface UsageSummaryCardsProps {
  data: UsageSummaryResponse;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatCost(n: number): string {
  return `$${n.toFixed(4)}`;
}

export function UsageSummaryCards({ data }: UsageSummaryCardsProps) {
  const totalTokens = data.total_prompt_tokens + data.total_completion_tokens;

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Total Cost
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {formatCost(data.total_cost_usd)}
          </div>
          <p className="text-xs text-muted-foreground">
            {data.report_count} research run{data.report_count !== 1 ? "s" : ""}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Total Tokens
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{formatTokens(totalTokens)}</div>
          <p className="text-xs text-muted-foreground">
            {data.by_model.length} model{data.by_model.length !== 1 ? "s" : ""}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Input Tokens
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {formatTokens(data.total_prompt_tokens)}
          </div>
          <p className="text-xs text-muted-foreground">
            {totalTokens > 0
              ? `${((data.total_prompt_tokens / totalTokens) * 100).toFixed(0)}% of total`
              : "—"}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Output Tokens
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {formatTokens(data.total_completion_tokens)}
          </div>
          <p className="text-xs text-muted-foreground">
            {totalTokens > 0
              ? `${((data.total_completion_tokens / totalTokens) * 100).toFixed(0)}% of total`
              : "—"}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Avg Cost / Run
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {data.report_count > 0
              ? formatCost(data.total_cost_usd / data.report_count)
              : "$0.00"}
          </div>
          <p className="text-xs text-muted-foreground">
            {data.report_count > 0
              ? `${formatTokens(Math.round(totalTokens / data.report_count))} tokens/run`
              : "—"}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
