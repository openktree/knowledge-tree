"use client";

import { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  BarChart2,
  ExternalLink,
  ChevronRight,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, getTaskChildren } from "@/lib/api";
import type {
  BottomUpProposedNode,
  PipelineTaskItem,
  ResearchReportResponse,
} from "@/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ResearchBuildProgressProps {
  conversationId: string;
  messageId: string;
  /** When provided (active build), shows these as the initial node list.
   *  When absent (historical view), derives the list from Hatchet tasks. */
  selectedNodes?: BottomUpProposedNode[];
  /** Initial status — set to "completed" when viewing a finished build. */
  initialStatus?: "running" | "completed" | "failed";
  onComplete?: () => void;
}

type NodeStatus = "pending" | "running" | "completed" | "failed";

interface NodeBuildState {
  name: string;
  nodeType: string;
  status: NodeStatus;
  durationMs: number | null;
  taskItem: PipelineTaskItem | null;
}

const NODE_TYPE_COLORS: Record<string, string> = {
  concept: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  entity: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  event: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
  location: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900 dark:text-cyan-200",
  perspective: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
};

// Known top-level workflow tasks to exclude from the node list
const EXCLUDED_TASK_NAMES = new Set([
  "bottom_up_prepare", "bottom_up_prepare_scope",
  "orchestrate", "synthesize", "resynthesize",
]);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function taskStatusToNode(s: string): NodeStatus {
  const upper = s.toUpperCase();
  if (upper === "SUCCEEDED" || upper === "COMPLETED") return "completed";
  if (upper === "FAILED" || upper === "CANCELLED" || upper === "TIMED_OUT") return "failed";
  if (upper === "RUNNING") return "running";
  return "pending";
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const secs = ms / 1000;
  if (secs < 60) return `${secs.toFixed(1)}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = Math.round(secs % 60);
  return `${mins}m${remSecs}s`;
}

function statusIcon(status: NodeStatus) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="size-4 text-green-500 shrink-0" />;
    case "failed":
      return <XCircle className="size-4 text-red-500 shrink-0" />;
    case "running":
      return <Loader2 className="size-4 animate-spin text-blue-500 shrink-0" />;
    default:
      return <div className="size-4 rounded-full border-2 border-muted-foreground/30 shrink-0" />;
  }
}

// ---------------------------------------------------------------------------
// TaskRow — recursive, lazy-loaded
// ---------------------------------------------------------------------------

interface TaskRowProps {
  task: PipelineTaskItem;
  depth: number;
  workflowRunId: string | null;
  nodeType?: string;
}

function TaskRow({ task, depth, workflowRunId, nodeType }: TaskRowProps) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<PipelineTaskItem[]>(task.children || []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  const hasChildren = task.has_children || children.length > 0;
  const status = taskStatusToNode(task.status);

  const toggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && children.length === 0 && task.has_children && workflowRunId && !loading) {
      setLoading(true);
      setError(false);
      try {
        const res = await getTaskChildren(workflowRunId, task.task_id);
        setChildren(res.tasks);
      } catch {
        setError(true);
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <>
      <div
        className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/30 cursor-default"
        style={{ paddingLeft: depth * 16 + 16 }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={toggle}
            className="shrink-0 p-0.5 -ml-1 rounded hover:bg-muted"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            <ChevronRight
              className={`size-3.5 transition-transform ${expanded ? "rotate-90" : ""}`}
            />
          </button>
        ) : (
          <div className="size-3.5 shrink-0" />
        )}
        {statusIcon(status)}
        <span className="flex-1 text-sm truncate">{task.display_name}</span>
        {depth === 0 && nodeType && (
          <Badge
            variant="secondary"
            className={`text-[10px] shrink-0 ${NODE_TYPE_COLORS[nodeType] || ""}`}
          >
            {nodeType}
          </Badge>
        )}
        {hasChildren && children.length > 0 && (
          <Badge variant="outline" className="text-[10px] shrink-0 tabular-nums">
            {children.length}
          </Badge>
        )}
        {task.duration_ms != null && (
          <span className="text-xs tabular-nums text-muted-foreground">
            {formatDuration(task.duration_ms)}
          </span>
        )}
      </div>
      {expanded && (
        <>
          {loading && (
            <div
              className="flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground"
              style={{ paddingLeft: (depth + 1) * 16 + 16 }}
            >
              <Loader2 className="size-3 animate-spin" /> Loading subtasks…
            </div>
          )}
          {error && (
            <div
              className="px-4 py-2 text-xs text-red-500"
              style={{ paddingLeft: (depth + 1) * 16 + 16 }}
            >
              Failed to load subtasks
            </div>
          )}
          {children.map((child) => (
            <TaskRow
              key={child.task_id}
              task={child}
              depth={depth + 1}
              workflowRunId={workflowRunId}
            />
          ))}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ResearchBuildProgress({
  conversationId,
  messageId,
  selectedNodes,
  initialStatus = "running",
  onComplete,
}: ResearchBuildProgressProps) {
  const [overallStatus, setOverallStatus] = useState<"running" | "completed" | "failed">(initialStatus);
  const [tasks, setTasks] = useState<PipelineTaskItem[]>([]);
  const [workflowRunId, setWorkflowRunId] = useState<string | null>(null);
  const [report, setReport] = useState<ResearchReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Poll progress (active builds) or fetch once (historical views)
  useEffect(() => {
    let cancelled = false;

    const fetchProgress = async () => {
      try {
        const progress = await api.conversations.getProgress(conversationId, messageId);
        if (cancelled) return;
        setTasks(progress.tasks);
        setWorkflowRunId(progress.workflow_run_id);
        setIsLoading(false);

        if (progress.status === "completed") {
          setOverallStatus("completed");
          try {
            const r = await api.conversations.getMessageReport(conversationId, messageId);
            if (!cancelled) setReport(r);
          } catch { /* ignore */ }
          onComplete?.();
        } else if (progress.status === "failed") {
          setOverallStatus("failed");
        }
      } catch {
        if (!cancelled) setIsLoading(false);
      }
    };

    fetchProgress();

    // Only poll if still running
    if (overallStatus === "running") {
      const timer = setInterval(fetchProgress, 3000);
      return () => { cancelled = true; clearInterval(timer); };
    }

    return () => { cancelled = true; };
  }, [conversationId, messageId, overallStatus, initialStatus, onComplete]);

  // Fetch report on mount for completed builds
  useEffect(() => {
    if (initialStatus !== "completed") return;
    let cancelled = false;
    api.conversations
      .getMessageReport(conversationId, messageId)
      .then((r) => { if (!cancelled) setReport(r); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [conversationId, messageId, initialStatus]);

  // Derive node list: prefer selectedNodes (active build), fallback to tasks
  const { coreNodes, perspectiveNodes, completedCount, totalCount } = useMemo(() => {
    if (selectedNodes && selectedNodes.length > 0) {
      // Active build — match selectedNodes against tasks
      const taskByName = new Map<string, PipelineTaskItem>();
      for (const t of tasks) {
        taskByName.set(t.display_name.toLowerCase(), t);
      }

      const core: NodeBuildState[] = selectedNodes.map((node) => {
        const task = taskByName.get(node.name.toLowerCase());
        return {
          name: node.name,
          nodeType: node.node_type,
          status: task ? taskStatusToNode(task.status) : "pending",
          durationMs: task?.duration_ms ?? null,
          taskItem: task ?? null,
        };
      });

      const selectedNames = new Set(selectedNodes.map((n) => n.name.toLowerCase()));
      const persp: NodeBuildState[] = tasks
        .filter((t) => !selectedNames.has(t.display_name.toLowerCase()) &&
          !EXCLUDED_TASK_NAMES.has(t.display_name))
        .map((t) => ({
          name: t.display_name,
          nodeType: "perspective",
          status: taskStatusToNode(t.status),
          durationMs: t.duration_ms ?? null,
          taskItem: t,
        }));

      const done = core.filter((n) => n.status === "completed").length;
      return { coreNodes: core, perspectiveNodes: persp, completedCount: done, totalCount: core.length };
    }

    // Historical view — derive everything from tasks, using node_type from API
    const nonExcluded = tasks.filter((t) => !EXCLUDED_TASK_NAMES.has(t.display_name));
    const core: NodeBuildState[] = [];
    const persp: NodeBuildState[] = [];
    for (const t of nonExcluded) {
      const nodeType = t.node_type || "node";
      const entry: NodeBuildState = {
        name: t.display_name,
        nodeType,
        status: taskStatusToNode(t.status),
        durationMs: t.duration_ms ?? null,
        taskItem: t,
      };
      if (nodeType === "perspective" || nodeType === "synthesis") {
        persp.push(entry);
      } else {
        core.push(entry);
      }
    }

    const done = core.filter((n) => n.status === "completed").length;
    return { coreNodes: core, perspectiveNodes: persp, completedCount: done, totalCount: core.length };
  }, [selectedNodes, tasks]);

  if (isLoading && !selectedNodes?.length) {
    return (
      <div className="max-w-2xl mx-auto text-center py-12">
        <Loader2 className="size-8 mx-auto animate-spin text-primary mb-4" />
        <p className="font-medium">Loading build progress...</p>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Overall status header */}
      <div className="border rounded-lg p-4 space-y-3 bg-muted/30">
        <div className="flex items-center gap-3">
          {overallStatus === "running" ? (
            <Loader2 className="size-5 animate-spin text-blue-500" />
          ) : overallStatus === "completed" ? (
            <CheckCircle2 className="size-5 text-green-500" />
          ) : (
            <XCircle className="size-5 text-red-500" />
          )}
          <h3 className="text-lg font-semibold">
            {overallStatus === "running"
              ? "Building Nodes..."
              : overallStatus === "completed"
                ? "Build Complete"
                : "Build Failed"}
          </h3>
          <div className="flex-1" />
          {totalCount > 0 && (
            <Badge variant="outline" className="text-xs tabular-nums">
              {completedCount} / {totalCount}
            </Badge>
          )}
        </div>

        {totalCount > 0 && (
          <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
            <div
              className="h-full bg-primary transition-all duration-500 rounded-full"
              style={{ width: `${(completedCount / totalCount) * 100}%` }}
            />
          </div>
        )}
      </div>

      {/* Research report */}
      {report && (
        <div className="border rounded-lg p-4 space-y-2 bg-muted/30">
          <div className="flex items-center gap-1.5">
            <BarChart2 className="size-3.5 text-muted-foreground" />
            <span className="text-sm font-medium">Research Summary</span>
          </div>
          <div className="flex flex-wrap gap-4 text-sm">
            <span className="tabular-nums">
              <span className="font-semibold">{report.nodes_created}</span>
              <span className="text-muted-foreground"> nodes created</span>
            </span>
            <span className="tabular-nums">
              <span className="font-semibold">{report.edges_created}</span>
              <span className="text-muted-foreground"> edges created</span>
            </span>
          </div>
          {report.scope_summaries.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {report.scope_summaries[0]}
            </p>
          )}
          {report.super_sources && report.super_sources.length > 0 && (
            <div className="mt-2 border border-amber-200 dark:border-amber-800 rounded-md p-3 bg-amber-50/50 dark:bg-amber-950/30">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-400 mb-1.5">
                {report.super_sources.length} large source{report.super_sources.length > 1 ? "s" : ""} deferred
              </p>
              <ul className="space-y-1">
                {report.super_sources.map((ss) => (
                  <li key={ss.raw_source_id} className="text-xs text-muted-foreground flex items-start gap-1.5">
                    <ExternalLink className="size-3 mt-0.5 shrink-0" />
                    <span className="break-all">
                      <a
                        href={ss.uri}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline hover:text-foreground"
                      >
                        {ss.title || ss.uri}
                      </a>
                      <span className="text-muted-foreground/70 ml-1">
                        (~{Math.round(ss.estimated_tokens / 1000)}k tokens)
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="text-[10px] text-muted-foreground/60 mt-1.5">
                Use the ingest feature to process these sources manually.
              </p>
            </div>
          )}
        </div>
      )}

      {/* Node list */}
      {coreNodes.length > 0 && (
        <div className="border rounded-lg overflow-hidden">
          <div className="px-4 py-2.5 bg-muted/30 border-b">
            <h4 className="text-sm font-medium">Nodes</h4>
          </div>
          <div className="divide-y">
            {coreNodes.map((node, idx) =>
              node.taskItem ? (
                <TaskRow
                  key={node.taskItem.task_id}
                  task={node.taskItem}
                  depth={0}
                  workflowRunId={workflowRunId}
                  nodeType={node.nodeType}
                />
              ) : (
                <div key={idx} className="flex items-center gap-3 px-4 py-3">
                  {statusIcon(node.status)}
                  <span className="flex-1 text-sm font-medium truncate">
                    {node.name}
                  </span>
                  <Badge
                    variant="secondary"
                    className={`text-[10px] shrink-0 ${NODE_TYPE_COLORS[node.nodeType] || ""}`}
                  >
                    {node.nodeType}
                  </Badge>
                  {node.durationMs != null && (
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {formatDuration(node.durationMs)}
                    </span>
                  )}
                </div>
              )
            )}
          </div>
        </div>
      )}

      {/* Perspective tasks */}
      {perspectiveNodes.length > 0 && (
        <div className="border rounded-lg overflow-hidden">
          <div className="px-4 py-2.5 bg-muted/30 border-b">
            <h4 className="text-sm font-medium">Perspectives</h4>
          </div>
          <div className="divide-y">
            {perspectiveNodes.map((node, idx) =>
              node.taskItem ? (
                <TaskRow
                  key={node.taskItem.task_id}
                  task={node.taskItem}
                  depth={0}
                  workflowRunId={workflowRunId}
                  nodeType="perspective"
                />
              ) : (
                <div key={idx} className="flex items-center gap-3 px-4 py-2.5">
                  {statusIcon(node.status)}
                  <span className="flex-1 text-sm truncate">{node.name}</span>
                  <Badge
                    variant="secondary"
                    className={`text-[10px] shrink-0 ${NODE_TYPE_COLORS.perspective}`}
                  >
                    perspective
                  </Badge>
                  {node.durationMs != null && (
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {formatDuration(node.durationMs)}
                    </span>
                  )}
                </div>
              )
            )}
          </div>
        </div>
      )}

      {/* Link to nodes */}
      {overallStatus === "completed" && (
        <Button variant="outline" className="w-full gap-2" asChild>
          <Link href="/nodes">
            <ExternalLink className="size-4" />
            Browse Nodes
          </Link>
        </Button>
      )}
    </div>
  );
}
