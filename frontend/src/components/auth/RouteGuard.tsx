"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SidebarLayout } from "@/components/layout/Sidebar";
import { useAuth } from "@/contexts/auth";

const PUBLIC_PATHS = ["/login", "/register", "/auth/callback"];
const ADMIN_PATHS = ["/usage", "/members", "/settings"];

export function RouteGuard({ children }: { children: React.ReactNode }) {
  const { user, token, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
  const isAdminPath = ADMIN_PATHS.some((p) => pathname.startsWith(p));

  useEffect(() => {
    if (loading) return;
    if (!token && !isPublic) {
      router.replace("/login");
      return;
    }
    if (isAdminPath && user && !user.is_superuser) {
      router.replace("/");
    }
  }, [token, loading, isPublic, isAdminPath, user, router]);

  // Public routes render immediately — no loading gate, no sidebar
  if (isPublic) {
    return <>{children}</>;
  }

  if (loading) {
    return (
      <TooltipProvider>
        <SidebarLayout>
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-muted-foreground">Loading…</p>
          </div>
        </SidebarLayout>
      </TooltipProvider>
    );
  }

  return (
    <TooltipProvider>
      <SidebarLayout>{children}</SidebarLayout>
    </TooltipProvider>
  );
}
