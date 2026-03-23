"use client";

import type { ComponentPropsWithoutRef } from "react";
import Markdown from "react-markdown";
import { cn } from "@/lib/utils";
import { Loader2 } from "lucide-react";

// ---------------------------------------------------------------------------
// Custom link renderer
// ---------------------------------------------------------------------------

function AnswerLink(props: ComponentPropsWithoutRef<"a">) {
  const { href, children, ...rest } = props;
  const isInternal =
    href?.startsWith("/nodes/") || href?.startsWith("/facts/");

  if (isInternal) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-blue-600 dark:text-blue-400 underline decoration-blue-600/30 dark:decoration-blue-400/30 underline-offset-2 hover:decoration-blue-600 dark:hover:decoration-blue-400 transition-colors"
        {...rest}
      >
        {children}
      </a>
    );
  }

  return (
    <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
      {children}
    </a>
  );
}

const markdownComponents = {
  a: AnswerLink,
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ActiveTurnDisplayProps {
  answer: string;
  phase: string;
  nodeCount: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ActiveTurnDisplay({
  answer,
  phase,
  nodeCount,
}: ActiveTurnDisplayProps) {
  const hasContent = answer.length > 0;

  return (
    <div className="flex w-full justify-start">
      <div className="max-w-[85%] rounded-lg px-4 py-3 bg-muted">
        {hasContent ? (
          <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed">
            <Markdown components={markdownComponents}>{answer}</Markdown>
            <span
              className={cn(
                "ml-0.5 inline-block h-4 w-[2px] align-text-bottom bg-foreground",
                "animate-pulse",
              )}
              aria-label="Streaming in progress"
            />
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3 py-2">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Working...</span>
            </div>
            {phase && phase !== "pending" && (
              <p className="text-xs text-muted-foreground">
                Phase: <span className="capitalize">{phase}</span>
                {nodeCount > 0 && (
                  <>
                    {" "}
                    &middot; {nodeCount} node{nodeCount !== 1 ? "s" : ""} found
                  </>
                )}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
