"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { TreePine } from "lucide-react";
import { toast } from "sonner";
import { QueryBar } from "@/components/query/QueryBar";
import { QueryBudgetControls } from "@/components/query/QueryBudgetControls";
import { QuickActions } from "@/components/query/QuickActions";
import { ConversationHistory } from "@/components/chat/ConversationHistory";
import { useAuth } from "@/contexts/auth";
import { api } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const { user } = useAuth();

  const [navBudget, setNavBudget] = useState(50);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (query: string) => {
      setIsSubmitting(true);
      setError(null);

      if (user?.has_api_key) {
        toast.info("This query will use your OpenRouter API key. Costs depend on complexity.");
      } else if (user?.is_superuser) {
        toast.info("Using system API key for this query.");
      }

      try {
        const response = await api.conversations.create({
          message: query,
          nav_budget: navBudget,
          explore_budget: 0,
          mode: "query",
        });
        router.push(`/conversation/${response.id}`);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to submit query";
        setError(message);
        setIsSubmitting(false);
      }
    },
    [navBudget, router, user],
  );

  return (
    <div className="flex min-h-full flex-col items-center justify-start px-4 pt-24 pb-12">
      <main className="w-full max-w-2xl space-y-8">
        {/* Header */}
        <div className="space-y-3 text-center">
          <div className="flex items-center justify-center gap-3">
            <TreePine className="size-8 text-primary" />
            <h1 className="text-4xl font-bold tracking-tight">
              Knowledge Tree
            </h1>
          </div>
          <p className="text-muted-foreground">
            A knowledge integration system that builds understanding from raw
            external data. Ask a question and watch the knowledge graph grow.
          </p>
        </div>

        {/* Query bar */}
        <QueryBar onSubmit={handleSubmit} disabled={isSubmitting} />

        {/* Error message */}
        {error && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {/* Budget controls */}
        <QueryBudgetControls
          navBudget={navBudget}
          onNavBudgetChange={setNavBudget}
        />

        {/* Quick actions */}
        <QuickActions />

        {/* Conversation history */}
        <ConversationHistory />
      </main>
    </div>
  );
}
