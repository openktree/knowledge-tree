"use client";

import { useState, useCallback } from "react";
import { FileText, Globe, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SourceUploadForm } from "@/components/research/SourceUploadForm";
import { WebResearchForm } from "@/components/research/WebResearchForm";
import { ResearchHistory } from "@/components/research/ResearchHistory";
import { ResearchBuildProgress } from "@/components/research/ResearchBuildProgress";
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
      const buildMsg = [...conv.messages]
        .reverse()
        .find((m) => m.role === "assistant");
      if (!buildMsg) return;

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
  }, []);

  const clearView = useCallback(() => {
    setViewingBuild(null);
  }, []);

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold">Research</h1>
        <p className="text-muted-foreground mt-1">
          Gather facts from sources, then build them into graph nodes.
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
          onClick={() => setTab("web-research")}
          className="gap-1.5"
        >
          <Globe className="size-4" />
          From the Web
        </Button>
      </div>

      {tab === "documents" && <SourceUploadForm />}
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
                Back to research
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
