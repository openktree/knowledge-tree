"use client";

import type { TokenUsageByModel } from "@/types";

interface UsageByModelTableProps {
  data: TokenUsageByModel[];
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function UsageByModelTable({ data }: UsageByModelTableProps) {
  const sorted = [...data].sort((a, b) => b.cost_usd - a.cost_usd);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 font-medium">Model</th>
            <th className="pb-2 font-medium text-right">Prompt Tokens</th>
            <th className="pb-2 font-medium text-right">Completion Tokens</th>
            <th className="pb-2 font-medium text-right">Total Tokens</th>
            <th className="pb-2 font-medium text-right">Cost (USD)</th>
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && (
            <tr>
              <td colSpan={5} className="py-4 text-center text-muted-foreground">
                No usage data yet
              </td>
            </tr>
          )}
          {sorted.map((row) => (
            <tr key={row.model_id} className="border-b last:border-0">
              <td className="py-2 font-mono text-xs">{row.model_id}</td>
              <td className="py-2 text-right">
                {formatTokens(row.prompt_tokens)}
              </td>
              <td className="py-2 text-right">
                {formatTokens(row.completion_tokens)}
              </td>
              <td className="py-2 text-right">
                {formatTokens(row.prompt_tokens + row.completion_tokens)}
              </td>
              <td className="py-2 text-right">
                ${row.cost_usd.toFixed(4)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
