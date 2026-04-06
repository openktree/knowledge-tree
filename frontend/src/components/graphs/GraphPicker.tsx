"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, ChevronsUpDown, Database, Loader2, Settings } from "lucide-react";
import { useGraph } from "@/contexts/graph";
import { useAuth } from "@/contexts/auth";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function GraphPicker({ collapsed }: { collapsed: boolean }) {
  const { activeGraph, graphs, setActiveGraph, loading, activeGraphInfo } =
    useGraph();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);

  if (loading) {
    return (
      <div className="flex items-center justify-center px-2 py-2">
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const pickerContent = (
    <PopoverContent
      className="w-56 p-0"
      side={collapsed ? "right" : "bottom"}
      align="start"
    >
      <Command>
        <CommandInput placeholder="Search graphs..." />
        <CommandList>
          <CommandEmpty>No graphs found.</CommandEmpty>
          <CommandGroup>
            {graphs.map((g) => (
              <CommandItem
                key={g.slug}
                value={g.slug + " " + g.name}
                onSelect={() => {
                  setActiveGraph(g.slug);
                  setOpen(false);
                }}
                className="flex items-center justify-between"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Check
                    className={cn(
                      "size-3.5 shrink-0",
                      g.slug === activeGraph ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <span className="truncate">{g.name}</span>
                </div>
                <Badge variant="secondary" className="ml-2 text-[0.6rem] px-1.5 py-0 shrink-0">
                  {g.node_count}
                </Badge>
              </CommandItem>
            ))}
          </CommandGroup>
          {user?.is_superuser && (
            <>
              <CommandSeparator />
              <CommandGroup>
                <Link href="/graphs" onClick={() => setOpen(false)}>
                  <CommandItem className="gap-2 cursor-pointer">
                    <Settings className="size-3.5" />
                    <span>Manage graphs</span>
                  </CommandItem>
                </Link>
              </CommandGroup>
            </>
          )}
        </CommandList>
      </Command>
    </PopoverContent>
  );

  if (collapsed) {
    return (
      <Popover open={open} onOpenChange={setOpen}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <button
                className="flex items-center justify-center rounded-md px-0 py-2 text-sm transition-colors text-muted-foreground hover:bg-accent/50 hover:text-foreground w-full"
              >
                <Database
                  className={cn(
                    "size-4",
                    activeGraph !== "default" && "text-primary",
                  )}
                />
              </button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="right">
            {activeGraphInfo?.name ?? "Default"}
          </TooltipContent>
        </Tooltip>
        {pickerContent}
      </Popover>
    );
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="mx-2 justify-between text-xs h-8 gap-1"
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <Database className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">
              {activeGraphInfo?.name ?? "Default"}
            </span>
          </div>
          <ChevronsUpDown className="size-3 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      {pickerContent}
    </Popover>
  );
}
