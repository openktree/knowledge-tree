"use client";

import { useState, useCallback } from "react";
import { Send, ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ChatInputProps {
  onSend: (message: string, navBudget: number, exploreBudget: number, waveCount?: number) => void;
  disabled?: boolean;
  mode?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ChatInput({ onSend, disabled = false, mode = "research" }: ChatInputProps) {
  const [message, setMessage] = useState("");
  const [showBudgets, setShowBudgets] = useState(false);
  const [navBudget, setNavBudget] = useState(30);
  const [exploreBudget, setExploreBudget] = useState(2);
  const [waveCount, setWaveCount] = useState(2);

  const handleSubmit = useCallback(() => {
    const trimmed = message.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed, navBudget, mode === "query" ? 0 : exploreBudget, mode === "query" ? undefined : waveCount);
    setMessage("");
  }, [message, disabled, onSend, navBudget, exploreBudget, waveCount, mode]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="border-t bg-background p-3 space-y-2">
      {/* Budget controls (collapsible) */}
      <button
        type="button"
        onClick={() => setShowBudgets((v) => !v)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {showBudgets ? (
          <ChevronUp className="h-3 w-3" />
        ) : (
          <ChevronDown className="h-3 w-3" />
        )}
        {mode === "query"
          ? `Budget: Nav ${navBudget}`
          : `Budget: Nav ${navBudget} / Explore ${exploreBudget} / Waves ${waveCount}`}
      </button>

      {showBudgets && (
        <div className="space-y-2 rounded-md border p-3">
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground w-16 shrink-0">
              Nav: {navBudget}
            </span>
            <Slider
              value={[navBudget]}
              min={1}
              max={100}
              step={1}
              onValueChange={([v]) => setNavBudget(v)}
            />
          </div>
          {mode !== "query" && (
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground w-16 shrink-0">
                Explore: {exploreBudget}
              </span>
              <Slider
                value={[exploreBudget]}
                min={1}
                max={20}
                step={1}
                onValueChange={([v]) => setExploreBudget(v)}
              />
            </div>
          )}
          {mode !== "query" && (
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground w-16 shrink-0">
                Waves: {waveCount}
              </span>
              <Slider
                value={[waveCount]}
                min={1}
                max={5}
                step={1}
                onValueChange={([v]) => setWaveCount(v)}
              />
            </div>
          )}
        </div>
      )}

      {/* Input area */}
      <div className="flex items-end gap-2">
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a follow-up..."
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 min-h-[38px] max-h-32"
          style={{ fieldSizing: "content" } as React.CSSProperties}
        />
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={disabled || !message.trim()}
          className="h-[38px] px-3"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
