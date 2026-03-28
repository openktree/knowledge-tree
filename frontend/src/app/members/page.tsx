"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/contexts/auth";
import type {
  MemberResponse,
  WaitlistEntryResponse,
  InviteResponse,
} from "@/types";

export default function MembersPage() {
  const { user } = useAuth();
  const [members, setMembers] = useState<MemberResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState<string | null>(null);

  // Waitlist state
  const [waitlist, setWaitlist] = useState<WaitlistEntryResponse[]>([]);
  const [waitlistLoading, setWaitlistLoading] = useState(true);
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [inviteCode, setInviteCode] = useState<string | null>(null);

  // Invites state
  const [invites, setInvites] = useState<InviteResponse[]>([]);
  const [invitesLoading, setInvitesLoading] = useState(true);
  const [creatingInvite, setCreatingInvite] = useState(false);
  const [newInviteEmail, setNewInviteEmail] = useState("");
  const [newInviteDays, setNewInviteDays] = useState(7);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

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

  const fetchWaitlist = useCallback(async () => {
    try {
      const data = await api.waitlist.list();
      setWaitlist(data);
    } catch {
      // ignore
    } finally {
      setWaitlistLoading(false);
    }
  }, []);

  const fetchInvites = useCallback(async () => {
    try {
      const data = await api.invites.list();
      setInvites(data);
    } catch {
      // ignore
    } finally {
      setInvitesLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMembers();
    fetchWaitlist();
    fetchInvites();
  }, [fetchMembers, fetchWaitlist, fetchInvites]);

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

  const handleReview = async (entryId: string, status: "approved" | "rejected") => {
    setReviewingId(entryId);
    setInviteCode(null);
    try {
      const result = await api.waitlist.review(entryId, { status });
      setWaitlist((prev) =>
        prev.map((e) => (e.id === entryId ? result.entry : e)),
      );
      if (result.invite) {
        setInviteCode(result.invite.code);
        fetchInvites();
      }
    } catch {
      // ignore
    } finally {
      setReviewingId(null);
    }
  };

  const handleCreateInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreatingInvite(true);
    try {
      await api.invites.create({
        email: newInviteEmail,
        expires_in_days: newInviteDays,
      });
      setNewInviteEmail("");
      setNewInviteDays(7);
      setShowCreateForm(false);
      fetchInvites();
    } catch {
      // ignore
    } finally {
      setCreatingInvite(false);
    }
  };

  const handleRevokeInvite = async (inviteId: string) => {
    if (!window.confirm("Revoke this invite?")) return;
    try {
      await api.invites.revoke(inviteId);
      fetchInvites();
    } catch {
      // ignore
    }
  };

  const copyCode = (code: string, id: string) => {
    navigator.clipboard.writeText(code);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-sm text-muted-foreground">Loading...</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      <h1 className="text-xl font-semibold mb-6">Members</h1>

      <Tabs defaultValue="members">
        <TabsList>
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="waitlist">
            Waitlist
            {waitlist.filter((e) => e.status === "pending").length > 0 && (
              <Badge variant="secondary" className="ml-2">
                {waitlist.filter((e) => e.status === "pending").length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="invites">Invites</TabsTrigger>
        </TabsList>

        {/* ── Members ─────────────────────────────────────────── */}
        <TabsContent value="members">
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
                        <span className="text-muted-foreground text-xs">{"\u2014"}</span>
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
        </TabsContent>

        {/* ── Waitlist ────────────────────────────────────────── */}
        <TabsContent value="waitlist">
          {inviteCode && (
            <div className="mb-4 rounded-lg border border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950 p-4">
              <p className="text-sm font-medium mb-1">Invite code generated:</p>
              <div className="flex items-center gap-2">
                <code className="text-xs bg-background border border-border rounded px-2 py-1 font-mono break-all">
                  {inviteCode}
                </code>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => copyCode(inviteCode, "banner")}
                >
                  {copiedId === "banner" ? "Copied" : "Copy"}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                Share this code with the approved user.
              </p>
            </div>
          )}

          {waitlistLoading ? (
            <p className="text-sm text-muted-foreground">Loading waitlist...</p>
          ) : waitlist.length === 0 ? (
            <p className="text-sm text-muted-foreground">No waitlist entries.</p>
          ) : (
            <div className="rounded-xl border border-border bg-card overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/50">
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Email</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Name</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Message</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Status</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Submitted</th>
                    <th className="text-right px-4 py-3 font-medium text-muted-foreground">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {waitlist.map((entry) => (
                    <tr key={entry.id} className="border-b border-border last:border-0">
                      <td className="px-4 py-3 break-all">{entry.email}</td>
                      <td className="px-4 py-3">{entry.display_name ?? "\u2014"}</td>
                      <td className="px-4 py-3 max-w-xs truncate" title={entry.message ?? undefined}>
                        {entry.message ?? "\u2014"}
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          variant={
                            entry.status === "approved"
                              ? "default"
                              : entry.status === "rejected"
                                ? "destructive"
                                : "secondary"
                          }
                        >
                          {entry.status}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {new Date(entry.created_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {entry.status === "pending" ? (
                          <div className="flex gap-1 justify-end">
                            <Button
                              size="sm"
                              variant="default"
                              disabled={reviewingId === entry.id}
                              onClick={() => handleReview(entry.id, "approved")}
                            >
                              {reviewingId === entry.id ? "..." : "Approve"}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={reviewingId === entry.id}
                              onClick={() => handleReview(entry.id, "rejected")}
                            >
                              Reject
                            </Button>
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">{"\u2014"}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </TabsContent>

        {/* ── Invites ─────────────────────────────────────────── */}
        <TabsContent value="invites">
          <div className="mb-4">
            {showCreateForm ? (
              <form onSubmit={handleCreateInvite} className="flex items-end gap-3">
                <div className="flex flex-col gap-1">
                  <label htmlFor="inv-email" className="text-xs font-medium text-muted-foreground">
                    Email
                  </label>
                  <input
                    id="inv-email"
                    type="email"
                    required
                    value={newInviteEmail}
                    onChange={(e) => setNewInviteEmail(e.target.value)}
                    className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label htmlFor="inv-days" className="text-xs font-medium text-muted-foreground">
                    Expires in (days)
                  </label>
                  <input
                    id="inv-days"
                    type="number"
                    min={1}
                    max={365}
                    value={newInviteDays}
                    onChange={(e) => setNewInviteDays(Number(e.target.value))}
                    className="w-20 rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
                <Button type="submit" size="sm" disabled={creatingInvite}>
                  {creatingInvite ? "Creating..." : "Create"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowCreateForm(false)}
                >
                  Cancel
                </Button>
              </form>
            ) : (
              <Button size="sm" onClick={() => setShowCreateForm(true)}>
                Create invite
              </Button>
            )}
          </div>

          {invitesLoading ? (
            <p className="text-sm text-muted-foreground">Loading invites...</p>
          ) : invites.length === 0 ? (
            <p className="text-sm text-muted-foreground">No invites yet.</p>
          ) : (
            <div className="rounded-xl border border-border bg-card overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/50">
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Email</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Code</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Expires</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Status</th>
                    <th className="text-right px-4 py-3 font-medium text-muted-foreground">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {invites.map((inv) => {
                    const isExpired = new Date(inv.expires_at) < new Date();
                    const isRedeemed = !!inv.redeemed_at;
                    return (
                      <tr key={inv.id} className="border-b border-border last:border-0">
                        <td className="px-4 py-3 break-all">{inv.email}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1">
                            <code className="text-xs font-mono truncate max-w-[120px]" title={inv.code}>
                              {inv.code.slice(0, 12)}...
                            </code>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-6 px-1 text-xs"
                              onClick={() => copyCode(inv.code, inv.id)}
                            >
                              {copiedId === inv.id ? "Copied" : "Copy"}
                            </Button>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-muted-foreground">
                          {new Date(inv.expires_at).toLocaleDateString()}
                        </td>
                        <td className="px-4 py-3">
                          <Badge
                            variant={
                              isRedeemed
                                ? "default"
                                : isExpired
                                  ? "destructive"
                                  : "secondary"
                            }
                          >
                            {isRedeemed ? "Redeemed" : isExpired ? "Expired" : "Pending"}
                          </Badge>
                        </td>
                        <td className="px-4 py-3 text-right">
                          {!isRedeemed && !isExpired ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => handleRevokeInvite(inv.id)}
                            >
                              Revoke
                            </Button>
                          ) : (
                            <span className="text-xs text-muted-foreground">{"\u2014"}</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
