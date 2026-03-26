"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus, FileText, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { listSyntheses } from "@/lib/api";
import { CreateSynthesisDialog } from "@/components/synthesis/CreateSynthesisDialog";
import type { SynthesisListItem } from "@/types";
import { formatSynthesisConcept } from "@/components/synthesis/utils";

export default function SynthesesPage() {
  const [items, setItems] = useState<SynthesisListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);

  const fetchSyntheses = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listSyntheses(0, 50);
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      console.error("Failed to load syntheses:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSyntheses();
  }, [fetchSyntheses]);

  return (
    <div className="mx-auto max-w-4xl py-8 px-4 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Syntheses</h1>
          <p className="text-sm text-muted-foreground">
            {total} synthesis document{total !== 1 ? "s" : ""}
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="mr-2 size-4" />
          New Synthesis
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 text-center">
            <FileText className="size-12 text-muted-foreground mb-4" />
            <h3 className="font-medium mb-1">No syntheses yet</h3>
            <p className="text-sm text-muted-foreground mb-4">
              Create a synthesis to explore and analyze your knowledge graph.
            </p>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="mr-2 size-4" />
              Create First Synthesis
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <a key={item.id} href={`/syntheses/${item.id}`}>
              <Card className="hover:bg-accent/50 transition-colors cursor-pointer">
                <CardHeader className="py-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <CardTitle className="text-base">
                        {formatSynthesisConcept(item.concept).title}
                      </CardTitle>
                      <Badge variant="outline" className="text-[10px]">
                        {item.node_type === "supersynthesis"
                          ? "Super"
                          : "Synthesis"}
                      </Badge>
                      <Badge
                        variant={
                          item.visibility === "public"
                            ? "default"
                            : "secondary"
                        }
                        className="text-[10px]"
                      >
                        {item.visibility}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span>{item.sentence_count} sentences</span>
                      {item.created_at && (
                        <span>
                          {new Date(item.created_at).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                  </div>
                </CardHeader>
              </Card>
            </a>
          ))}
        </div>
      )}

      <CreateSynthesisDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={fetchSyntheses}
      />
    </div>
  );
}
