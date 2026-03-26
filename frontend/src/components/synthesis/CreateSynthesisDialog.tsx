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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createSynthesis } from "@/lib/api";

interface CreateSynthesisDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export function CreateSynthesisDialog({
  open,
  onOpenChange,
  onCreated,
}: CreateSynthesisDialogProps) {
  const [topic, setTopic] = useState("");
  const [budget, setBudget] = useState(20);
  const [visibility, setVisibility] = useState("public");
  const [creating, setCreating] = useState(false);

  const handleSubmit = async () => {
    if (!topic.trim()) return;
    setCreating(true);
    try {
      await createSynthesis({
        topic: topic.trim(),
        exploration_budget: budget,
        visibility,
      });
      onOpenChange(false);
      setTopic("");
      onCreated();
    } catch (err) {
      console.error("Failed to create synthesis:", err);
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New Synthesis</DialogTitle>
          <DialogDescription>
            Create a synthesis document by exploring the knowledge graph on a topic.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="topic">Topic</Label>
            <Input
              id="topic"
              placeholder="e.g., Climate change mitigation strategies"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="budget">Exploration Budget (nodes)</Label>
            <Input
              id="budget"
              type="number"
              min={5}
              max={100}
              value={budget}
              onChange={(e) => setBudget(parseInt(e.target.value) || 20)}
            />
          </div>
          <div className="space-y-2">
            <Label>Visibility</Label>
            <Select value={visibility} onValueChange={setVisibility}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="public">Public</SelectItem>
                <SelectItem value="private">Private</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!topic.trim() || creating}>
            {creating && <Loader2 className="mr-2 size-4 animate-spin" />}
            Create Synthesis
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
