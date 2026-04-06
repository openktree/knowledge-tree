"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { CircleDot, ArrowLeftRight, FileText, PanelLeftClose, PanelLeft, TreePine, Upload, Globe, Sprout, GitPullRequestArrow, BarChart3, Users, Settings, Search, ExternalLink, Menu, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet";
import { UserMenu } from "@/components/auth/UserMenu";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { useAuth } from "@/contexts/auth";
import { useIsMobile } from "@/hooks/useIsMobile";
import { cn } from "@/lib/utils";

const WORKFLOW_ITEMS = [
  { href: "/", label: "Home", icon: TreePine },
  { href: "/investigate", label: "Investigate", icon: Search },
  { href: "/grow-graph", label: "Grow Graph", icon: Upload },
] as const;

const DATA_ITEMS = [
  { href: "/nodes", label: "Nodes", icon: CircleDot },
  { href: "/edges", label: "Edges", icon: ArrowLeftRight },
  { href: "/facts", label: "Facts", icon: FileText },
  { href: "/sources", label: "Sources", icon: Globe },
  { href: "/seeds", label: "Seeds", icon: Sprout },
  { href: "/edge-candidates", label: "Candidates", icon: GitPullRequestArrow },
] as const;

const ADMIN_NAV_ITEMS = [
  { href: "/usage", label: "Usage", icon: BarChart3 },
  { href: "/sources/insights", label: "Source Insights", icon: AlertTriangle },
  { href: "/members", label: "Members", icon: Users },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

const SITE_DOMAIN = process.env.NEXT_PUBLIC_SITE_DOMAIN || "openktree.com";

const EXTERNAL_LINKS = [
  { href: `https://${SITE_DOMAIN}`, label: "Home" },
  { href: `https://docs.${SITE_DOMAIN}`, label: "Docs" },
  { href: `https://wiki.${SITE_DOMAIN}`, label: "Wiki" },
];

function NavContent({
  collapsed,
  isAdmin,
  isActive,
  onNavigate,
}: {
  collapsed: boolean;
  isAdmin: boolean;
  isActive: (href: string) => boolean;
  onNavigate?: () => void;
}) {
  return (
    <>
      {/* Navigation */}
      <nav className="flex-1 flex flex-col gap-1 p-2">
        {[...WORKFLOW_ITEMS, ...DATA_ITEMS].map((item, index) => {
          const active = isActive(item.href);
          const showDivider = index === WORKFLOW_ITEMS.length;
          const linkContent = (
            <Link
              href={item.href}
              onClick={onNavigate}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                collapsed && "justify-center px-0",
              )}
            >
              <item.icon className="size-4 shrink-0" />
              {!collapsed && <span>{item.label}</span>}
            </Link>
          );

          const element = collapsed ? (
            <Tooltip key={item.href}>
              <TooltipTrigger asChild>{linkContent}</TooltipTrigger>
              <TooltipContent side="right">{item.label}</TooltipContent>
            </Tooltip>
          ) : (
            <div key={item.href}>{linkContent}</div>
          );

          if (showDivider) {
            return (
              <div key={item.href}>
                <div className={cn("border-t border-border my-1", collapsed && "mx-1")} />
                {element}
              </div>
            );
          }

          return element;
        })}

        {isAdmin && (
          <>
            <div className={cn("border-t border-border my-1", collapsed && "mx-1")} />
            {ADMIN_NAV_ITEMS.map((item) => {
              const active = isActive(item.href);
              const linkContent = (
                <Link
                  href={item.href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                    active
                      ? "bg-accent text-accent-foreground font-medium"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                    collapsed && "justify-center px-0",
                  )}
                >
                  <item.icon className="size-4 shrink-0" />
                  {!collapsed && <span>{item.label}</span>}
                </Link>
              );

              if (collapsed) {
                return (
                  <Tooltip key={item.href}>
                    <TooltipTrigger asChild>{linkContent}</TooltipTrigger>
                    <TooltipContent side="right">{item.label}</TooltipContent>
                  </Tooltip>
                );
              }

              return <div key={item.href}>{linkContent}</div>;
            })}
          </>
        )}
      </nav>

      {/* External links */}
      <div className={cn("border-t border-border p-2 flex flex-col gap-1", collapsed && "px-1")}>
        {EXTERNAL_LINKS.map((item) => {
          const linkContent = (
            <a
              key={item.href}
              href={item.href}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                collapsed && "justify-center px-0",
              )}
            >
              <ExternalLink className="size-4 shrink-0" />
              {!collapsed && <span>{item.label}</span>}
            </a>
          );

          if (collapsed) {
            return (
              <Tooltip key={item.href}>
                <TooltipTrigger asChild>{linkContent}</TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            );
          }

          return <div key={item.href}>{linkContent}</div>;
        })}
      </div>
    </>
  );
}

export function SidebarLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();
  const { user } = useAuth();
  const isAdmin = user?.is_superuser ?? false;
  const isMobile = useIsMobile();

  const isActive = (href: string) => {
    if (href === "/") return pathname === "/";
    return pathname.startsWith(href);
  };

  if (isMobile) {
    return (
      <div className="flex flex-col h-screen">
        {/* Mobile top bar */}
        <div className="flex items-center gap-2 px-3 py-2 border-b bg-card shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setMobileOpen(true)}
            className="px-2"
          >
            <Menu className="size-5" />
          </Button>
          <Image src="/logo.svg" alt="Knowledge Tree" width={20} height={20} className="size-5 shrink-0" />
          <span className="text-sm font-semibold truncate">Knowledge Tree</span>
        </div>

        {/* Mobile drawer */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetContent side="left" className="w-[260px] p-0 flex flex-col">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            {/* Logo */}
            <div className="flex items-center gap-2 px-3 py-4 border-b">
              <Image src="/logo.svg" alt="Knowledge Tree" width={20} height={20} className="size-5 shrink-0" />
              <span className="text-sm font-semibold truncate">Knowledge Tree</span>
            </div>

            <NavContent
              collapsed={false}
              isAdmin={isAdmin}
              isActive={isActive}
              onNavigate={() => setMobileOpen(false)}
            />

            {/* User menu + theme */}
            <div className="border-t p-2 flex flex-col gap-1">
              <ThemeToggle collapsed={false} />
              <UserMenu collapsed={false} />
            </div>
          </SheetContent>
        </Sheet>

        {/* Main content */}
        <main className="flex-1 min-w-0 overflow-auto">{children}</main>
      </div>
    );
  }

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside
        className={cn(
          "flex flex-col border-r bg-card transition-[width] duration-200 shrink-0",
          collapsed ? "w-[52px]" : "w-[208px]",
        )}
      >
        {/* Logo */}
        <div className="flex items-center gap-2 px-3 py-4 border-b">
          <Image src="/logo.svg" alt="Knowledge Tree" width={20} height={20} className="size-5 shrink-0" />
          {!collapsed && (
            <span className="text-sm font-semibold truncate">Knowledge Tree</span>
          )}
        </div>

        <NavContent
          collapsed={collapsed}
          isAdmin={isAdmin}
          isActive={isActive}
        />

        {/* User menu + theme + collapse toggle */}
        <div className="border-t p-2 flex flex-col gap-1">
          <ThemeToggle collapsed={collapsed} />
          <UserMenu collapsed={collapsed} />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setCollapsed(!collapsed)}
            className={cn("w-full", collapsed && "px-0")}
          >
            {collapsed ? (
              <PanelLeft className="size-4" />
            ) : (
              <>
                <PanelLeftClose className="size-4 mr-2" />
                <span className="text-xs">Collapse</span>
              </>
            )}
          </Button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 overflow-auto">{children}</main>
    </div>
  );
}
