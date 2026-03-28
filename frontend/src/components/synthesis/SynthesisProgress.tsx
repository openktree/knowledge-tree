"use client";

import { useState, useEffect } from "react";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getWorkflowProgress } from "@/lib/api";
import type { PipelineTaskItem } from "@/types";

interface SynthesisProgressProps {
  workflowRunId: string;
  onComplete: () => void;
}

type OverallStatus = "running" | "completed" | "failed";

function taskStatusIcon(status: string) {
  const upper = status.toUpperCase();
  if (upper === "SUCCEEDED" || upper === "COMPLETED")
    return <CheckCircle2 className="size-4 text-green-500 shrink-0" />;
  if (upper === "FAILED" || upper === "CANCELLED" || upper === "TIMED_OUT")
    return <XCircle className="size-4 text-red-500 shrink-0" />;
  if (upper === "RUNNING")
    return <Loader2 className="size-4 animate-spin text-blue-500 shrink-0" />;
  return <div className="size-4 rounded-full border-2 border-muted-foreground/30 shrink-0" />;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const secs = ms / 1000;
  if (secs < 60) return `${secs.toFixed(1)}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = Math.round(secs % 60);
  return `${mins}m${remSecs}s`;
}

export function SynthesisProgress({ workflowRunId, onComplete }: SynthesisProgressProps) {
  const [status, setStatus] = useState<OverallStatus>("running");
  const [tasks, setTasks] = useState<PipelineTaskItem[]>([]);

  useEffect(() => {
    let cancelled = false;

    const fetchProgress = async () => {
      try {
        const progress = await getWorkflowProgress(workflowRunId);
        if (cancelled) return;
        setTasks(progress.tasks);

        if (progress.status === "completed") {
          setStatus("completed");
          onComplete();
        } else if (progress.status === "failed") {
          setStatus("failed");
        }
      } catch {
        // Ignore transient errors, keep polling
      }
    };

    fetchProgress();

    if (status === "running") {
      const timer = setInterval(fetchProgress, 3000);
      return () => { cancelled = true; clearInterval(timer); };
    }

    return () => { cancelled = true; };
  }, [workflowRunId, status, onComplete]);

  const completed = tasks.filter(
    (t) => t.status === "SUCCEEDED" || t.status === "COMPLETED"
  ).length;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        {status === "running" ? (
          <Loader2 className="size-5 animate-spin text-blue-500" />
        ) : status === "completed" ? (
          <CheckCircle2 className="size-5 text-green-500" />
        ) : (
          <XCircle className="size-5 text-red-500" />
        )}
        <span className="font-medium">
          {status === "running"
            ? "Synthesizing..."
            : status === "completed"
              ? "Synthesis Complete"
              : "Synthesis Failed"}
        </span>
        {tasks.length > 0 && (
          <Badge variant="outline" className="ml-auto text-xs tabular-nums">
            {completed} / {tasks.length}
          </Badge>
        )}
      </div>

      {tasks.length > 0 && (
        <>
          <div className="w-full bg-muted rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full bg-primary transition-all duration-500 rounded-full"
              style={{ width: `${tasks.length > 0 ? (completed / tasks.length) * 100 : 0}%` }}
            />
          </div>

          <div className="max-h-48 overflow-y-auto space-y-1">
            {tasks.map((task) => (
              <div
                key={task.task_id}
                className="flex items-center gap-2 px-2 py-1.5 text-sm"
              >
                {taskStatusIcon(task.status)}
                <span className="flex-1 truncate">{task.display_name}</span>
                {task.duration_ms != null && (
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {formatDuration(task.duration_ms)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {status === "completed" && (
        <Button variant="outline" className="w-full" onClick={onComplete}>
          Done
        </Button>
      )}
    </div>
  );
}
