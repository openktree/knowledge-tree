"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/auth";
import {
  getGraph,
  listGraphMembers,
  addGraphMember,
  removeGraphMember,
  updateGraphMemberRole,
  updateGraph,
} from "@/lib/api";
import type { GraphResponse, GraphMemberResponse } from "@/types";

export default function GraphDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const { user } = useAuth();

  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [members, setMembers] = useState<GraphMemberResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");

  // Add member form
  const [showAddMember, setShowAddMember] = useState(false);
  const [newMemberUserId, setNewMemberUserId] = useState("");
  const [newMemberRole, setNewMemberRole] = useState("reader");
  const [addingMember, setAddingMember] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [g, m] = await Promise.all([
        getGraph(slug),
        listGraphMembers(slug),
      ]);
      setGraph(g);
      setMembers(m);
      setEditName(g.name);
      setEditDescription(g.description || "");
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleSave = async () => {
    if (!graph) return;
    try {
      const updated = await updateGraph(slug, {
        name: editName,
        description: editDescription || undefined,
      });
      setGraph(updated);
      setEditing(false);
    } catch {
      // ignore
    }
  };

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddingMember(true);
    try {
      await addGraphMember(slug, {
        user_id: newMemberUserId,
        role: newMemberRole,
      });
      setNewMemberUserId("");
      setNewMemberRole("reader");
      setShowAddMember(false);
      fetchData();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to add member");
    } finally {
      setAddingMember(false);
    }
  };

  const handleRemoveMember = async (userId: string) => {
    if (!window.confirm("Remove this member?")) return;
    try {
      await removeGraphMember(slug, userId);
      fetchData();
    } catch {
      // ignore
    }
  };

  const handleChangeRole = async (userId: string, currentRole: string) => {
    const newRole = currentRole === "admin" ? "writer" : currentRole === "writer" ? "reader" : "admin";
    try {
      await updateGraphMemberRole(slug, userId, { role: newRole });
      fetchData();
    } catch {
      // ignore
    }
  };

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-sm text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (!graph) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">Graph not found.</p>
      </div>
    );
  }

  const isAdmin = user?.is_superuser || members.some(
    (m) => m.user_id === user?.id && m.role === "admin"
  );

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          {editing ? (
            <div className="space-y-2">
              <input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="rounded-md border border-border bg-background px-3 py-1.5 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <input
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                placeholder="Description"
                className="block rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring w-full"
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={handleSave}>Save</Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
              </div>
            </div>
          ) : (
            <>
              <h1 className="text-xl font-semibold">{graph.name}</h1>
              {graph.description && (
                <p className="text-sm text-muted-foreground mt-1">{graph.description}</p>
              )}
            </>
          )}
        </div>
        <div className="flex gap-2 items-center">
          {graph.is_default && <Badge variant="outline">Default</Badge>}
          <Badge variant={graph.status === "active" ? "default" : "secondary"}>
            {graph.status}
          </Badge>
          {isAdmin && !editing && (
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
        </div>
      </div>

      {/* Details */}
      <div className="rounded-xl border border-border bg-card p-4 mb-6">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          <div>
            <p className="text-xs text-muted-foreground">Slug</p>
            <p className="font-mono text-xs">{graph.slug}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Schema</p>
            <p className="font-mono text-xs">{graph.schema_name}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Storage</p>
            <p>{graph.storage_mode === "database" ? "Separate DB" : "Shared DB"}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Nodes</p>
            <p>{graph.node_count}</p>
          </div>
        </div>
      </div>

      {/* Members */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-medium">Members ({members.length})</h2>
        {isAdmin && !showAddMember && (
          <Button size="sm" onClick={() => setShowAddMember(true)}>
            Add Member
          </Button>
        )}
      </div>

      {showAddMember && (
        <form onSubmit={handleAddMember} className="mb-4 flex items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">User ID</label>
            <input
              required
              value={newMemberUserId}
              onChange={(e) => setNewMemberUserId(e.target.value)}
              placeholder="UUID"
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">Role</label>
            <select
              value={newMemberRole}
              onChange={(e) => setNewMemberRole(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="reader">Reader</option>
              <option value="writer">Writer</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <Button type="submit" size="sm" disabled={addingMember}>
            {addingMember ? "Adding..." : "Add"}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setShowAddMember(false)}>
            Cancel
          </Button>
        </form>
      )}

      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50">
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Email</th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Name</th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Role</th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">Added</th>
              {isAdmin && (
                <th className="text-right px-4 py-3 font-medium text-muted-foreground">Actions</th>
              )}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3 break-all">{m.email}</td>
                <td className="px-4 py-3">{m.display_name ?? "\u2014"}</td>
                <td className="px-4 py-3">
                  <Badge variant={m.role === "admin" ? "default" : "secondary"}>
                    {m.role}
                  </Badge>
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {new Date(m.created_at).toLocaleDateString()}
                </td>
                {isAdmin && (
                  <td className="px-4 py-3 text-right">
                    <div className="flex gap-1 justify-end">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleChangeRole(m.user_id, m.role)}
                      >
                        Cycle Role
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleRemoveMember(m.user_id)}
                      >
                        Remove
                      </Button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
            {members.length === 0 && (
              <tr>
                <td colSpan={isAdmin ? 5 : 4} className="px-4 py-6 text-center text-muted-foreground">
                  No members yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
