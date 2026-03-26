"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  SynthesisDocumentResponse,
  SynthesisSentenceResponse,
  SentenceFactsBySourceResponse,
} from "@/types";
import { getSentenceFacts } from "@/lib/api";
import { SynthesisFactPanel } from "./SynthesisFactPanel";
import { SynthesisNodeList } from "./SynthesisNodeList";
import { SubSynthesisList } from "./SubSynthesisList";

interface SynthesisDocumentProps {
  document: SynthesisDocumentResponse;
}

export function SynthesisDocument({ document }: SynthesisDocumentProps) {
  const [selectedSentence, setSelectedSentence] = useState<number | null>(null);
  const [sentenceFacts, setSentenceFacts] = useState<
    SentenceFactsBySourceResponse[] | null
  >(null);
  const [loadingFacts, setLoadingFacts] = useState(false);

  const handleSentenceClick = async (position: number) => {
    if (selectedSentence === position) {
      setSelectedSentence(null);
      setSentenceFacts(null);
      return;
    }
    setSelectedSentence(position);
    setLoadingFacts(true);
    try {
      const facts = await getSentenceFacts(document.id, position);
      setSentenceFacts(facts);
    } catch {
      setSentenceFacts([]);
    } finally {
      setLoadingFacts(false);
    }
  };

  return (
    <div className="flex gap-6">
      {/* Main document */}
      <div className="flex-1 min-w-0">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle className="text-2xl">{document.concept}</CardTitle>
              <Badge variant="outline">
                {document.node_type === "supersynthesis"
                  ? "Super-Synthesis"
                  : "Synthesis"}
              </Badge>
              <Badge
                variant={
                  document.visibility === "public" ? "default" : "secondary"
                }
              >
                {document.visibility}
              </Badge>
            </div>
            {document.created_at && (
              <p className="text-sm text-muted-foreground">
                Created {new Date(document.created_at).toLocaleDateString()}
              </p>
            )}
          </CardHeader>
          <CardContent className="space-y-1">
            {document.sentences.length > 0 ? (
              document.sentences.map((sentence) => (
                <SynthesisSentenceView
                  key={sentence.position}
                  sentence={sentence}
                  isSelected={selectedSentence === sentence.position}
                  onClick={() => handleSentenceClick(sentence.position)}
                />
              ))
            ) : document.definition ? (
              <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap">
                {document.definition}
              </div>
            ) : (
              <p className="text-muted-foreground">No content available.</p>
            )}
          </CardContent>
        </Card>

        {/* Sub-syntheses (for supersynthesis) */}
        {document.sub_syntheses.length > 0 && (
          <SubSynthesisList subSyntheses={document.sub_syntheses} />
        )}

        {/* Referenced nodes */}
        {document.referenced_nodes.length > 0 && (
          <SynthesisNodeList nodes={document.referenced_nodes} />
        )}
      </div>

      {/* Right panel — facts for selected sentence */}
      {selectedSentence !== null && (
        <div className="w-96 shrink-0">
          <SynthesisFactPanel
            position={selectedSentence}
            facts={sentenceFacts}
            loading={loadingFacts}
            onClose={() => {
              setSelectedSentence(null);
              setSentenceFacts(null);
            }}
          />
        </div>
      )}
    </div>
  );
}

// ── Sentence component ────────────────────────────────────────────

interface SynthesisSentenceViewProps {
  sentence: SynthesisSentenceResponse;
  isSelected: boolean;
  onClick: () => void;
}

function SynthesisSentenceView({
  sentence,
  isSelected,
  onClick,
}: SynthesisSentenceViewProps) {
  // Parse node links: [text](/nodes/uuid) -> clickable links
  const parts = sentence.text.split(/(\[[^\]]+\]\(\/nodes\/[a-f0-9-]+\))/g);

  return (
    <span
      className={`inline cursor-pointer transition-colors rounded px-0.5 ${
        isSelected
          ? "bg-primary/10 ring-1 ring-primary/30"
          : "hover:bg-muted/50"
      }`}
      onClick={onClick}
    >
      {parts.map((part, i) => {
        const match = part.match(/\[([^\]]+)\]\(\/nodes\/([a-f0-9-]+)\)/);
        if (match) {
          return (
            <a
              key={i}
              href={`/nodes/${match[2]}`}
              className="text-primary underline underline-offset-2 hover:text-primary/80"
              onClick={(e) => e.stopPropagation()}
            >
              {match[1]}
            </a>
          );
        }
        return <span key={i}>{part}</span>;
      })}{" "}
      {sentence.fact_count > 0 && (
        <Badge variant="outline" className="text-[10px] px-1 py-0 align-super">
          {sentence.fact_count}
        </Badge>
      )}
    </span>
  );
}
