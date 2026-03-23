"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { History, MessageSquare, Trash2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";
import type { ConversationListItem } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDays = Math.floor(diffHr / 24);
  if (diffDays < 30) return `${diffDays}d ago`;
  return new Date(iso).toLocaleDateString();
}

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen).trimEnd() + "...";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ConversationHistory() {
  const router = useRouter();
  const [conversations, setConversations] = useState<ConversationListItem[]>(
    [],
  );
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.conversations
      .list({ limit: 20, mode: "query" })
      .then((data) => {
        if (!cancelled) {
          setConversations(data.items);
          setLoaded(true);
        }
      })
      .catch(() => {
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleClick = useCallback(
    (id: string) => {
      router.push(`/conversation/${id}`);
    },
    [router],
  );

  const handleDelete = useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.stopPropagation();
      if (!window.confirm("Delete this conversation? Knowledge graph data will be preserved.")) return;
      try {
        await api.conversations.delete(id);
        setConversations((prev) => prev.filter((c) => c.id !== id));
      } catch {
        // Silently ignore — user can retry
      }
    },
    [],
  );

  if (!loaded) return null;

  if (conversations.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <History className="size-4" />
            Recent Conversations
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No conversations yet
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <History className="size-4" />
          Recent Conversations
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ScrollArea className="max-h-64">
          <div className="space-y-1">
            {conversations.map((conv, idx) => (
              <div key={conv.id}>
                {idx > 0 && <Separator className="my-1" />}
                <div
                  role="button"
                  tabIndex={0}
                  className="group/row flex w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-accent cursor-pointer"
                  onClick={() => handleClick(conv.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      handleClick(conv.id);
                    }
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {truncate(conv.title || "Untitled", 60)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {relativeTime(conv.updated_at)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge variant="secondary" className="text-xs">
                      <MessageSquare className="mr-1 size-3" />
                      {conv.message_count}
                    </Badge>
                    <button
                      type="button"
                      className="p-1 text-muted-foreground hover:text-destructive opacity-0 group-hover/row:opacity-100 transition-opacity"
                      onClick={(e) => handleDelete(e, conv.id)}
                      aria-label="Delete conversation"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
