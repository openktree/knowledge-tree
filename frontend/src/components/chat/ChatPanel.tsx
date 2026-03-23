"use client";

import { useEffect, useRef } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { ExpandIngestInput } from "@/components/chat/ExpandIngestInput";
import { ActiveTurnDisplay } from "@/components/chat/ActiveTurnDisplay";
import { SourcesList } from "@/components/research/SourcesList";
import { useResearchSources } from "@/hooks/useResearchSources";
import type { ConversationMessageResponse } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ChatPanelProps {
  messages: ConversationMessageResponse[];
  activeTurnAnswer: string;
  activeTurnPhase: string;
  isTurnActive: boolean;
  nodeCount: number;
  onSendMessage: (
    message: string,
    navBudget: number,
    exploreBudget: number,
    waveCount?: number,
  ) => void;
  onResynthesize?: (messageId: string) => void;
  mode?: string;
  conversationId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ChatPanel({
  messages,
  activeTurnAnswer,
  activeTurnPhase,
  isTurnActive,
  nodeCount,
  onSendMessage,
  onResynthesize,
  mode,
  conversationId,
}: ChatPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const { sources: ingestSources, isLoading: sourcesLoading } =
    useResearchSources(conversationId ?? null, mode ?? null);

  // Auto-scroll to bottom when new messages arrive or answer streams
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, activeTurnAnswer]);

  // Filter out pending/running assistant messages from display (shown as ActiveTurnDisplay)
  const displayMessages = messages.filter(
    (m) =>
      !(
        m.role === "assistant" &&
        (m.status === "pending" || m.status === "running")
      ),
  );

  return (
    <div className="flex flex-col h-full">
      {/* Ingest sources panel (above messages) */}
      {mode === "ingest" && conversationId && (
        <SourcesList
          sources={ingestSources}
          conversationId={conversationId}
          isLoading={sourcesLoading}
        />
      )}

      {/* Message history */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-3 p-4">
          {displayMessages.map((msg) => (
            <ChatMessage
              key={msg.id}
              message={msg}
              onResynthesize={msg.role === "assistant" ? onResynthesize : undefined}
              isTurnActive={isTurnActive}
            />
          ))}

          {/* Active turn streaming display */}
          {isTurnActive && (
            <ActiveTurnDisplay
              answer={activeTurnAnswer}
              phase={activeTurnPhase}
              nodeCount={nodeCount}
            />
          )}

          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      {/* Input area */}
      {mode === "ingest" ? (
        !isTurnActive &&
        displayMessages.some((m) => m.role === "assistant" && m.status === "completed") && (
          <ExpandIngestInput
            existingNodeCount={nodeCount}
            onSendMessage={onSendMessage}
            disabled={isTurnActive}
          />
        )
      ) : (
        <ChatInput onSend={onSendMessage} disabled={isTurnActive} mode={mode} />
      )}
    </div>
  );
}
