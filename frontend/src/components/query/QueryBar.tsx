"use client";

import { useState, useCallback, type FormEvent, type KeyboardEvent } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

interface QueryBarProps {
  onSubmit: (query: string) => void;
  disabled?: boolean;
}

export function QueryBar({ onSubmit, disabled = false }: QueryBarProps) {
  const [query, setQuery] = useState("");

  const handleSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault();
      const trimmed = query.trim();
      if (trimmed.length === 0) return;
      onSubmit(trimmed);
    },
    [query, onSubmit],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const trimmed = query.trim();
        if (trimmed.length === 0) return;
        onSubmit(trimmed);
      }
    },
    [query, onSubmit],
  );

  return (
    <form onSubmit={handleSubmit} className="flex w-full gap-2">
      <div className="relative flex-1">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="text"
          placeholder="Ask anything... e.g. 'How does photosynthesis work?'"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          className="h-11 pl-9 text-base"
        />
      </div>
      <Button
        type="submit"
        disabled={disabled || query.trim().length === 0}
        size="lg"
        className="h-11 px-6"
      >
        <Search className="size-4" />
        Search
      </Button>
    </form>
  );
}
