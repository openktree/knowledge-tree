"use client";

import { useState, useCallback, useMemo, type ComponentPropsWithoutRef } from "react";
import Markdown from "react-markdown";
import { Copy, Check } from "lucide-react";
import { cn, linkifyFactTokens } from "@/lib/utils";
import type { ActivityEntry } from "@/types";
import { ActivityLog } from "@/components/answer/ActivityLog";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

// ---------------------------------------------------------------------------
// Custom link renderer — opens internal /nodes/ and /facts/ links in new tabs
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

export interface AnswerViewProps {
  /** The synthesized answer text. */
  answer: string;
  /** Whether the answer is still being streamed in. */
  isStreaming: boolean;
  /** Current query phase for richer status display. */
  phase?: string;
  /** Number of nodes visited so far. */
  nodeCount?: number;
  /** Real-time activity entries from the agent. */
  activities: ActivityEntry[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AnswerView({
  answer,
  isStreaming,
  phase,
  nodeCount,
  activities,
}: AnswerViewProps) {
  const hasContent = answer.length > 0;
  const isWorking = isStreaming && !hasContent;
  const [copied, setCopied] = useState(false);
  const processedAnswer = useMemo(() => linkifyFactTokens(answer), [answer]);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(processedAnswer);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [processedAnswer]);

  return (
    <Card className="w-full flex-1 flex flex-col min-h-0 overflow-hidden">
      <CardHeader className="shrink-0 flex flex-row items-center justify-between">
        <CardTitle className="text-lg">Answer</CardTitle>
        {hasContent && !isStreaming && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={handleCopy}
                  aria-label="Copy answer"
                >
                  {copied ? (
                    <Check className="text-green-500" />
                  ) : (
                    <Copy className="text-muted-foreground" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{copied ? "Copied!" : "Copy answer"}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </CardHeader>
      <CardContent className="flex-1 overflow-y-auto">
        {hasContent ? (
          <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed">
            <Markdown components={markdownComponents}>{processedAnswer}</Markdown>
            {isStreaming && (
              <span
                className={cn(
                  "ml-0.5 inline-block h-4 w-[2px] align-text-bottom bg-foreground",
                  "animate-pulse",
                )}
                aria-label="Streaming in progress"
              />
            )}
          </div>
        ) : isStreaming ? (
          <div className="flex flex-col items-center gap-4 py-8">
            <ActivityLog activities={activities} isActive={isWorking} />
            {phase && phase !== "pending" && (
              <p className="text-xs text-muted-foreground">
                Phase: <span className="capitalize">{phase}</span>
                {nodeCount !== undefined && nodeCount > 0 && (
                  <> &middot; {nodeCount} node{nodeCount !== 1 ? "s" : ""} found</>
                )}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No answer available yet.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
