"use client";

import { FileText, Link2, Download, ExternalLink, Loader2, CheckCircle2, XCircle, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { IngestSourceResponse } from "@/types";

interface SourcesListProps {
  sources: IngestSourceResponse[];
  conversationId: string;
  isLoading: boolean;
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "ready":
      return <CheckCircle2 className="size-4 text-green-500" />;
    case "failed":
      return <XCircle className="size-4 text-red-500" />;
    case "processing":
      return <Loader2 className="size-4 text-blue-500 animate-spin" />;
    default:
      return <Clock className="size-4 text-muted-foreground" />;
  }
}

function formatFileSize(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function SourcesList({
  sources,
  conversationId,
  isLoading,
}: SourcesListProps) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading sources...
      </div>
    );
  }

  if (sources.length === 0) return null;

  return (
    <div className="border-b p-3 space-y-2">
      <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Sources ({sources.length})
      </div>
      <div className="space-y-1.5">
        {sources.map((source) => (
          <div
            key={source.id}
            className="flex items-center gap-2 text-sm rounded-md px-2 py-1.5 bg-muted/50"
          >
            <StatusIcon status={source.status} />
            {source.source_type === "file" ? (
              <FileText className="size-4 text-muted-foreground shrink-0" />
            ) : (
              <Link2 className="size-4 text-muted-foreground shrink-0" />
            )}
            <span className="truncate flex-1" title={source.original_name}>
              {source.original_name}
            </span>
            {source.file_size !== null && (
              <span className="text-xs text-muted-foreground shrink-0">
                {formatFileSize(source.file_size)}
              </span>
            )}
            {source.section_count !== null && (
              <Badge variant="secondary" className="text-xs shrink-0">
                {source.section_count} sections
              </Badge>
            )}
            {source.error && (
              <span className="text-xs text-red-500 truncate max-w-[150px]" title={source.error}>
                {source.error}
              </span>
            )}
            {source.source_type === "file" ? (
              <Button
                variant="ghost"
                size="icon"
                className="size-6 shrink-0"
                asChild
              >
                <a
                  href={api.research.getSourceDownloadUrl(conversationId, source.id)}
                  download
                >
                  <Download className="size-3" />
                </a>
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                className="size-6 shrink-0"
                asChild
              >
                <a
                  href={source.original_name}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <ExternalLink className="size-3" />
                </a>
              </Button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
