"use client";

import type { ConvergenceResponse } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ConfidenceIndicator } from "@/components/answer/ConfidenceIndicator";
import {
  CheckCircle2,
  AlertTriangle,
  Lightbulb,
  Loader2,
  GitCompareArrows,
} from "lucide-react";

interface ConvergenceTabProps {
  convergence: ConvergenceResponse | null;
  isLoading: boolean;
}

export function ConvergenceTab({
  convergence,
  isLoading,
}: ConvergenceTabProps) {
  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-10 w-10 mb-3 animate-spin opacity-50" />
        <p>Loading convergence data...</p>
      </div>
    );
  }

  if (!convergence) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <GitCompareArrows className="h-10 w-10 mb-3 opacity-50" />
        <p>No convergence data available.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-6 flex items-center justify-center">
          <div className="text-center space-y-2">
            <ConfidenceIndicator
              score={convergence.convergence_score}
              label="Overall Convergence"
            />
            <p className="text-xs text-muted-foreground">
              {convergence.convergence_score >= 0.8
                ? "Strong consensus across models"
                : convergence.convergence_score >= 0.5
                  ? "Moderate agreement across models"
                  : "Significant divergence across models"}
            </p>
          </div>
        </CardContent>
      </Card>

      {convergence.converged_claims.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-green-500" />
              Converged Claims
              <Badge variant="secondary" className="ml-auto">
                {convergence.converged_claims.length}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {convergence.converged_claims.map((claim, index) => (
                <li key={index} className="text-sm flex gap-2">
                  <span className="text-green-500 mt-0.5 shrink-0">*</span>
                  <span>{claim}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {convergence.divergent_claims.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              Divergent Claims
              <Badge variant="secondary" className="ml-auto">
                {convergence.divergent_claims.length}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3">
              {convergence.divergent_claims.map((claim, index) => {
                const model =
                  typeof claim === "object" && claim !== null
                    ? (claim as Record<string, unknown>).model_id
                    : null;
                const content =
                  typeof claim === "object" && claim !== null
                    ? ((claim as Record<string, unknown>).claim as string) ||
                      ((claim as Record<string, unknown>).content as string) ||
                      JSON.stringify(claim)
                    : String(claim);

                return (
                  <li key={index} className="text-sm space-y-1">
                    <div className="flex items-start gap-2">
                      <span className="text-amber-500 mt-0.5 shrink-0">*</span>
                      <span>{content}</span>
                    </div>
                    {typeof model === "string" && (
                      <Badge
                        variant="outline"
                        className="ml-5 text-xs text-muted-foreground"
                      >
                        {model}
                      </Badge>
                    )}
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      )}

      {convergence.recommended_content && (
        <>
          <Separator />
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <Lightbulb className="h-4 w-4 text-blue-500" />
                Recommended Content
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">
                {convergence.recommended_content}
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
