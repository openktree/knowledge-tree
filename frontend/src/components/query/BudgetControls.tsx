"use client";

import { useCallback } from "react";
import { Compass, FlaskConical } from "lucide-react";
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

interface BudgetControlsProps {
  navBudget: number;
  onNavBudgetChange: (n: number) => void;
  exploreBudget: number;
  onExploreBudgetChange: (n: number) => void;
  hideNav?: boolean;
}

interface Preset {
  label: string;
  nav: number;
  explore: number;
}

interface PresetGroup {
  label: string;
  presets: Preset[];
}

const PRESET_GROUPS: PresetGroup[] = [
  {
    label: "Local",
    presets: [
      { label: "Zero", nav: 0, explore: 0 },
      { label: "Light", nav: 50, explore: 0 },
      { label: "Moderate", nav: 250, explore: 0 },
      { label: "Heavy", nav: 750, explore: 0 },
    ],
  },
  {
    label: "Exploration",
    presets: [
      { label: "Tiny", nav: 50, explore: 5 },
      { label: "Small", nav: 200, explore: 20 },
      { label: "Medium", nav: 500, explore: 50 },
      { label: "Deep", nav: 1500, explore: 150 },
    ],
  },
];

/** Explore-only presets for bottom-up mode (nav is automatic). */
const EXPLORE_PRESETS: Preset[] = [
  { label: "Tiny", nav: 0, explore: 5 },
  { label: "Small", nav: 0, explore: 20 },
  { label: "Medium", nav: 0, explore: 50 },
  { label: "Deep", nav: 0, explore: 150 },
];

export function BudgetControls({
  navBudget,
  onNavBudgetChange,
  exploreBudget,
  onExploreBudgetChange,
  hideNav = false,
}: BudgetControlsProps) {
  const applyPreset = useCallback(
    (preset: Preset) => {
      onNavBudgetChange(preset.nav);
      onExploreBudgetChange(preset.explore);
    },
    [onNavBudgetChange, onExploreBudgetChange],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Budget Controls</CardTitle>
        <CardDescription>
          Control how much the system reads and explores to answer your query.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Preset buttons */}
        {hideNav ? (
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">
              Depth
            </span>
            <div className="flex flex-wrap gap-2">
              {EXPLORE_PRESETS.map((preset) => {
                const isActive = exploreBudget === preset.explore;
                return (
                  <Button
                    key={preset.label}
                    variant={isActive ? "default" : "outline"}
                    size="sm"
                    onClick={() => applyPreset(preset)}
                  >
                    {preset.label}
                    <span className="ml-1 text-xs opacity-70">
                      ({preset.explore})
                    </span>
                  </Button>
                );
              })}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {PRESET_GROUPS.map((group) => (
              <div key={group.label} className="space-y-1.5">
                <span className="text-xs font-medium text-muted-foreground">
                  {group.label}
                </span>
                <div className="flex flex-wrap gap-2">
                  {group.presets.map((preset) => {
                    const isActive =
                      navBudget === preset.nav &&
                      exploreBudget === preset.explore;
                    return (
                      <Button
                        key={preset.label}
                        variant={isActive ? "default" : "outline"}
                        size="sm"
                        onClick={() => applyPreset(preset)}
                      >
                        {preset.label}
                        <span className="ml-1 text-xs opacity-70">
                          ({preset.nav},{preset.explore})
                        </span>
                      </Button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Navigation budget input */}
        {!hideNav && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex cursor-help items-center gap-2 text-sm font-medium">
                    <Compass className="size-4 text-muted-foreground" />
                    Navigation Budget
                  </div>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-xs">
                  How many existing nodes the agent can read from the knowledge
                  graph. These are cheap database reads -- no API calls involved.
                </TooltipContent>
              </Tooltip>
              <Input
                type="number"
                min={0}
                value={navBudget}
                onChange={(e) => onNavBudgetChange(Math.max(0, Number(e.target.value) || 0))}
                className="w-24 text-right"
              />
            </div>
          </div>
        )}

        {/* Exploration budget input */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex cursor-help items-center gap-2 text-sm font-medium">
                  <FlaskConical className="size-4 text-muted-foreground" />
                  Exploration Budget
                </div>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs">
                How many new nodes can be created or expanded. Each unit
                triggers a Brave Search API call and model inference -- this is
                the main cost driver.
              </TooltipContent>
            </Tooltip>
            <Input
              type="number"
              min={0}
              value={exploreBudget}
              onChange={(e) => onExploreBudgetChange(Math.max(0, Number(e.target.value) || 0))}
              className="w-24 text-right"
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
