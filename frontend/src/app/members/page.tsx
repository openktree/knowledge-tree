"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/auth";
import type { MemberResponse } from "@/types";

export default function MembersPage() {
  const { user } = useAuth();
  const [members, setMembers] = useState<MemberResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState<string | null>(null);

  const fetchMembers = useCallback(async () => {
    try {
      const data = await api.members.list();
      setMembers(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMembers();
  }, [fetchMembers]);

  const toggleRole = async (member: MemberResponse) => {
    if (member.id === user?.id) return;
    const confirmed = window.confirm(
      member.is_superuser
        ? `Demote ${member.email} from admin?`
        : `Promote ${member.email} to admin?`,
    );
    if (!confirmed) return;

    setUpdating(member.id);
    try {
      const updated = await api.members.updateRole(member.id, {
        is_superuser: !member.is_superuser,
      });
      setMembers((prev) =>
        prev.map((m) => (m.id === updated.id ? updated : m)),
      );
    } catch {
      // ignore
    } finally {
      setUpdating(null);
    }
  };

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-sm text-muted-foreground">Loading members...</p>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <h1 className="text-xl font-semibold mb-6">Members</h1>

      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50">
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Email</th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Name</th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Role</th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">BYOK</th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Joined</th>
              <th className="text-right px-4 py-3 font-medium text-muted-foreground">Actions</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3 break-all">{m.email}</td>
                <td className="px-4 py-3">{m.display_name ?? "\u2014"}</td>
                <td className="px-4 py-3">
                  <Badge variant={m.is_superuser ? "default" : "secondary"}>
                    {m.is_superuser ? "Admin" : "User"}
                  </Badge>
                </td>
                <td className="px-4 py-3">
                  {m.has_byok ? (
                    <span className="text-green-600 text-xs">Active</span>
                  ) : (
                    <span className="text-muted-foreground text-xs">\u2014</span>
                  )}
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {new Date(m.created_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-3 text-right">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={m.id === user?.id || updating === m.id}
                    onClick={() => toggleRole(m)}
                  >
                    {updating === m.id
                      ? "..."
                      : m.is_superuser
                        ? "Demote"
                        : "Promote"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
