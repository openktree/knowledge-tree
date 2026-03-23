"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface JustificationTextProps {
  text: string;
  className?: string;
}

/**
 * Renders justification text with {fact:<uuid>} tokens converted to
 * clickable superscript citation links, matching the AnswerView pattern.
 */
export function JustificationText({ text, className }: JustificationTextProps) {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let citationIndex = 0;

  const re = /\{fact:([0-9a-f-]{36})\}/gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    citationIndex++;
    const uuid = match[1];
    parts.push(
      <a
        key={`${uuid}-${citationIndex}`}
        href={`/facts/${uuid}`}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(
          "inline-flex items-center justify-center min-w-[1.25rem] h-4 px-1",
          "text-[10px] font-medium rounded no-underline align-super leading-none",
          "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
          "hover:bg-blue-200 dark:hover:bg-blue-800 transition-colors",
        )}
      >
        {citationIndex}
      </a>,
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  // No tokens found — render as-is
  if (citationIndex === 0) {
    return <span className={className}>{text}</span>;
  }

  return <span className={className}>{parts}</span>;
}
