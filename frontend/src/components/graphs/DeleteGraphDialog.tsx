"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { deleteGraph } from "@/lib/api";
import type { GraphResponse } from "@/types";

interface DeleteGraphDialogProps {
  graph: GraphResponse | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted: () => void;
}

export function DeleteGraphDialog({
  graph,
  open,
  onOpenChange,
  onDeleted,
}: DeleteGraphDialogProps) {
  const [confirmSlug, setConfirmSlug] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {
    if (!graph || confirmSlug !== graph.slug) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteGraph(graph.slug);
      setConfirmSlug("");
      onOpenChange(false);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete graph");
    } finally {
      setDeleting(false);
    }
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setConfirmSlug("");
      setError(null);
    }
    onOpenChange(next);
  };

  if (!graph) return null;

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete graph &ldquo;{graph.name}&rdquo;</AlertDialogTitle>
          <AlertDialogDescription>
            This action cannot be undone. All data in this graph will be
            permanently deleted. Type <strong>{graph.slug}</strong> to confirm.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2 py-2">
          <Label htmlFor="confirm-slug">Graph slug</Label>
          <Input
            id="confirm-slug"
            value={confirmSlug}
            onChange={(e) => setConfirmSlug(e.target.value)}
            placeholder={graph.slug}
            autoComplete="off"
          />
          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={confirmSlug !== graph.slug || deleting}
          >
            {deleting && <Loader2 className="mr-2 size-4 animate-spin" />}
            Delete graph
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
