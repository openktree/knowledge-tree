"use client";

import { useState, useCallback, useMemo, type ComponentPropsWithoutRef } from "react";
import Markdown from "react-markdown";
import { Copy, Check, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn, linkifyFactTokens } from "@/lib/utils";
import type { ConversationMessageResponse } from "@/types";

// ---------------------------------------------------------------------------
// Custom link renderer (same as AnswerView)
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

interface ChatMessageProps {
  message: ConversationMessageResponse;
  onResynthesize?: (messageId: string) => void;
  isTurnActive?: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ChatMessage({ message, onResynthesize, isTurnActive }: ChatMessageProps) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const processedContent = useMemo(
    () => (isUser ? message.content : linkifyFactTokens(message.content)),
    [isUser, message.content],
  );

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(processedContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [processedContent]);

  return (
    <div
      className={cn(
        "group flex w-full",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      <div
        className={cn(
          "relative max-w-[85%] rounded-lg px-4 py-3",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted",
        )}
      >
        {isUser ? (
          <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        ) : (
          <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed">
            <Markdown components={markdownComponents}>
              {processedContent || "..."}
            </Markdown>
          </div>
        )}

        {/* Action buttons for assistant messages */}
        {!isUser && message.content && (
          <div className="absolute top-2 right-2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            {onResynthesize && (message.status === "completed" || message.status === "failed") && (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      disabled={isTurnActive}
                      onClick={() => onResynthesize(message.id)}
                      aria-label="Re-synthesize"
                    >
                      <RefreshCw className="text-muted-foreground" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Re-synthesize</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={handleCopy}
                    aria-label="Copy message"
                  >
                    {copied ? (
                      <Check className="text-green-500" />
                    ) : (
                      <Copy className="text-muted-foreground" />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{copied ? "Copied!" : "Copy"}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        )}

        {/* Budget badges for assistant messages */}
        {!isUser &&
          message.status === "completed" &&
          message.nav_used != null && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Badge variant="secondary" className="text-xs">
                Nav: {message.nav_used}
              </Badge>
              {message.explore_used != null && (
                <Badge variant="secondary" className="text-xs">
                  Explore: {message.explore_used}
                </Badge>
              )}
              {message.visited_nodes && (
                <Badge variant="outline" className="text-xs">
                  {message.visited_nodes.length} node
                  {message.visited_nodes.length !== 1 ? "s" : ""}
                </Badge>
              )}
            </div>
          )}

        {/* Status indicator for pending/running/failed */}
        {!isUser && message.status === "failed" && message.error && (
          <p className="mt-2 text-xs text-destructive">{message.error}</p>
        )}
      </div>
    </div>
  );
}
