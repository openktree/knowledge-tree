"use client";

import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useMounted } from "@/hooks/useMounted";

interface ThemeToggleProps {
  collapsed?: boolean;
}

export function ThemeToggle({ collapsed = false }: ThemeToggleProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useMounted();

  if (!mounted) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className={cn("w-full", collapsed && "px-0")}
        disabled
      >
        <Sun className="size-4" />
        {!collapsed && <span className="text-xs ml-2">Theme</span>}
      </Button>
    );
  }

  const isDark = resolvedTheme === "dark";

  const toggle = () => setTheme(isDark ? "light" : "dark");

  const button = (
    <Button
      variant="ghost"
      size="sm"
      onClick={toggle}
      className={cn("w-full", collapsed && "px-0")}
    >
      {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
      {!collapsed && (
        <span className="text-xs ml-2">{isDark ? "Light mode" : "Dark mode"}</span>
      )}
    </Button>
  );

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{button}</TooltipTrigger>
        <TooltipContent side="right">
          {isDark ? "Light mode" : "Dark mode"}
        </TooltipContent>
      </Tooltip>
    );
  }

  return button;
}
