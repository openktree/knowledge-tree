"use client";

import type { FactResponse } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Link2, FileSearch } from "lucide-react";

interface SourcesTabProps {
  facts: FactResponse[];
}

export function SourcesTab({ facts }: SourcesTabProps) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Link2 className="h-4 w-4" />
            Provenance Tracking
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{facts.length}</Badge>
            <span className="text-sm text-muted-foreground">
              fact{facts.length !== 1 ? "s" : ""} linked to this node
            </span>
          </div>

          <p className="text-sm text-muted-foreground leading-relaxed">
            Sources are tracked through the fact provenance chain. Each fact is
            decomposed from raw sources retrieved by knowledge providers. View
            the Facts tab to see the individual facts and their origins.
          </p>

          <div className="rounded-md border p-3 bg-muted/50">
            <div className="flex items-start gap-2">
              <FileSearch className="h-4 w-4 mt-0.5 text-muted-foreground shrink-0" />
              <div className="text-xs text-muted-foreground space-y-1">
                <p className="font-medium">Provenance chain:</p>
                <p>Node &rarr; Fact &rarr; RawSource</p>
                <p>
                  Facts accumulate sources over time through deduplication. A
                  single fact may be backed by multiple independent sources,
                  strengthening its reliability.
                </p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
