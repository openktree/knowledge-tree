"use client";

import { useMemo } from "react";
import type { FactResponse } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { ExternalLink, FileText, Globe } from "lucide-react";
import { cn } from "@/lib/utils";

interface FactsTabProps {
  facts: FactResponse[];
}

const factTypeColors: Record<string, string> = {
  claim:
    "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200",
  account:
    "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
  measurement:
    "bg-cyan-100 text-cyan-800 dark:bg-cyan-900 dark:text-cyan-200",
  formula:
    "bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-200",
  quote: "bg-pink-100 text-pink-800 dark:bg-pink-900 dark:text-pink-200",
  procedure:
    "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  reference:
    "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  code: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
  perspective:
    "bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-200",
};

function formatDate(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function extractDomain(uri: string): string {
  try {
    return new URL(uri).hostname.replace(/^www\./, "");
  } catch {
    return uri;
  }
}

export function FactsTab({ facts }: FactsTabProps) {
  const domains = useMemo(() => {
    const seen = new Set<string>();
    for (const fact of facts) {
      for (const source of fact.sources) {
        seen.add(extractDomain(source.uri));
      }
    }
    return Array.from(seen).sort();
  }, [facts]);

  const groupedFacts = useMemo(() => {
    const groups = new Map<string, FactResponse[]>();
    const sorted = [...facts].sort((a, b) =>
      a.fact_type.localeCompare(b.fact_type)
    );
    for (const fact of sorted) {
      const existing = groups.get(fact.fact_type) ?? [];
      existing.push(fact);
      groups.set(fact.fact_type, existing);
    }
    return groups;
  }, [facts]);

  if (facts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <FileText className="h-10 w-10 mb-3 opacity-50" />
        <p>No facts linked to this node.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {domains.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Globe className="h-3.5 w-3.5" />
            <span>Sources</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {domains.map((domain) => (
              <Badge key={domain} variant="outline" className="text-xs font-normal">
                {domain}
              </Badge>
            ))}
          </div>
          <Separator />
        </div>
      )}
      {Array.from(groupedFacts.entries()).map(([factType, typeFacts]) => (
        <div key={factType} className="space-y-2">
          <div className="flex items-center gap-2">
            <Badge
              className={cn(
                "text-xs capitalize",
                factTypeColors[factType] ?? ""
              )}
              variant="secondary"
            >
              {factType}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {typeFacts.length} fact{typeFacts.length !== 1 ? "s" : ""}
            </span>
          </div>

          <div className="space-y-2">
            {typeFacts.map((fact) => (
              <Card key={fact.id}>
                <CardContent className="py-3 space-y-1">
                  <p className="text-sm leading-relaxed break-words">{fact.content}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatDate(fact.created_at)}
                  </p>
                  {fact.sources.length > 0 && (
                    <div className="pt-1 space-y-0.5">
                      {fact.sources.map((source) => (
                        <div
                          key={source.source_id}
                          className="flex items-center gap-1 text-xs text-muted-foreground min-w-0"
                        >
                          <ExternalLink className="h-3 w-3 shrink-0" />
                          <a
                            href={source.uri}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="min-w-0 truncate hover:underline text-blue-600 dark:text-blue-400"
                          >
                            {source.title ?? source.uri}
                          </a>
                          <span className="shrink-0">
                            {formatDate(source.retrieved_at)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>

          <Separator />
        </div>
      ))}
    </div>
  );
}
