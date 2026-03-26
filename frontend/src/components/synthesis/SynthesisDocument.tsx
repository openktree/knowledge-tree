"use client";

import { useState, useMemo, useCallback } from "react";
import Link from "next/link";
import Markdown from "react-markdown";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { X, CircleDot } from "lucide-react";
import { formatSynthesisConcept } from "./utils";
import type {
  SynthesisDocumentResponse,
  SynthesisSentenceResponse,
  SynthesisNodeResponse,
} from "@/types";
import { SynthesisNodeList } from "./SynthesisNodeList";
import { SubSynthesisList } from "./SubSynthesisList";

interface SynthesisDocumentProps {
  document: SynthesisDocumentResponse;
}

function buildParagraphSentenceMap(
  definition: string,
  sentences: SynthesisSentenceResponse[]
): Map<string, SynthesisSentenceResponse[]> {
  const map = new Map<string, SynthesisSentenceResponse[]>();
  if (!definition || sentences.length === 0) return map;

  const paragraphs = definition
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);

  for (const para of paragraphs) {
    const plainPara = para
      .replace(/^#{1,6}\s+/, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\*([^*]+)\*/g, "$1");

    const matched: SynthesisSentenceResponse[] = [];
    for (const s of sentences) {
      const plainSentence = s.text
        .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .replace(/\*([^*]+)\*/g, "$1")
        .trim();
      if (
        plainSentence.length > 10 &&
        plainPara.includes(plainSentence.slice(0, 40))
      ) {
        matched.push(s);
      }
    }

    if (matched.length > 0) {
      const key = para.slice(0, 60).trim();
      map.set(key, matched);
    }
  }

  return map;
}

/** Recursively extract plain text from React children. */
function extractText(children: React.ReactNode): string {
  if (!children) return "";
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(extractText).join("");
  if (typeof children === "object" && "props" in children) {
    return extractText(
      (children as React.ReactElement).props?.children
    );
  }
  return "";
}

export function SynthesisDocument({ document }: SynthesisDocumentProps) {
  const [selectedParaKey, setSelectedParaKey] = useState<string | null>(null);
  const [selectedNodes, setSelectedNodes] = useState<string[]>([]);

  const nodeMap = useMemo(() => {
    const map = new Map<string, SynthesisNodeResponse>();
    for (const n of document.referenced_nodes) {
      map.set(n.node_id, n);
    }
    return map;
  }, [document.referenced_nodes]);

  const paragraphMap = useMemo(
    () =>
      buildParagraphSentenceMap(
        document.definition || "",
        document.sentences
      ),
    [document.definition, document.sentences]
  );

  const handleSectionClick = useCallback(
    (key: string, sentences: SynthesisSentenceResponse[]) => {
      if (selectedParaKey === key) {
        setSelectedParaKey(null);
        setSelectedNodes([]);
        return;
      }
      setSelectedParaKey(key);
      // Collect unique node IDs from all sentences in this section
      const nodeIds = new Set<string>();
      for (const s of sentences) {
        for (const nid of s.node_ids) {
          nodeIds.add(nid);
        }
      }
      setSelectedNodes(Array.from(nodeIds));
    },
    [selectedParaKey]
  );

  // Helper: get section info for a text block
  const getSectionInfo = useCallback(
    (children: React.ReactNode) => {
      const text = extractText(children);
      const key = text.slice(0, 60).trim();
      const sentences = paragraphMap.get(key);
      const hasInfo =
        sentences &&
        sentences.some((s) => s.node_ids.length > 0);
      const totalNodes = sentences
        ? new Set(sentences.flatMap((s) => s.node_ids)).size
        : 0;
      const isSelected = selectedParaKey === key;
      return { key, sentences, hasInfo, totalNodes, isSelected };
    },
    [paragraphMap, selectedParaKey]
  );

  const markdownComponents = useMemo(
    () => ({
      p: ({ children }: { children?: React.ReactNode }) => {
        const { key, sentences, hasInfo, totalNodes, isSelected } =
          getSectionInfo(children);
        return (
          <p
            className={`mb-4 leading-relaxed rounded-sm transition-colors ${
              isSelected
                ? "bg-primary/5 ring-1 ring-primary/20 px-2 py-1 -mx-2"
                : hasInfo
                  ? "hover:bg-muted/30 cursor-pointer px-2 py-1 -mx-2"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? () => handleSectionClick(key, sentences)
                : undefined
            }
          >
            {children}
            {hasInfo && totalNodes > 0 && (
              <>
                {" "}
                <Badge
                  variant={isSelected ? "default" : "outline"}
                  className="text-[10px] px-1 py-0 align-super"
                >
                  {totalNodes}n
                </Badge>
              </>
            )}
          </p>
        );
      },
      h1: ({ children }: { children?: React.ReactNode }) => {
        const { key, sentences, hasInfo, totalNodes, isSelected } =
          getSectionInfo(children);
        return (
          <h1
            className={`text-2xl font-bold mt-6 mb-3 rounded-sm transition-colors ${
              isSelected
                ? "bg-primary/5 ring-1 ring-primary/20 px-2 -mx-2"
                : hasInfo
                  ? "hover:bg-muted/30 cursor-pointer px-2 -mx-2"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? () => handleSectionClick(key, sentences)
                : undefined
            }
          >
            {children}
            {hasInfo && totalNodes > 0 && (
              <>
                {" "}
                <Badge
                  variant={isSelected ? "default" : "outline"}
                  className="text-[10px] px-1 py-0 align-super"
                >
                  {totalNodes}n
                </Badge>
              </>
            )}
          </h1>
        );
      },
      h2: ({ children }: { children?: React.ReactNode }) => {
        const { key, sentences, hasInfo, totalNodes, isSelected } =
          getSectionInfo(children);
        return (
          <h2
            className={`text-xl font-semibold mt-5 mb-2 rounded-sm transition-colors ${
              isSelected
                ? "bg-primary/5 ring-1 ring-primary/20 px-2 -mx-2"
                : hasInfo
                  ? "hover:bg-muted/30 cursor-pointer px-2 -mx-2"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? () => handleSectionClick(key, sentences)
                : undefined
            }
          >
            {children}
            {hasInfo && totalNodes > 0 && (
              <>
                {" "}
                <Badge
                  variant={isSelected ? "default" : "outline"}
                  className="text-[10px] px-1 py-0 align-super"
                >
                  {totalNodes}n
                </Badge>
              </>
            )}
          </h2>
        );
      },
      h3: ({ children }: { children?: React.ReactNode }) => {
        const { key, sentences, hasInfo, totalNodes, isSelected } =
          getSectionInfo(children);
        return (
          <h3
            className={`text-lg font-medium mt-4 mb-2 rounded-sm transition-colors ${
              isSelected
                ? "bg-primary/5 ring-1 ring-primary/20 px-2 -mx-2"
                : hasInfo
                  ? "hover:bg-muted/30 cursor-pointer px-2 -mx-2"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? () => handleSectionClick(key, sentences)
                : undefined
            }
          >
            {children}
            {hasInfo && totalNodes > 0 && (
              <>
                {" "}
                <Badge
                  variant={isSelected ? "default" : "outline"}
                  className="text-[10px] px-1 py-0 align-super"
                >
                  {totalNodes}n
                </Badge>
              </>
            )}
          </h3>
        );
      },
      a: ({
        href,
        children,
      }: {
        href?: string;
        children?: React.ReactNode;
      }) => {
        if (href?.startsWith("/nodes/") || href?.startsWith("/facts/")) {
          return (
            <Link
              href={href}
              className="text-primary underline underline-offset-2 hover:text-primary/80"
              onClick={(e) => e.stopPropagation()}
            >
              {children}
            </Link>
          );
        }
        return (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline underline-offset-2"
            onClick={(e) => e.stopPropagation()}
          >
            {children}
          </a>
        );
      },
      li: ({ children }: { children?: React.ReactNode }) => {
        const { key, sentences, hasInfo, isSelected } =
          getSectionInfo(children);
        return (
          <li
            className={`mb-1 ${
              isSelected
                ? "bg-primary/5 rounded px-1"
                : hasInfo
                  ? "hover:bg-muted/30 cursor-pointer rounded px-1"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? (e) => {
                    e.stopPropagation();
                    handleSectionClick(key, sentences);
                  }
                : undefined
            }
          >
            {children}
          </li>
        );
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedParaKey, paragraphMap, getSectionInfo]
  );

  const hasDefinition = document.definition && document.definition.trim();

  return (
    <div className="flex gap-6">
      {/* Main document */}
      <div className="flex-1 min-w-0">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle className="text-2xl">
                {formatSynthesisConcept(document.concept).title}
              </CardTitle>
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
            {(formatSynthesisConcept(document.concept).date || document.created_at) && (
              <p className="text-sm text-muted-foreground">
                {formatSynthesisConcept(document.concept).date
                  ?? (document.created_at && new Date(document.created_at).toLocaleDateString())}
              </p>
            )}
          </CardHeader>
          <CardContent className="prose prose-sm dark:prose-invert max-w-none">
            {hasDefinition ? (
              <Markdown components={markdownComponents}>
                {document.definition!}
              </Markdown>
            ) : (
              <p className="text-muted-foreground">No content available.</p>
            )}
          </CardContent>
        </Card>

        {document.sub_syntheses.length > 0 && (
          <SubSynthesisList subSyntheses={document.sub_syntheses} />
        )}
        {document.referenced_nodes.length > 0 && (
          <SynthesisNodeList nodes={document.referenced_nodes} />
        )}
      </div>

      {/* Right panel — related nodes for clicked section */}
      {selectedNodes.length > 0 && (
        <div className="w-80 shrink-0">
          <Card className="sticky top-4">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <CardTitle className="text-sm flex items-center gap-1.5">
                <CircleDot className="size-3.5" />
                Related Nodes ({selectedNodes.length})
              </CardTitle>
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                onClick={() => {
                  setSelectedParaKey(null);
                  setSelectedNodes([]);
                }}
              >
                <X className="size-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-1.5 max-h-[70vh] overflow-y-auto">
              {selectedNodes.map((nid) => {
                const nodeInfo = nodeMap.get(nid);
                return (
                  <Link
                    key={nid}
                    href={`/nodes/${nid}`}
                    className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-accent transition-colors"
                  >
                    <CircleDot className="size-3.5 text-primary shrink-0" />
                    <span className="truncate">
                      {nodeInfo?.concept ?? nid.slice(0, 8) + "..."}
                    </span>
                    {nodeInfo?.node_type && (
                      <Badge
                        variant="outline"
                        className="text-[10px] px-1.5 py-0 shrink-0 ml-auto"
                      >
                        {nodeInfo.node_type}
                      </Badge>
                    )}
                  </Link>
                );
              })}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
