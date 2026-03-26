"use client";

import { useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { X, CircleDot, FileText, Loader2 } from "lucide-react";
import type {
  SynthesisDocumentResponse,
  SynthesisSentenceResponse,
  SynthesisNodeResponse,
  SentenceFactLink,
} from "@/types";
import { getSentenceFacts } from "@/lib/api";
import { SynthesisNodeList } from "./SynthesisNodeList";
import { SubSynthesisList } from "./SubSynthesisList";

interface SynthesisDocumentProps {
  document: SynthesisDocumentResponse;
}

export function SynthesisDocument({ document }: SynthesisDocumentProps) {
  const [selectedPosition, setSelectedPosition] = useState<number | null>(null);
  const [factLinks, setFactLinks] = useState<SentenceFactLink[] | null>(null);
  const [loadingFacts, setLoadingFacts] = useState(false);

  const selectedSentence =
    selectedPosition !== null
      ? document.sentences[selectedPosition] ?? null
      : null;

  // Build a node lookup from referenced_nodes
  const nodeMap = new Map<string, SynthesisNodeResponse>();
  for (const n of document.referenced_nodes) {
    nodeMap.set(n.node_id, n);
  }

  const handleSentenceClick = async (position: number) => {
    if (selectedPosition === position) {
      setSelectedPosition(null);
      setFactLinks(null);
      return;
    }
    setSelectedPosition(position);
    setFactLinks(null);

    // Lazy-load fact links for this sentence
    const sentence = document.sentences[position];
    if (sentence && sentence.fact_count > 0) {
      setLoadingFacts(true);
      try {
        const facts = await getSentenceFacts(document.id, position);
        setFactLinks(facts);
      } catch {
        setFactLinks([]);
      } finally {
        setLoadingFacts(false);
      }
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
                  isSelected={selectedPosition === sentence.position}
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

      {/* Right panel — sentence details (lazy-loaded) */}
      {selectedSentence && (
        <div className="w-96 shrink-0">
          <Card className="sticky top-4">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <CardTitle className="text-sm">
                Sentence {selectedSentence.position + 1}
              </CardTitle>
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                onClick={() => {
                  setSelectedPosition(null);
                  setFactLinks(null);
                }}
              >
                <X className="size-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-4 max-h-[70vh] overflow-y-auto">
              {/* Sentence text */}
              <p className="text-sm text-muted-foreground italic border-l-2 pl-3">
                {selectedSentence.text.replace(
                  /\[([^\]]+)\]\([^)]+\)/g,
                  "$1"
                )}
              </p>

              {/* Related nodes (already in the response) */}
              {selectedSentence.node_ids.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-medium flex items-center gap-1.5">
                    <CircleDot className="size-3" />
                    Related Nodes ({selectedSentence.node_ids.length})
                  </h4>
                  <div className="space-y-1">
                    {selectedSentence.node_ids.map((nid) => {
                      const nodeInfo = nodeMap.get(nid);
                      return (
                        <Link
                          key={nid}
                          href={`/nodes/${nid}`}
                          className="flex items-center gap-2 rounded border px-2 py-1.5 text-xs hover:bg-accent transition-colors"
                        >
                          <CircleDot className="size-3 text-primary shrink-0" />
                          <span className="truncate">
                            {nodeInfo?.concept ?? nid.slice(0, 8) + "..."}
                          </span>
                          {nodeInfo?.node_type && (
                            <Badge
                              variant="outline"
                              className="text-[9px] px-1 py-0 shrink-0"
                            >
                              {nodeInfo.node_type}
                            </Badge>
                          )}
                        </Link>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Related facts (lazy-loaded) */}
              {selectedSentence.fact_count > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-medium flex items-center gap-1.5">
                    <FileText className="size-3" />
                    Closest Facts ({selectedSentence.fact_count})
                  </h4>
                  {loadingFacts ? (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                      <Loader2 className="size-3 animate-spin" />
                      Loading facts...
                    </div>
                  ) : factLinks && factLinks.length > 0 ? (
                    <ul className="space-y-1">
                      {factLinks.map((fl) => (
                        <li key={fl.fact_id} className="text-xs">
                          <Link
                            href={`/facts/${fl.fact_id}`}
                            className="text-primary hover:underline"
                          >
                            {fl.fact_id.slice(0, 12)}...
                          </Link>
                          <span className="text-muted-foreground ml-1">
                            ({(fl.distance * 100).toFixed(0)}% match)
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-[10px] text-muted-foreground">
                      No fact links available.
                    </p>
                  )}
                </div>
              )}

              {selectedSentence.node_ids.length === 0 &&
                selectedSentence.fact_count === 0 && (
                  <p className="text-xs text-muted-foreground">
                    No nodes or facts linked to this sentence.
                  </p>
                )}
            </CardContent>
          </Card>
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
  const parts = sentence.text.split(
    /(\[[^\]]+\]\(\/(?:nodes|facts)\/[a-f0-9-]+\))/g
  );

  const hasInfo = sentence.fact_count > 0 || sentence.node_ids.length > 0;

  return (
    <span
      className={`inline cursor-pointer transition-colors rounded px-0.5 ${
        isSelected
          ? "bg-primary/10 ring-1 ring-primary/30"
          : hasInfo
            ? "hover:bg-muted/50"
            : ""
      }`}
      onClick={hasInfo ? onClick : undefined}
    >
      {parts.map((part, i) => {
        const match = part.match(
          /\[([^\]]+)\]\(\/(nodes|facts)\/([a-f0-9-]+)\)/
        );
        if (match) {
          const [, text, type, id] = match;
          return (
            <Link
              key={i}
              href={`/${type}/${id}`}
              className="text-primary underline underline-offset-2 hover:text-primary/80"
              onClick={(e) => e.stopPropagation()}
            >
              {text}
            </Link>
          );
        }
        return <span key={i}>{part}</span>;
      })}{" "}
      {hasInfo && (
        <Badge
          variant={isSelected ? "default" : "outline"}
          className="text-[10px] px-1 py-0 align-super"
        >
          {sentence.node_ids.length > 0 && `${sentence.node_ids.length}n`}
          {sentence.node_ids.length > 0 && sentence.fact_count > 0 && " "}
          {sentence.fact_count > 0 && `${sentence.fact_count}f`}
        </Badge>
      )}
    </span>
  );
}
