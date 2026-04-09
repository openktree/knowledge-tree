"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Loader2, Plus, UserPlus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAuth } from "@/contexts/auth";
import { useGraph } from "@/contexts/graph";
import {
  getGraph,
  listGraphMembers,
  addGraphMember,
  removeGraphMember,
  updateGraphMemberRole,
  updateGraph,
} from "@/lib/api";
import { DeleteGraphDialog } from "@/components/graphs/DeleteGraphDialog";
import { MemberSearch } from "@/components/graphs/MemberSearch";
import type { GraphResponse, GraphMemberResponse, MemberResponse } from "@/types";

const ROLE_DESCRIPTIONS: Record<string, string> = {
  reader: "Can view nodes, edges, and facts",
  writer: "Can create and edit content",
  admin: "Full access including member management",
};

export default function GraphDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const { refreshGraphs: refreshGraphContext } = useGraph();

  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [members, setMembers] = useState<GraphMemberResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  // Add member form
  const [showAddMember, setShowAddMember] = useState(false);
  const [selectedMember, setSelectedMember] = useState<MemberResponse | null>(null);
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
    } catch (err) {
      console.error("Graph operation failed:", err);
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
      refreshGraphContext();
    } catch (err) {
      console.error("Graph operation failed:", err);
    }
  };

  const [togglesSaving, setTogglesSaving] = useState(false);
  const [togglesError, setTogglesError] = useState<string | null>(null);

  const handleTogglePublicCache = async (
    field: "contribute_to_public" | "use_public_cache",
    value: boolean,
  ) => {
    if (!graph) return;
    // Optimistic update so the switch flips immediately; rolled back
    // on failure. Saves a re-render flicker on the slow API path.
    const previous = graph;
    setGraph({ ...graph, [field]: value });
    setTogglesError(null);
    setTogglesSaving(true);
    try {
      const updated = await updateGraph(slug, { [field]: value });
      setGraph(updated);
    } catch (err) {
      setGraph(previous);
      setTogglesError(err instanceof Error ? err.message : "Failed to update toggle");
    } finally {
      setTogglesSaving(false);
    }
  };

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedMember) return;
    setAddingMember(true);
    try {
      await addGraphMember(slug, {
        user_id: selectedMember.id,
        role: newMemberRole,
      });
      setSelectedMember(null);
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
    } catch (err) {
      console.error("Graph operation failed:", err);
    }
  };

  const handleChangeRole = async (userId: string, newRole: string) => {
    try {
      await updateGraphMemberRole(slug, userId, { role: newRole });
      fetchData();
    } catch (err) {
      console.error("Graph operation failed:", err);
    }
  };

  const handleDeleted = () => {
    refreshGraphContext();
    router.push("/graphs");
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
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

  const isAdmin =
    user?.is_superuser ||
    members.some((m) => m.user_id === user?.id && m.role === "admin");

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
                <Button size="sm" onClick={handleSave}>
                  Save
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <>
              <h1 className="text-xl font-semibold">{graph.name}</h1>
              {graph.description && (
                <p className="text-sm text-muted-foreground mt-1">
                  {graph.description}
                </p>
              )}
            </>
          )}
        </div>
        <div className="flex gap-2 items-center">
          {graph.is_default && <Badge variant="outline">Default</Badge>}
          <Badge
            variant={graph.status === "active" ? "default" : "secondary"}
          >
            {graph.status}
          </Badge>
          {isAdmin && !editing && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setEditing(true)}
            >
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
            <p className="text-xs text-muted-foreground">Database</p>
            <p>{graph.database_connection_name ?? "default"}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Nodes</p>
            <p>{graph.node_count}</p>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm mt-3 pt-3 border-t border-border">
          <div>
            <p className="text-xs text-muted-foreground">Members</p>
            <p>{members.length}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Type</p>
            <p>{graph.graph_type}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">BYOK</p>
            <p>{graph.byok_enabled ? "Enabled" : "Disabled"}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Created</p>
            <p>
              {new Date(graph.created_at).toLocaleDateString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
              })}
            </p>
          </div>
        </div>
      </div>

      {/* Public cache settings — non-default graphs only.
          The default graph has no upstream so the API rejects toggle
          edits there with HTTP 400; rendering them on the default graph
          would just be a misleading affordance. */}
      {!graph.is_default && (
        <div className="rounded-xl border border-border bg-card p-4 mb-6">
          <div className="mb-3">
            <h2 className="text-base font-medium">Public knowledge sharing</h2>
            <p className="text-xs text-muted-foreground mt-1">
              Connect this graph to the shared public knowledge pool to save
              decomposition cost on URLs other graphs have already processed,
              and to grow the public pool with new ones you ingest. File
              uploads are always private regardless of these settings.
            </p>
          </div>

          <div className="space-y-3 divide-y divide-border">
            <label className="flex items-start gap-3 pt-3 first:pt-0">
              <Switch
                checked={graph.use_public_cache}
                onCheckedChange={(checked) =>
                  handleTogglePublicCache("use_public_cache", checked)
                }
                disabled={!isAdmin || togglesSaving}
                aria-label="Use public knowledge cache"
              />
              <div className="flex-1">
                <p className="text-sm font-medium">Use public cache</p>
                <p className="text-xs text-muted-foreground">
                  Before decomposing a fetched URL, check the public graph for
                  an existing decomposition. On a hit, facts and concept nodes
                  are imported into this graph and the LLM cost is skipped.
                </p>
              </div>
            </label>

            <label className="flex items-start gap-3 pt-3">
              <Switch
                checked={graph.contribute_to_public}
                onCheckedChange={(checked) =>
                  handleTogglePublicCache("contribute_to_public", checked)
                }
                disabled={!isAdmin || togglesSaving}
                aria-label="Contribute to public knowledge cache"
              />
              <div className="flex-1">
                <p className="text-sm font-medium">Contribute to public graph</p>
                <p className="text-xs text-muted-foreground">
                  After decomposing a fetched URL, push the source and
                  extracted facts upstream so the public pool grows with use.
                </p>
              </div>
            </label>
          </div>

          {togglesError && (
            <p className="mt-3 text-xs text-destructive">{togglesError}</p>
          )}
        </div>
      )}

      {/* Members */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-medium">Members ({members.length})</h2>
        {isAdmin && !showAddMember && (
          <Button size="sm" onClick={() => setShowAddMember(true)}>
            <UserPlus className="mr-1.5 size-3.5" />
            Add Member
          </Button>
        )}
      </div>

      {showAddMember && (
        <form
          onSubmit={handleAddMember}
          className="mb-4 rounded-lg border border-border bg-card p-3 space-y-3"
        >
          <div className="flex items-end gap-3">
            <div className="flex flex-col gap-1 flex-1">
              <label className="text-xs font-medium text-muted-foreground">
                Search user by email
              </label>
              <MemberSearch
                onSelect={setSelectedMember}
                excludeUserIds={members.map((m) => m.user_id)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">
                Role
              </label>
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
            <Button
              type="submit"
              size="sm"
              disabled={addingMember || !selectedMember}
            >
              {addingMember ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <>
                  <Plus className="mr-1 size-3" />
                  Add
                </>
              )}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setShowAddMember(false);
                setSelectedMember(null);
              }}
            >
              Cancel
            </Button>
          </div>
          {selectedMember && (
            <p className="text-xs text-muted-foreground">
              Selected: <strong>{selectedMember.email}</strong>
              {selectedMember.display_name && ` (${selectedMember.display_name})`}
            </p>
          )}
        </form>
      )}

      <div className="rounded-xl border border-border bg-card overflow-hidden mb-8">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50">
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">
                Email
              </th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">
                Name
              </th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">
                <Tooltip>
                  <TooltipTrigger className="cursor-help underline decoration-dotted underline-offset-4">
                    Role
                  </TooltipTrigger>
                  <TooltipContent side="top" className="max-w-xs text-xs">
                    <p>
                      <strong>Reader:</strong> {ROLE_DESCRIPTIONS.reader}
                    </p>
                    <p>
                      <strong>Writer:</strong> {ROLE_DESCRIPTIONS.writer}
                    </p>
                    <p>
                      <strong>Admin:</strong> {ROLE_DESCRIPTIONS.admin}
                    </p>
                  </TooltipContent>
                </Tooltip>
              </th>
              <th className="text-left px-4 py-3 font-medium text-muted-foreground">
                Added
              </th>
              {isAdmin && (
                <th className="text-right px-4 py-3 font-medium text-muted-foreground">
                  Actions
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr
                key={m.id}
                className="border-b border-border last:border-0"
              >
                <td className="px-4 py-3 break-all">{m.email}</td>
                <td className="px-4 py-3">
                  {m.display_name ?? "\u2014"}
                </td>
                <td className="px-4 py-3">
                  <Badge
                    variant={m.role === "admin" ? "default" : "secondary"}
                  >
                    {m.role}
                  </Badge>
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {new Date(m.created_at).toLocaleDateString()}
                </td>
                {isAdmin && (
                  <td className="px-4 py-3 text-right">
                    <div className="flex gap-1 justify-end items-center">
                      <select
                        value={m.role}
                        onChange={(e) =>
                          handleChangeRole(m.user_id, e.target.value)
                        }
                        className="rounded-md border border-border bg-background px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
                      >
                        <option value="reader">Reader</option>
                        <option value="writer">Writer</option>
                        <option value="admin">Admin</option>
                      </select>
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
                <td
                  colSpan={isAdmin ? 5 : 4}
                  className="px-4 py-6 text-center text-muted-foreground"
                >
                  No members yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Danger zone */}
      {user?.is_superuser && !graph.is_default && (
        <div className="rounded-xl border border-destructive/30 p-4">
          <h3 className="text-sm font-medium text-destructive mb-2">
            Danger Zone
          </h3>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Delete this graph</p>
              <p className="text-xs text-muted-foreground">
                Once deleted, all data in this graph will be permanently
                removed.
              </p>
            </div>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setShowDeleteDialog(true)}
            >
              Delete graph
            </Button>
          </div>
        </div>
      )}

      <DeleteGraphDialog
        graph={graph}
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        onDeleted={handleDeleted}
      />
    </div>
  );
}
