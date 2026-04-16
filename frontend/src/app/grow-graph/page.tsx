"use client";

import { useState, useCallback } from "react";
import { FileText, Globe, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SourceUploadForm } from "@/components/research/SourceUploadForm";
import { WebResearchForm } from "@/components/research/WebResearchForm";
import { ResearchHistory } from "@/components/research/ResearchHistory";
import { ResearchBuildProgress } from "@/components/research/ResearchBuildProgress";
import { IngestHistory } from "@/components/research/IngestHistory";
import { api } from "@/lib/api";

type ResearchTab = "documents" | "web-research";

interface ViewingBuild {
  conversationId: string;
  messageId: string;
  title: string;
  status: "running" | "completed" | "failed";
}

export default function ResearchPage() {
  const [tab, setTab] = useState<ResearchTab>("web-research");
  const [resumeId, setResumeId] = useState<string | null>(null);
  const [viewingBuild, setViewingBuild] = useState<ViewingBuild | null>(null);
  const [viewingIngest, setViewingIngest] = useState<ViewingBuild | null>(null);

  const handleResume = useCallback((conversationId: string) => {
    setResumeId(conversationId);
    setViewingBuild(null);
    setTab("web-research");
  }, []);

  const clearResume = useCallback(() => {
    setResumeId(null);
  }, []);

  const handleView = useCallback(async (conversationId: string) => {
    try {
      const conv = await api.conversations.get(conversationId);
      const assistantMsgs = conv.messages.filter((m) => m.role === "assistant");

      if (assistantMsgs.length <= 1) {
        // Only prepare phase exists — show summary instead of build progress
        handleResume(conversationId);
        return;
      }

      // Build phase exists — show progress for the last assistant message (build msg)
      const buildMsg = assistantMsgs[assistantMsgs.length - 1];

      const status =
        buildMsg.status === "completed" ? "completed" :
        buildMsg.status === "failed" ? "failed" : "running";

      setViewingBuild({
        conversationId,
        messageId: buildMsg.id,
        title: conv.title || "Untitled",
        status: status as ViewingBuild["status"],
      });
      setTab("web-research");
    } catch {
      // Silently ignore — user can try again
    }
  }, [handleResume]);

  const clearView = useCallback(() => {
    setViewingBuild(null);
  }, []);

  const handleViewIngest = useCallback(async (conversationId: string) => {
    try {
      const conv = await api.conversations.get(conversationId);
      const assistantMsgs = conv.messages.filter((m) => m.role === "assistant");
      if (assistantMsgs.length === 0) return;

      const buildMsg = assistantMsgs[assistantMsgs.length - 1];
      const status =
        buildMsg.status === "completed" ? "completed" :
        buildMsg.status === "failed" ? "failed" : "running";

      setViewingIngest({
        conversationId,
        messageId: buildMsg.id,
        title: conv.title || "Untitled",
        status: status as ViewingBuild["status"],
      });
    } catch {
      // Silently ignore
    }
  }, []);

  const clearViewIngest = useCallback(() => {
    setViewingIngest(null);
  }, []);

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold">Grow Graph</h1>
        <p className="text-muted-foreground mt-1">
          Add new data to your knowledge graph by uploading documents or
          discovering web sources.
        </p>
        <p className="text-xs text-muted-foreground/70 mt-1">
          Use &ldquo;From Source&rdquo; to upload files directly, or
          &ldquo;From the Web&rdquo; to let an agent find and process online
          sources. Ingested data is decomposed into facts and seeds that feed
          the graph.
        </p>
      </div>

      {/* Tab toggle */}
      <div className="flex items-center gap-1 rounded-lg border p-1 mb-8 w-fit">
        <Button
          variant={tab === "documents" ? "default" : "ghost"}
          size="sm"
          onClick={() => { setTab("documents"); setViewingBuild(null); }}
          className="gap-1.5"
        >
          <FileText className="size-4" />
          From Source
        </Button>
        <Button
          variant={tab === "web-research" ? "default" : "ghost"}
          size="sm"
          onClick={() => { setTab("web-research"); setViewingIngest(null); }}
          className="gap-1.5"
        >
          <Globe className="size-4" />
          From the Web
        </Button>
      </div>

      {tab === "documents" && (
        <>
          {viewingIngest ? (
            <div className="space-y-4">
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5 -ml-2"
                onClick={clearViewIngest}
              >
                <ArrowLeft className="size-3.5" />
                Back to ingestion
              </Button>
              <h2 className="text-lg font-semibold">{viewingIngest.title}</h2>
              <ResearchBuildProgress
                conversationId={viewingIngest.conversationId}
                messageId={viewingIngest.messageId}
                initialStatus={viewingIngest.status}
              />
            </div>
          ) : (
            <div className="space-y-8">
              <SourceUploadForm />
              <IngestHistory onView={handleViewIngest} />
            </div>
          )}
        </>
      )}
      {tab === "web-research" && (
        <>
          {viewingBuild ? (
            <div className="space-y-4">
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5 -ml-2"
                onClick={clearView}
              >
                <ArrowLeft className="size-3.5" />
                Back to ingestion
              </Button>
              <h2 className="text-lg font-semibold">{viewingBuild.title}</h2>
              <ResearchBuildProgress
                conversationId={viewingBuild.conversationId}
                messageId={viewingBuild.messageId}
                initialStatus={viewingBuild.status}
              />
            </div>
          ) : (
            <div className="space-y-8">
              <WebResearchForm
                resumeConversationId={resumeId}
                onResetResume={clearResume}
              />
              <ResearchHistory onResume={handleResume} onView={handleView} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
