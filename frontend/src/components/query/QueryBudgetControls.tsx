"use client";

import { useCallback } from "react";
import { Compass } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";

interface QueryBudgetControlsProps {
  navBudget: number;
  onNavBudgetChange: (n: number) => void;
}

interface Preset {
  label: string;
  nav: number;
}

const PRESETS: Preset[] = [
  { label: "Light", nav: 20 },
  { label: "Standard", nav: 50 },
  { label: "Thorough", nav: 100 },
  { label: "Deep", nav: 200 },
];

export function QueryBudgetControls({
  navBudget,
  onNavBudgetChange,
}: QueryBudgetControlsProps) {
  const applyPreset = useCallback(
    (preset: Preset) => {
      onNavBudgetChange(preset.nav);
    },
    [onNavBudgetChange],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Navigation Budget</CardTitle>
        <CardDescription>
          How many nodes the agent can read from the existing graph. No external
          API calls — fast and free.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Preset buttons */}
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((preset) => {
            const isActive = navBudget === preset.nav;
            return (
              <Button
                key={preset.label}
                variant={isActive ? "default" : "outline"}
                size="sm"
                onClick={() => applyPreset(preset)}
              >
                {preset.label}
                <span className="ml-1 text-xs opacity-70">({preset.nav})</span>
              </Button>
            );
          })}
        </div>

        {/* Navigation budget input */}
        <div className="flex items-center justify-between">
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex cursor-help items-center gap-2 text-sm font-medium">
                <Compass className="size-4 text-muted-foreground" />
                Custom
              </div>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              Number of existing nodes the agent can read. Each read costs 1
              unit. Already-visited nodes are free.
            </TooltipContent>
          </Tooltip>
          <Input
            type="number"
            min={1}
            value={navBudget}
            onChange={(e) =>
              onNavBudgetChange(Math.max(1, Number(e.target.value) || 1))
            }
            className="w-24 text-right"
          />
        </div>
      </CardContent>
    </Card>
  );
}
