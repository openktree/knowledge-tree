"use client";

import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getSynthesis, getSentenceFacts } from "@/lib/api";
import type {
  SynthesisDocumentResponse,
  SentenceFactLink,
  SynthesisNodeResponse,
} from "@/types";
import { formatSynthesisConcept } from "./utils";

interface PromptEntry {
  id: string;
  name: string;
  stage: string;
  purpose: string;
  prompt: string;
}

interface ExportPDFProps {
  documentId: string;
  concept: string;
}

export function ExportPDFButton({ documentId, concept }: ExportPDFProps) {
  const [exporting, setExporting] = useState(false);

  const handleExport = async () => {
    setExporting(true);
    try {
      // 1. Load the full document
      const doc = await getSynthesis(documentId);

      // 2. Load all fact details for sentences that have facts
      const factMap = new Map<number, SentenceFactLink[]>();
      for (const s of doc.sentences) {
        if (s.fact_count > 0) {
          try {
            const facts = await getSentenceFacts(documentId, s.position);
            factMap.set(s.position, facts);
          } catch {
            // skip
          }
        }
      }

      // 3. Load node definitions
      const nodeDefinitions = new Map<string, string>();
      for (const n of doc.referenced_nodes) {
        try {
          const resp = await fetch(
            `${process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") || "http://localhost:8000"}/api/v1/nodes/${n.node_id}`,
            {
              headers: {
                Authorization: `Bearer ${localStorage.getItem("access_token") || ""}`,
              },
            }
          );
          if (resp.ok) {
            const nodeData = await resp.json();
            if (nodeData.definition) {
              nodeDefinitions.set(n.node_id, nodeData.definition);
            }
          }
        } catch {
          // skip
        }
      }

      // 4. Load prompts for transparency section
      let prompts: PromptEntry[] = [];
      try {
        const baseUrl =
          process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ||
          "http://localhost:8000";
        const resp = await fetch(`${baseUrl}/api/v1/prompts`);
        if (resp.ok) {
          const data = await resp.json();
          prompts = data.prompts || [];
        }
      } catch {
        // prompts section will be empty
      }

      // 5. Generate and download
      const html = buildExportHTML(doc, factMap, nodeDefinitions, prompts);
      downloadAsHTML(html, concept);
    } catch (err) {
      console.error("Export failed:", err);
    } finally {
      setExporting(false);
    }
  };

  return (
    <Button
      variant="ghost"
      size="sm"
      className="text-stone-500 hover:text-stone-700"
      onClick={handleExport}
      disabled={exporting}
    >
      {exporting ? (
        <Loader2 className="mr-2 size-4 animate-spin" />
      ) : (
        <Download className="mr-2 size-4" />
      )}
      Export
    </Button>
  );
}

// ── HTML Generation ──────────────────────────────────────────────

function buildExportHTML(
  doc: SynthesisDocumentResponse,
  factMap: Map<number, SentenceFactLink[]>,
  nodeDefinitions: Map<string, string>,
  prompts: PromptEntry[] = []
): string {
  const { title, date } = formatSynthesisConcept(doc.concept);

  // Build node reference index: node_id -> reference number
  const nodeIndex = new Map<string, number>();
  doc.referenced_nodes.forEach((n, i) => {
    nodeIndex.set(n.node_id, i + 1);
  });

  // Build fact reference index: fact_id -> global reference number
  const factIndex = new Map<string, number>();
  let factCounter = 1;
  for (const [, facts] of factMap) {
    for (const f of facts) {
      if (!factIndex.has(f.fact_id)) {
        factIndex.set(f.fact_id, factCounter++);
      }
    }
  }

  // Process definition text — replace node links with numbered references
  let body = doc.definition || "";

  // Replace [text](/nodes/uuid) with text[N]
  body = body.replace(
    /\[([^\]]+)\]\(\/nodes\/([a-f0-9-]+)\)/g,
    (_, text, nodeId) => {
      const num = nodeIndex.get(nodeId);
      return num ? `${text}<sup class="node-ref">[${num}]</sup>` : text;
    }
  );

  // Replace [text](/facts/uuid) with text
  body = body.replace(/\[([^\]]+)\]\(\/facts\/[a-f0-9-]+\)/g, "$1");

  // Convert markdown to HTML (basic)
  body = markdownToHTML(body);

  // Insert fact citations after sentences that have them
  // We do this by matching sentence text in the HTML
  for (const [position, facts] of factMap) {
    const sentence = doc.sentences[position];
    if (!sentence || facts.length === 0) continue;

    const refs = facts
      .map((f) => {
        const num = factIndex.get(f.fact_id);
        return num ? `<sup class="fact-ref">[${num}]</sup>` : "";
      })
      .join("");

    if (refs) {
      // Try to find a unique snippet from the sentence in the body
      const plainSnippet = sentence.text
        .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .slice(0, 60);

      // Find the snippet and append refs after the next period
      const idx = body.indexOf(plainSnippet);
      if (idx >= 0) {
        const periodIdx = body.indexOf(".", idx + plainSnippet.length - 10);
        if (periodIdx >= 0 && periodIdx < idx + plainSnippet.length + 100) {
          body =
            body.slice(0, periodIdx + 1) +
            refs +
            body.slice(periodIdx + 1);
        }
      }
    }
  }

  // Build facts section
  const factsSection = buildFactsSection(factIndex, factMap);

  // Build node annexes
  const annexes = buildNodeAnnexes(doc.referenced_nodes, nodeIndex, nodeDefinitions);

  // Build prompt transparency section
  const promptSection = buildPromptTransparency(prompts);

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>${escapeHTML(title)}</title>
<style>
  @page { margin: 2.5cm; size: A4; }
  body {
    font-family: 'Georgia', 'Times New Roman', serif;
    font-size: 11pt;
    line-height: 1.7;
    color: #1c1915;
    max-width: 700px;
    margin: 0 auto;
    padding: 2rem;
  }
  h1 { font-size: 22pt; font-weight: 600; margin-bottom: 0.3rem; line-height: 1.25; }
  h2 { font-size: 15pt; font-weight: 600; margin-top: 1.5rem; margin-bottom: 0.5rem; border-bottom: 1px solid #d4cdc2; padding-bottom: 0.3rem; }
  h3 { font-size: 13pt; font-weight: 600; margin-top: 1.2rem; margin-bottom: 0.4rem; }
  p { margin-bottom: 0.8rem; text-align: justify; }
  .meta { font-size: 9.5pt; color: #6a6059; margin-bottom: 1.5rem; }
  .meta .badge {
    display: inline-block; font-size: 8pt; text-transform: uppercase;
    letter-spacing: 0.08em; font-weight: 600; padding: 1px 6px;
    border: 1px solid #d4cdc2; border-radius: 3px; margin-right: 6px;
    font-family: 'Helvetica', 'Arial', sans-serif;
  }
  sup.node-ref { color: #1e63a8; font-size: 8pt; font-weight: 600; font-family: sans-serif; }
  sup.fact-ref { color: #7c4e28; font-size: 8pt; font-weight: 600; font-family: sans-serif; }
  ul, ol { padding-left: 1.5rem; margin-bottom: 0.8rem; }
  li { margin-bottom: 0.3rem; }
  strong { font-weight: 700; }
  em { font-style: italic; }
  hr { border: none; border-top: 1px solid #d4cdc2; margin: 2rem 0; }

  .facts-section { margin-top: 2rem; page-break-before: always; }
  .facts-section h2 { color: #7c4e28; }
  .facts-disclaimer {
    font-size: 9pt; color: #6a6059; font-style: italic;
    background: #faf8f5; border: 1px solid #e8e3d8; border-radius: 4px;
    padding: 0.6rem 0.8rem; margin-bottom: 1rem; line-height: 1.5;
    font-family: 'Helvetica', 'Arial', sans-serif;
  }
  .fact-entry {
    font-size: 9.5pt; margin-bottom: 0.6rem; padding-left: 1.5rem;
    text-indent: -1.5rem; line-height: 1.5;
  }
  .fact-num { color: #7c4e28; font-weight: 700; font-family: sans-serif; }
  .fact-type {
    font-size: 8pt; text-transform: uppercase; color: #6a6059;
    font-family: sans-serif; letter-spacing: 0.05em;
  }
  .fact-source { font-size: 9pt; color: #6a6059; }
  .fact-url { font-size: 8.5pt; color: #1e63a8; word-break: break-all; }

  .annexes { margin-top: 2rem; page-break-before: always; }
  .annexes h2 { color: #1e63a8; }
  .annex-entry { margin-bottom: 1.2rem; }
  .annex-num { color: #1e63a8; font-weight: 700; font-family: sans-serif; font-size: 10pt; }
  .annex-name { font-weight: 600; font-size: 11pt; }
  .annex-def { font-size: 10pt; color: #3d3830; margin-top: 0.3rem; line-height: 1.6; }

  .prompts-section { margin-top: 2rem; page-break-before: always; }
  .prompts-section h2 { color: #5a5248; }
  .prompts-intro {
    font-size: 9.5pt; color: #6a6059; margin-bottom: 1rem; line-height: 1.5;
  }
  .prompt-entry { margin-bottom: 1.5rem; }
  .prompt-header { margin-bottom: 0.3rem; }
  .prompt-name { font-weight: 600; font-size: 10.5pt; }
  .prompt-stage {
    font-size: 8pt; text-transform: uppercase; letter-spacing: 0.08em;
    color: #6a6059; font-family: sans-serif; margin-left: 0.5rem;
  }
  .prompt-purpose {
    font-size: 9.5pt; color: #6a6059; font-style: italic;
    margin-bottom: 0.4rem; line-height: 1.5;
  }
  .prompt-text {
    font-size: 8.5pt; font-family: 'Courier New', monospace;
    background: #f8f5f0; border: 1px solid #e8e3d8; border-radius: 4px;
    padding: 0.6rem 0.8rem; line-height: 1.45;
    white-space: pre-wrap; word-wrap: break-word;
    color: #3d3830; max-height: 300px; overflow-y: auto;
  }

  @media print {
    body { padding: 0; }
    .no-print { display: none; }
  }
</style>
</head>
<body>

<h1>${escapeHTML(title)}</h1>
<div class="meta">
  <span class="badge">${doc.node_type === "supersynthesis" ? "Super-Synthesis" : "Synthesis"}</span>
  <span class="badge">${doc.visibility}</span>
  ${date ? `<br>${date}` : doc.created_at ? `<br>${new Date(doc.created_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}` : ""}
  <br>${doc.sentences.length} sections · ${doc.referenced_nodes.length} nodes · ${factIndex.size} facts cited
</div>

${body}

${factsSection}

${annexes}

${promptSection}

</body>
</html>`;
}

function buildFactsSection(
  factIndex: Map<string, number>,
  factMap: Map<number, SentenceFactLink[]>
): string {
  if (factIndex.size === 0) return "";

  // Collect unique facts in order
  const allFacts = new Map<string, SentenceFactLink>();
  for (const [, facts] of factMap) {
    for (const f of facts) {
      if (!allFacts.has(f.fact_id)) {
        allFacts.set(f.fact_id, f);
      }
    }
  }

  let html = `<div class="facts-section">
<h2>Evidence Citations</h2>
<div class="facts-disclaimer">
<strong>Note on fact extraction:</strong> Facts in this section were extracted from original sources by an AI language model.
There may be minor differences between the fact text and the original source text. The extraction process makes facts
atomic and self-contained — for example, pronouns like "he" or "it" are replaced with the actual subject so each fact
can be understood independently. The original source URL is provided for verification.
</div>
`;

  for (const [factId, fact] of allFacts) {
    const num = factIndex.get(factId);
    if (!num) continue;

    html += `<div class="fact-entry">
<span class="fact-num">[${num}]</span> `;

    if (fact.fact_type) {
      html += `<span class="fact-type">${escapeHTML(fact.fact_type)}</span> `;
    }

    html += escapeHTML(fact.content || "");

    if (fact.author || fact.source_title || fact.source_uri) {
      html += `<br><span class="fact-source">`;
      if (fact.author) html += `${escapeHTML(fact.author)}`;
      if (fact.author && fact.source_title) html += ` — `;
      if (fact.source_title) html += `<em>${escapeHTML(fact.source_title)}</em>`;
      html += `</span>`;
      if (fact.source_uri) {
        html += `<br><span class="fact-url">${escapeHTML(fact.source_uri)}</span>`;
      }
    }

    html += `</div>\n`;
  }

  html += `</div>`;
  return html;
}

function buildNodeAnnexes(
  nodes: SynthesisNodeResponse[],
  nodeIndex: Map<string, number>,
  nodeDefinitions: Map<string, string>
): string {
  if (nodes.length === 0) return "";

  let html = `<div class="annexes">
<h2>Annex: Node Definitions</h2>
<p style="font-size: 9.5pt; color: #6a6059; margin-bottom: 1rem;">
Definitions of knowledge graph nodes referenced in this synthesis. Each node represents a concept, entity, or topic
with provenance-tracked facts from external sources.
</p>
`;

  for (const node of nodes) {
    const num = nodeIndex.get(node.node_id);
    if (!num) continue;

    const def = nodeDefinitions.get(node.node_id);

    html += `<div class="annex-entry">
<span class="annex-num">[${num}]</span> <span class="annex-name">${escapeHTML(node.concept)}</span>
<span style="font-size: 9pt; color: #6a6059; text-transform: uppercase;"> (${escapeHTML(node.node_type || "concept")})</span>`;

    if (def) {
      // Truncate long definitions
      const truncated = def.length > 800 ? def.slice(0, 800) + "..." : def;
      html += `<div class="annex-def">${escapeHTML(truncated)}</div>`;
    } else {
      html += `<div class="annex-def" style="font-style: italic; color: #9a9088;">No definition available.</div>`;
    }

    html += `</div>\n`;
  }

  html += `</div>`;
  return html;
}

// ── Prompt Transparency ──────────────────────────────────────────

function buildPromptTransparency(prompts: PromptEntry[]): string {
  if (prompts.length === 0) return "";

  // Group prompts by stage
  const stages = new Map<string, PromptEntry[]>();
  for (const p of prompts) {
    if (!stages.has(p.stage)) stages.set(p.stage, []);
    stages.get(p.stage)!.push(p);
  }

  let html = `<div class="prompts-section">
<h2>Prompt Transparency</h2>
<div class="prompts-intro">
<p>This section discloses all LLM system prompts used in the knowledge pipeline that produced this document.
Every piece of AI-generated content — from fact extraction to synthesis writing — was guided by these instructions.
Prompt transparency supports research credibility: readers can evaluate not just the evidence, but the reasoning
framework the AI was given to process it.</p>
<p>The prompts below are the <strong>exact system instructions</strong> sent to the language model at each pipeline stage.
User-specific context (the topic, node data, facts) is appended at runtime but is not shown here as it varies per run.</p>
</div>
`;

  for (const [stage, stagePrompts] of stages) {
    html += `<h3 style="color: #5a5248; font-size: 12pt; margin-top: 1.5rem; margin-bottom: 0.8rem;">${escapeHTML(stage)}</h3>\n`;

    for (const p of stagePrompts) {
      // Truncate very long prompts for readability
      const promptText = p.prompt.length > 3000
        ? p.prompt.slice(0, 3000) + "\n\n[... truncated for brevity — full prompt available via API at /api/v1/prompts]"
        : p.prompt;

      html += `<div class="prompt-entry">
<div class="prompt-header">
  <span class="prompt-name">${escapeHTML(p.name)}</span>
</div>
<div class="prompt-purpose">${escapeHTML(p.purpose)}</div>
<div class="prompt-text">${escapeHTML(promptText)}</div>
</div>\n`;
    }
  }

  html += `</div>`;
  return html;
}

// ── Utilities ────────────────────────────────────────────────────

function markdownToHTML(md: string): string {
  let html = md;

  // Headings
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");

  // Bold and italic
  html = html.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");

  // Horizontal rules
  html = html.replace(/^---+$/gm, "<hr>");

  // Lists (basic)
  html = html.replace(
    /^(\d+\.\s+.+(?:\n\d+\.\s+.+)*)/gm,
    (match) => {
      const items = match.split("\n").map((line) =>
        `<li>${line.replace(/^\d+\.\s+/, "")}</li>`
      ).join("\n");
      return `<ol>${items}</ol>`;
    }
  );
  html = html.replace(
    /^([-*]\s+.+(?:\n[-*]\s+.+)*)/gm,
    (match) => {
      const items = match.split("\n").map((line) =>
        `<li>${line.replace(/^[-*]\s+/, "")}</li>`
      ).join("\n");
      return `<ul>${items}</ul>`;
    }
  );

  // Paragraphs — wrap lines not already in tags
  html = html
    .split("\n\n")
    .map((block) => {
      block = block.trim();
      if (!block) return "";
      if (block.startsWith("<h") || block.startsWith("<ul") || block.startsWith("<ol") || block.startsWith("<hr")) {
        return block;
      }
      return `<p>${block.replace(/\n/g, " ")}</p>`;
    })
    .join("\n\n");

  return html;
}

function escapeHTML(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function downloadAsHTML(html: string, concept: string) {
  const { title } = formatSynthesisConcept(concept);
  const filename = `${title.replace(/[^a-zA-Z0-9 ]/g, "").replace(/\s+/g, "_")}_synthesis.html`;

  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
