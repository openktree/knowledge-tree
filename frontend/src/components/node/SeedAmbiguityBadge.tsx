"use client";

import { GitBranch } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { NodeResponse } from "@/types";

interface SeedAmbiguity {
  is_disambiguated: boolean;
  ambiguity_type: string | null;
  parent_name: string | null;
  sibling_names: string[];
}

interface SeedAmbiguityBadgeProps {
  node: NodeResponse;
}

export function SeedAmbiguityBadge({ node }: SeedAmbiguityBadgeProps) {
  const ambiguity = node.metadata?.seed_ambiguity as SeedAmbiguity | undefined;
  if (!ambiguity?.is_disambiguated) return null;

  const tooltip = [
    ambiguity.parent_name
      ? `Disambiguated from '${ambiguity.parent_name}'`
      : "Disambiguated seed",
    ambiguity.sibling_names.length > 0
      ? `Also: ${ambiguity.sibling_names.join(", ")}`
      : null,
  ]
    .filter(Boolean)
    .join(" — ");

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge className="bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200 gap-1">
            <GitBranch className="h-3 w-3" />
            Disambiguated
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <p className="text-xs max-w-64">{tooltip}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
