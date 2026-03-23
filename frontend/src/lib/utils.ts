import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ---------------------------------------------------------------------------
// Convert raw {fact:<uuid>|<label>} tokens into markdown links.
// Used by AnswerView and ChatMessage for rendering + clipboard copy.
// ---------------------------------------------------------------------------

// Matches {fact:<uuid>|<label>} (with label) or {fact:<uuid>} (legacy, no label)
const FACT_TOKEN_RE = /\{fact:([0-9a-f-]{36})(?:\|([^}]+))?\}/gi;

/**
 * Replace `{fact:<uuid>|<label>}` tokens with markdown citation links.
 * Uses the embedded label when available, falls back to numbered citations.
 */
export function linkifyFactTokens(text: string): string {
  let index = 0;
  return text.replace(FACT_TOKEN_RE, (_match, uuid: string, label?: string) => {
    index++;
    const linkText = label?.trim() || `source ${index}`;
    return `[${linkText}](/facts/${uuid})`;
  });
}
