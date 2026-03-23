"use client";

import type { ConversationUsageSummary } from "@/types";

interface UsageByConversationTableProps {
  data: ConversationUsageSummary[];
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

const TYPE_LABELS: Record<string, string> = {
  research: "Research",
  graph_builder: "Graph Builder",
  ingestion: "Ingestion",
};

function formatTypes(types: string[]): string {
  return types.map((t) => TYPE_LABELS[t] || t).join(", ");
}

export function UsageByConversationTable({ data }: UsageByConversationTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 font-medium">Conversation</th>
            <th className="pb-2 font-medium">Type</th>
            <th className="pb-2 font-medium text-right">Runs</th>
            <th className="pb-2 font-medium text-right">Input Tokens</th>
            <th className="pb-2 font-medium text-right">Output Tokens</th>
            <th className="pb-2 font-medium text-right">Total Tokens</th>
            <th className="pb-2 font-medium text-right">Cost (USD)</th>
            <th className="pb-2 font-medium text-right">Last Run</th>
          </tr>
        </thead>
        <tbody>
          {data.length === 0 && (
            <tr>
              <td colSpan={8} className="py-4 text-center text-muted-foreground">
                No conversation usage data yet
              </td>
            </tr>
          )}
          {data.map((row) => (
            <tr key={row.conversation_id} className="border-b last:border-0">
              <td className="py-2">
                <div className="font-medium text-xs truncate max-w-[300px]">
                  {row.title || row.conversation_id.slice(0, 8)}
                </div>
                <div className="text-xs text-muted-foreground font-mono">
                  {row.conversation_id.slice(0, 8)}...
                </div>
              </td>
              <td className="py-2 text-xs">
                {formatTypes(row.report_types ?? ["research"])}
              </td>
              <td className="py-2 text-right">{row.report_count}</td>
              <td className="py-2 text-right">
                {formatTokens(row.total_prompt_tokens)}
              </td>
              <td className="py-2 text-right">
                {formatTokens(row.total_completion_tokens)}
              </td>
              <td className="py-2 text-right">
                {formatTokens(row.total_prompt_tokens + row.total_completion_tokens)}
              </td>
              <td className="py-2 text-right font-medium">
                ${row.total_cost_usd.toFixed(4)}
              </td>
              <td className="py-2 text-right text-muted-foreground text-xs">
                {row.last_at
                  ? new Date(row.last_at).toLocaleDateString()
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
