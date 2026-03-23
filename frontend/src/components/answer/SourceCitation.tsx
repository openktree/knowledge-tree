"use client";

import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SourceCitationProps {
  /** Display title for the source. */
  title: string;
  /** URI of the source. Opens in a new tab when clicked. */
  uri: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SourceCitation({ title, uri }: SourceCitationProps) {
  return (
    <a
      href={uri}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-1.5 text-sm text-primary",
        "underline-offset-4 hover:underline",
        "transition-colors hover:text-primary/80",
        "rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      )}
    >
      <span className="truncate">{title}</span>
      <ExternalLink className="size-3.5 shrink-0" aria-hidden="true" />
    </a>
  );
}
