"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface DeleteConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  itemId: string;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

export function DeleteConfirmDialog({
  open,
  title,
  description,
  itemId,
  onConfirm,
  onCancel,
}: DeleteConfirmDialogProps) {
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async () => {
    setIsDeleting(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
      setIsDeleting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <p className="text-xs text-muted-foreground font-mono truncate">
          ID: {itemId}
        </p>
        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={isDeleting}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={isDeleting}
          >
            {isDeleting && <Loader2 className="mr-2 size-4 animate-spin" />}
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
