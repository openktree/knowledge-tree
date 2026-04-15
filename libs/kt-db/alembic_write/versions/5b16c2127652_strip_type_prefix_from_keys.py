"""strip_type_prefix_from_keys

Revision ID: 5b16c2127652
Revises: 750a40fc98be
Create Date: 2026-04-13 15:07:24.149149

Strips node-type prefixes (concept:, entity:, event:, location:) from all
write-db keys and collapses cross_type edges to related.

When multiple prefixed keys map to the same stripped key (e.g.
entity:pubmed-medline + concept:pubmed-medline → pubmed-medline), a merge
is performed: the "winner" keeps the key, all FK references from losers
are re-pointed, and losers are deleted.

Winner selection: highest fact_count (seeds) or most fact_ids (nodes/edges),
tiebreak by most recent updated_at.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5b16c2127652"
down_revision: Union[str, None] = "750a40fc98be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BASE_NODE_TYPES = ("concept", "entity", "event", "location")


def _strip_key(key: str) -> str:
    """Strip type prefix from a single key."""
    for nt in BASE_NODE_TYPES:
        prefix = f"{nt}:"
        if key.startswith(prefix):
            return key[len(prefix) :]
    return key


def _strip_edge_key(key: str) -> str:
    """Strip type prefixes from an edge key and collapse cross_type→related."""
    result = key.replace("cross_type:", "related:")
    for nt in BASE_NODE_TYPES:
        result = result.replace(f"{nt}:", "")
    return result


def upgrade() -> None:
    conn = op.get_bind()

    # ================================================================
    # PHASE 1: Merge colliding seeds
    # ================================================================
    _merge_seed_collisions(conn)

    # ================================================================
    # PHASE 2: Merge colliding nodes
    # ================================================================
    _merge_node_collisions(conn)

    # ================================================================
    # PHASE 3: Merge colliding edges
    # ================================================================
    _merge_edge_collisions(conn)

    # ================================================================
    # PHASE 4: Strip prefixes from all remaining keys (no collisions)
    # ================================================================
    _strip_all_prefixes(conn)


# ── Seed merges ────────────────────────────────────────────���─────────


def _merge_seed_collisions(conn: sa.engine.Connection) -> None:
    """Find seed collision groups and merge losers into winners.

    Two kinds of collisions:
    A. Multiple prefixed keys map to same stripped key (e.g. concept:foo + entity:foo).
    B. A prefixed key's stripped form already exists as an unprefixed key.
    """
    rows = conn.execute(sa.text("""
        WITH stripped AS (
            SELECT key,
                   CASE WHEN key LIKE 'concept:%%' THEN substring(key from 9)
                        WHEN key LIKE 'entity:%%' THEN substring(key from 8)
                        WHEN key LIKE 'event:%%' THEN substring(key from 7)
                        WHEN key LIKE 'location:%%' THEN substring(key from 10)
                   END as new_key
            FROM write_seeds
            WHERE key LIKE 'concept:%%' OR key LIKE 'entity:%%'
               OR key LIKE 'event:%%' OR key LIKE 'location:%%'
        ),
        -- Case A: multi-prefix collisions
        multi_prefix AS (
            SELECT s.key, s.new_key
            FROM stripped s
            WHERE s.new_key IN (
                SELECT new_key FROM stripped GROUP BY new_key HAVING count(*) > 1
            )
        ),
        -- Case B: prefixed key's stripped form already exists unprefixed.
        -- Include BOTH the prefixed loser AND the existing unprefixed key
        -- so the group picks a winner among all candidates.
        existing_unprefixed AS (
            SELECT s.key, s.new_key
            FROM stripped s
            WHERE s.new_key IN (
                SELECT key FROM write_seeds
                WHERE key NOT LIKE 'concept:%%' AND key NOT LIKE 'entity:%%'
                  AND key NOT LIKE 'event:%%' AND key NOT LIKE 'location:%%'
            )
            UNION
            SELECT ws.key, ws.key as new_key
            FROM write_seeds ws
            WHERE ws.key IN (
                SELECT new_key FROM stripped
                WHERE new_key IN (
                    SELECT key FROM write_seeds
                    WHERE key NOT LIKE 'concept:%%' AND key NOT LIKE 'entity:%%'
                      AND key NOT LIKE 'event:%%' AND key NOT LIKE 'location:%%'
                )
            )
        )
        SELECT key, new_key FROM multi_prefix
        UNION
        SELECT key, new_key FROM existing_unprefixed
        ORDER BY 2, 1
    """)).fetchall()

    if not rows:
        return

    # Group by new_key
    groups: dict[str, list[str]] = {}
    for old_key, new_key in rows:
        groups.setdefault(new_key, []).append(old_key)

    # For each group, pick winner and merge
    for new_key, old_keys in groups.items():
        # Pick winner: highest fact_count, tiebreak by updated_at desc
        winner_row = conn.execute(sa.text("""
            SELECT key FROM write_seeds
            WHERE key = ANY(:keys)
            ORDER BY fact_count DESC, updated_at DESC
            LIMIT 1
        """), {"keys": old_keys}).fetchone()
        assert winner_row is not None
        winner_key = winner_row[0]
        loser_keys = [k for k in old_keys if k != winner_key]

        for loser_key in loser_keys:
            _repoint_seed_refs(conn, loser_key, winner_key)
            # Merge fact_count into winner (sum of unique facts, not just counts)
            conn.execute(sa.text("""
                UPDATE write_seeds
                SET fact_count = (
                    SELECT count(DISTINCT fact_id)
                    FROM write_seed_facts
                    WHERE seed_key = :winner
                )
                WHERE key = :winner
            """), {"winner": winner_key})
            # Delete loser seed
            conn.execute(
                sa.text("DELETE FROM write_seeds WHERE key = :loser"),
                {"loser": loser_key},
            )


def _repoint_seed_refs(
    conn: sa.engine.Connection, loser: str, winner: str
) -> None:
    """Re-point all references from loser seed key to winner."""
    # write_seed_facts — delete dupes first (same fact linked to both)
    conn.execute(sa.text("""
        DELETE FROM write_seed_facts
        WHERE seed_key = :loser
          AND fact_id IN (
              SELECT fact_id FROM write_seed_facts WHERE seed_key = :winner
          )
    """), {"loser": loser, "winner": winner})
    conn.execute(sa.text("""
        UPDATE write_seed_facts SET seed_key = :winner WHERE seed_key = :loser
    """), {"loser": loser, "winner": winner})

    # write_edge_candidates — delete dupes first (same pair+fact)
    # seed_key_a
    conn.execute(sa.text("""
        DELETE FROM write_edge_candidates
        WHERE seed_key_a = :loser
          AND EXISTS (
              SELECT 1 FROM write_edge_candidates wec2
              WHERE wec2.seed_key_a = :winner
                AND wec2.seed_key_b = write_edge_candidates.seed_key_b
                AND wec2.fact_id = write_edge_candidates.fact_id
          )
    """), {"loser": loser, "winner": winner})
    conn.execute(sa.text("""
        UPDATE write_edge_candidates SET seed_key_a = :winner WHERE seed_key_a = :loser
    """), {"loser": loser, "winner": winner})

    # seed_key_b
    conn.execute(sa.text("""
        DELETE FROM write_edge_candidates
        WHERE seed_key_b = :loser
          AND EXISTS (
              SELECT 1 FROM write_edge_candidates wec2
              WHERE wec2.seed_key_b = :winner
                AND wec2.seed_key_a = write_edge_candidates.seed_key_a
                AND wec2.fact_id = write_edge_candidates.fact_id
          )
    """), {"loser": loser, "winner": winner})
    conn.execute(sa.text("""
        UPDATE write_edge_candidates SET seed_key_b = :winner WHERE seed_key_b = :loser
    """), {"loser": loser, "winner": winner})

    # write_seed_merges — just re-point, no unique constraints
    conn.execute(sa.text("""
        UPDATE write_seed_merges SET source_seed_key = :winner
        WHERE source_seed_key = :loser
    """), {"loser": loser, "winner": winner})
    conn.execute(sa.text("""
        UPDATE write_seed_merges SET target_seed_key = :winner
        WHERE target_seed_key = :loser
    """), {"loser": loser, "winner": winner})

    # write_seed_routes — delete dupes on (parent, child) unique constraint
    conn.execute(sa.text("""
        DELETE FROM write_seed_routes
        WHERE parent_seed_key = :loser
          AND child_seed_key IN (
              SELECT child_seed_key FROM write_seed_routes
              WHERE parent_seed_key = :winner
          )
    """), {"loser": loser, "winner": winner})
    conn.execute(sa.text("""
        UPDATE write_seed_routes SET parent_seed_key = :winner
        WHERE parent_seed_key = :loser
    """), {"loser": loser, "winner": winner})

    conn.execute(sa.text("""
        DELETE FROM write_seed_routes
        WHERE child_seed_key = :loser
          AND parent_seed_key IN (
              SELECT parent_seed_key FROM write_seed_routes
              WHERE child_seed_key = :winner
          )
    """), {"loser": loser, "winner": winner})
    conn.execute(sa.text("""
        UPDATE write_seed_routes SET child_seed_key = :winner
        WHERE child_seed_key = :loser
    """), {"loser": loser, "winner": winner})

    # write_seeds.merged_into_key — seeds that were merged into the loser
    conn.execute(sa.text("""
        UPDATE write_seeds SET merged_into_key = :winner
        WHERE merged_into_key = :loser
    """), {"loser": loser, "winner": winner})


# ── Node merges ──────────────────────────────────────────────────────


def _merge_node_collisions(conn: sa.engine.Connection) -> None:
    """Merge colliding node keys. Winner = most fact_ids, tiebreak updated_at."""
    rows = conn.execute(sa.text("""
        WITH stripped AS (
            SELECT key,
                   CASE WHEN key LIKE 'concept:%%' THEN substring(key from 9)
                        WHEN key LIKE 'entity:%%' THEN substring(key from 8)
                        WHEN key LIKE 'event:%%' THEN substring(key from 7)
                        WHEN key LIKE 'location:%%' THEN substring(key from 10)
                   END as new_key
            FROM write_nodes
            WHERE key LIKE 'concept:%%' OR key LIKE 'entity:%%'
               OR key LIKE 'event:%%' OR key LIKE 'location:%%'
        ),
        multi_prefix AS (
            SELECT s.key, s.new_key FROM stripped s
            WHERE s.new_key IN (SELECT new_key FROM stripped GROUP BY new_key HAVING count(*) > 1)
        ),
        existing_unprefixed AS (
            SELECT s.key, s.new_key FROM stripped s
            WHERE s.new_key IN (
                SELECT key FROM write_nodes
                WHERE key NOT LIKE 'concept:%%' AND key NOT LIKE 'entity:%%'
                  AND key NOT LIKE 'event:%%' AND key NOT LIKE 'location:%%'
            )
            UNION
            SELECT wn.key, wn.key as new_key FROM write_nodes wn
            WHERE wn.key IN (
                SELECT new_key FROM stripped WHERE new_key IN (
                    SELECT key FROM write_nodes
                    WHERE key NOT LIKE 'concept:%%' AND key NOT LIKE 'entity:%%'
                      AND key NOT LIKE 'event:%%' AND key NOT LIKE 'location:%%'
                )
            )
        )
        SELECT key, new_key FROM multi_prefix
        UNION
        SELECT key, new_key FROM existing_unprefixed
        ORDER BY 2, 1
    """)).fetchall()

    if not rows:
        return

    groups: dict[str, list[str]] = {}
    for old_key, new_key in rows:
        groups.setdefault(new_key, []).append(old_key)

    for new_key, old_keys in groups.items():
        # Winner = most fact_ids, tiebreak updated_at
        winner_row = conn.execute(sa.text("""
            SELECT key FROM write_nodes
            WHERE key = ANY(:keys)
            ORDER BY coalesce(array_length(fact_ids, 1), 0) DESC, updated_at DESC
            LIMIT 1
        """), {"keys": old_keys}).fetchone()
        assert winner_row is not None
        winner_key = winner_row[0]
        loser_keys = [k for k in old_keys if k != winner_key]

        for loser_key in loser_keys:
            _repoint_node_refs(conn, loser_key, winner_key)
            # Merge fact_ids arrays (union)
            conn.execute(sa.text("""
                UPDATE write_nodes
                SET fact_ids = (
                    SELECT array_agg(DISTINCT fid)
                    FROM (
                        SELECT unnest(fact_ids) as fid FROM write_nodes WHERE key = :winner
                        UNION
                        SELECT unnest(fact_ids) as fid FROM write_nodes WHERE key = :loser
                    ) sub
                    WHERE fid IS NOT NULL
                )
                WHERE key = :winner
            """), {"winner": winner_key, "loser": loser_key})
            conn.execute(
                sa.text("DELETE FROM write_nodes WHERE key = :loser"),
                {"loser": loser_key},
            )


def _repoint_node_refs(
    conn: sa.engine.Connection, loser: str, winner: str
) -> None:
    """Re-point all references from loser node key to winner."""
    # write_edges.source_node_key / target_node_key
    conn.execute(sa.text("""
        UPDATE write_edges SET source_node_key = :winner
        WHERE source_node_key = :loser
    """), {"loser": loser, "winner": winner})
    conn.execute(sa.text("""
        UPDATE write_edges SET target_node_key = :winner
        WHERE target_node_key = :loser
    """), {"loser": loser, "winner": winner})

    # write_nodes.parent_key
    conn.execute(sa.text("""
        UPDATE write_nodes SET parent_key = :winner
        WHERE parent_key = :loser
    """), {"loser": loser, "winner": winner})

    # write_seeds.promoted_node_key
    conn.execute(sa.text("""
        UPDATE write_seeds SET promoted_node_key = :winner
        WHERE promoted_node_key = :loser
    """), {"loser": loser, "winner": winner})

    # write_node_counters — merge by summing, delete loser after
    # First check if winner already has a counter row
    has_winner = conn.execute(sa.text("""
        SELECT 1 FROM write_node_counters WHERE node_key = :winner
    """), {"winner": winner}).fetchone()

    if has_winner:
        # Sum loser's counts into winner
        conn.execute(sa.text("""
            UPDATE write_node_counters wnc
            SET access_count = wnc.access_count + COALESCE(loser.access_count, 0),
                update_count = wnc.update_count + COALESCE(loser.update_count, 0),
                seed_fact_count = wnc.seed_fact_count + COALESCE(loser.seed_fact_count, 0)
            FROM write_node_counters loser
            WHERE wnc.node_key = :winner AND loser.node_key = :loser
        """), {"winner": winner, "loser": loser})
        conn.execute(sa.text("""
            DELETE FROM write_node_counters WHERE node_key = :loser
        """), {"loser": loser})
    else:
        conn.execute(sa.text("""
            UPDATE write_node_counters SET node_key = :winner
            WHERE node_key = :loser
        """), {"loser": loser, "winner": winner})

    # write_convergence_reports — just re-point
    conn.execute(sa.text("""
        UPDATE write_convergence_reports SET node_key = :winner
        WHERE node_key = :loser
    """), {"loser": loser, "winner": winner})

    # write_dimensions — key starts with node_key: prefix
    # These have format node_key:model_slug:batch_index
    # Replace the node_key prefix portion
    conn.execute(sa.text("""
        UPDATE write_dimensions
        SET key = :winner || substring(key from length(:loser) + 1)
        WHERE key LIKE :loser_prefix
    """), {"winner": winner, "loser": loser, "loser_prefix": loser + ":%"})

    # write_node_versions — re-point node_key
    conn.execute(sa.text("""
        UPDATE write_node_versions SET node_key = :winner
        WHERE node_key = :loser
    """), {"loser": loser, "winner": winner})


# ── Edge merges ──────────────────────────────────────────────────────


def _merge_edge_collisions(conn: sa.engine.Connection) -> None:
    """Merge colliding edge keys after prefix stripping + cross_type→related."""
    rows = conn.execute(sa.text("""
        WITH stripped AS (
            SELECT key,
                   regexp_replace(
                       regexp_replace(
                           regexp_replace(
                               regexp_replace(
                                   regexp_replace(key, 'cross_type:', 'related:'),
                                   'concept:', '', 'g'),
                               'entity:', '', 'g'),
                           'event:', '', 'g'),
                       'location:', '', 'g') as new_key
            FROM write_edges
        ),
        groups AS (
            SELECT new_key FROM stripped GROUP BY new_key HAVING count(*) > 1
        )
        SELECT s.key, s.new_key
        FROM stripped s JOIN groups g ON s.new_key = g.new_key
        ORDER BY s.new_key, s.key
    """)).fetchall()

    if not rows:
        return

    groups: dict[str, list[str]] = {}
    for old_key, new_key in rows:
        groups.setdefault(new_key, []).append(old_key)

    for new_key, old_keys in groups.items():
        # Winner = most fact_ids, tiebreak updated_at
        winner_row = conn.execute(sa.text("""
            SELECT key FROM write_edges
            WHERE key = ANY(:keys)
            ORDER BY coalesce(array_length(fact_ids, 1), 0) DESC, updated_at DESC
            LIMIT 1
        """), {"keys": old_keys}).fetchone()
        assert winner_row is not None
        winner_key = winner_row[0]
        loser_keys = [k for k in old_keys if k != winner_key]

        for loser_key in loser_keys:
            # Merge fact_ids
            conn.execute(sa.text("""
                UPDATE write_edges
                SET fact_ids = (
                    SELECT array_agg(DISTINCT fid)
                    FROM (
                        SELECT unnest(fact_ids) as fid FROM write_edges WHERE key = :winner
                        UNION
                        SELECT unnest(fact_ids) as fid FROM write_edges WHERE key = :loser
                    ) sub
                    WHERE fid IS NOT NULL
                ),
                weight = (
                    SELECT greatest(w.weight, l.weight)
                    FROM write_edges w, write_edges l
                    WHERE w.key = :winner AND l.key = :loser
                )
                WHERE key = :winner
            """), {"winner": winner_key, "loser": loser_key})
            conn.execute(
                sa.text("DELETE FROM write_edges WHERE key = :loser"),
                {"loser": loser_key},
            )


# ── Prefix stripping (non-colliding keys) ───────────────────────────


def _strip_all_prefixes(conn: sa.engine.Connection) -> None:
    """Strip type prefixes from all remaining keys. No collisions at this point."""

    # 1. Seeds
    conn.execute(sa.text("""
        UPDATE write_seeds
        SET key = CASE
            WHEN key LIKE 'concept:%%' THEN substring(key from 9)
            WHEN key LIKE 'entity:%%' THEN substring(key from 8)
            WHEN key LIKE 'event:%%' THEN substring(key from 7)
            WHEN key LIKE 'location:%%' THEN substring(key from 10)
            ELSE key END
        WHERE key LIKE 'concept:%%' OR key LIKE 'entity:%%'
           OR key LIKE 'event:%%' OR key LIKE 'location:%%'
    """))

    # 2. Nodes
    conn.execute(sa.text("""
        UPDATE write_nodes
        SET key = CASE
            WHEN key LIKE 'concept:%%' THEN substring(key from 9)
            WHEN key LIKE 'entity:%%' THEN substring(key from 8)
            WHEN key LIKE 'event:%%' THEN substring(key from 7)
            WHEN key LIKE 'location:%%' THEN substring(key from 10)
            ELSE key END
        WHERE key LIKE 'concept:%%' OR key LIKE 'entity:%%'
           OR key LIKE 'event:%%' OR key LIKE 'location:%%'
    """))

    # 3. Set all base node types to 'concept'
    for table in ("write_seeds", "write_nodes"):
        conn.execute(sa.text(f"""
            UPDATE {table}
            SET node_type = 'concept'
            WHERE node_type IN ('entity', 'event', 'location')
        """))

    # 4. Edges — strip prefixes + cross_type→related in key
    conn.execute(sa.text("""
        UPDATE write_edges
        SET key = regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(key, 'cross_type:', 'related:'),
                        'concept:', '', 'g'),
                    'entity:', '', 'g'),
                'event:', '', 'g'),
            'location:', '', 'g')
        WHERE key LIKE '%%concept:%%' OR key LIKE '%%entity:%%'
           OR key LIKE '%%event:%%' OR key LIKE '%%location:%%'
           OR key LIKE 'cross_type:%%'
    """))

    # 5. Edge relationship_type
    conn.execute(sa.text("""
        UPDATE write_edges
        SET relationship_type = 'related'
        WHERE relationship_type = 'cross_type'
    """))

    # 6. Edge source/target node keys
    conn.execute(sa.text("""
        UPDATE write_edges
        SET source_node_key = CASE
            WHEN source_node_key LIKE 'concept:%%' THEN substring(source_node_key from 9)
            WHEN source_node_key LIKE 'entity:%%' THEN substring(source_node_key from 8)
            WHEN source_node_key LIKE 'event:%%' THEN substring(source_node_key from 7)
            WHEN source_node_key LIKE 'location:%%' THEN substring(source_node_key from 10)
            ELSE source_node_key END,
        target_node_key = CASE
            WHEN target_node_key LIKE 'concept:%%' THEN substring(target_node_key from 9)
            WHEN target_node_key LIKE 'entity:%%' THEN substring(target_node_key from 8)
            WHEN target_node_key LIKE 'event:%%' THEN substring(target_node_key from 7)
            WHEN target_node_key LIKE 'location:%%' THEN substring(target_node_key from 10)
            ELSE target_node_key END
        WHERE source_node_key LIKE 'concept:%%' OR source_node_key LIKE 'entity:%%'
           OR source_node_key LIKE 'event:%%' OR source_node_key LIKE 'location:%%'
           OR target_node_key LIKE 'concept:%%' OR target_node_key LIKE 'entity:%%'
           OR target_node_key LIKE 'event:%%' OR target_node_key LIKE 'location:%%'
    """))

    # 7. Dimensions — strip node_key prefix from composite key
    for nt in BASE_NODE_TYPES:
        prefix_len = len(nt) + 1  # +1 for colon
        conn.execute(sa.text(f"""
            UPDATE write_dimensions
            SET key = substring(key from {prefix_len + 1})
            WHERE key LIKE '{nt}:%%'
        """))

    # 8. Node counters
    conn.execute(sa.text("""
        UPDATE write_node_counters
        SET node_key = CASE
            WHEN node_key LIKE 'concept:%%' THEN substring(node_key from 9)
            WHEN node_key LIKE 'entity:%%' THEN substring(node_key from 8)
            WHEN node_key LIKE 'event:%%' THEN substring(node_key from 7)
            WHEN node_key LIKE 'location:%%' THEN substring(node_key from 10)
            ELSE node_key END
        WHERE node_key LIKE 'concept:%%' OR node_key LIKE 'entity:%%'
           OR node_key LIKE 'event:%%' OR node_key LIKE 'location:%%'
    """))

    # 9. Convergence reports
    conn.execute(sa.text("""
        UPDATE write_convergence_reports
        SET node_key = CASE
            WHEN node_key LIKE 'concept:%%' THEN substring(node_key from 9)
            WHEN node_key LIKE 'entity:%%' THEN substring(node_key from 8)
            WHEN node_key LIKE 'event:%%' THEN substring(node_key from 7)
            WHEN node_key LIKE 'location:%%' THEN substring(node_key from 10)
            ELSE node_key END
        WHERE node_key LIKE 'concept:%%' OR node_key LIKE 'entity:%%'
           OR node_key LIKE 'event:%%' OR node_key LIKE 'location:%%'
    """))

    # 10. Seed FK columns — seed_facts, edge_candidates, merges, routes
    conn.execute(sa.text("""
        UPDATE write_seed_facts
        SET seed_key = CASE
            WHEN seed_key LIKE 'concept:%%' THEN substring(seed_key from 9)
            WHEN seed_key LIKE 'entity:%%' THEN substring(seed_key from 8)
            WHEN seed_key LIKE 'event:%%' THEN substring(seed_key from 7)
            WHEN seed_key LIKE 'location:%%' THEN substring(seed_key from 10)
            ELSE seed_key END
        WHERE seed_key LIKE 'concept:%%' OR seed_key LIKE 'entity:%%'
           OR seed_key LIKE 'event:%%' OR seed_key LIKE 'location:%%'
    """))

    for col in ("seed_key_a", "seed_key_b"):
        conn.execute(sa.text(f"""
            UPDATE write_edge_candidates
            SET {col} = CASE
                WHEN {col} LIKE 'concept:%%' THEN substring({col} from 9)
                WHEN {col} LIKE 'entity:%%' THEN substring({col} from 8)
                WHEN {col} LIKE 'event:%%' THEN substring({col} from 7)
                WHEN {col} LIKE 'location:%%' THEN substring({col} from 10)
                ELSE {col} END
            WHERE {col} LIKE 'concept:%%' OR {col} LIKE 'entity:%%'
               OR {col} LIKE 'event:%%' OR {col} LIKE 'location:%%'
        """))

    for col in ("source_seed_key", "target_seed_key"):
        conn.execute(sa.text(f"""
            UPDATE write_seed_merges
            SET {col} = CASE
                WHEN {col} LIKE 'concept:%%' THEN substring({col} from 9)
                WHEN {col} LIKE 'entity:%%' THEN substring({col} from 8)
                WHEN {col} LIKE 'event:%%' THEN substring({col} from 7)
                WHEN {col} LIKE 'location:%%' THEN substring({col} from 10)
                ELSE {col} END
            WHERE {col} LIKE 'concept:%%' OR {col} LIKE 'entity:%%'
               OR {col} LIKE 'event:%%' OR {col} LIKE 'location:%%'
        """))

    for col in ("parent_seed_key", "child_seed_key"):
        conn.execute(sa.text(f"""
            UPDATE write_seed_routes
            SET {col} = CASE
                WHEN {col} LIKE 'concept:%%' THEN substring({col} from 9)
                WHEN {col} LIKE 'entity:%%' THEN substring({col} from 8)
                WHEN {col} LIKE 'event:%%' THEN substring({col} from 7)
                WHEN {col} LIKE 'location:%%' THEN substring({col} from 10)
                ELSE {col} END
            WHERE {col} LIKE 'concept:%%' OR {col} LIKE 'entity:%%'
               OR {col} LIKE 'event:%%' OR {col} LIKE 'location:%%'
        """))

    # 11. write_seeds.merged_into_key and promoted_node_key
    conn.execute(sa.text("""
        UPDATE write_seeds
        SET merged_into_key = CASE
            WHEN merged_into_key LIKE 'concept:%%' THEN substring(merged_into_key from 9)
            WHEN merged_into_key LIKE 'entity:%%' THEN substring(merged_into_key from 8)
            WHEN merged_into_key LIKE 'event:%%' THEN substring(merged_into_key from 7)
            WHEN merged_into_key LIKE 'location:%%' THEN substring(merged_into_key from 10)
            ELSE merged_into_key END
        WHERE merged_into_key LIKE 'concept:%%' OR merged_into_key LIKE 'entity:%%'
           OR merged_into_key LIKE 'event:%%' OR merged_into_key LIKE 'location:%%'
    """))

    conn.execute(sa.text("""
        UPDATE write_seeds
        SET promoted_node_key = CASE
            WHEN promoted_node_key LIKE 'concept:%%' THEN substring(promoted_node_key from 9)
            WHEN promoted_node_key LIKE 'entity:%%' THEN substring(promoted_node_key from 8)
            WHEN promoted_node_key LIKE 'event:%%' THEN substring(promoted_node_key from 7)
            WHEN promoted_node_key LIKE 'location:%%' THEN substring(promoted_node_key from 10)
            ELSE promoted_node_key END
        WHERE promoted_node_key LIKE 'concept:%%' OR promoted_node_key LIKE 'entity:%%'
           OR promoted_node_key LIKE 'event:%%' OR promoted_node_key LIKE 'location:%%'
    """))

    # 12. write_nodes.parent_key and source_concept_key
    conn.execute(sa.text("""
        UPDATE write_nodes
        SET parent_key = CASE
            WHEN parent_key LIKE 'concept:%%' THEN substring(parent_key from 9)
            WHEN parent_key LIKE 'entity:%%' THEN substring(parent_key from 8)
            WHEN parent_key LIKE 'event:%%' THEN substring(parent_key from 7)
            WHEN parent_key LIKE 'location:%%' THEN substring(parent_key from 10)
            ELSE parent_key END
        WHERE parent_key LIKE 'concept:%%' OR parent_key LIKE 'entity:%%'
           OR parent_key LIKE 'event:%%' OR parent_key LIKE 'location:%%'
    """))

    conn.execute(sa.text("""
        UPDATE write_nodes
        SET source_concept_key = CASE
            WHEN source_concept_key LIKE 'concept:%%' THEN substring(source_concept_key from 9)
            WHEN source_concept_key LIKE 'entity:%%' THEN substring(source_concept_key from 8)
            WHEN source_concept_key LIKE 'event:%%' THEN substring(source_concept_key from 7)
            WHEN source_concept_key LIKE 'location:%%' THEN substring(source_concept_key from 10)
            ELSE source_concept_key END
        WHERE source_concept_key LIKE 'concept:%%' OR source_concept_key LIKE 'entity:%%'
           OR source_concept_key LIKE 'event:%%' OR source_concept_key LIKE 'location:%%'
    """))

    # 13. write_node_versions.node_key
    conn.execute(sa.text("""
        UPDATE write_node_versions
        SET node_key = CASE
            WHEN node_key LIKE 'concept:%%' THEN substring(node_key from 9)
            WHEN node_key LIKE 'entity:%%' THEN substring(node_key from 8)
            WHEN node_key LIKE 'event:%%' THEN substring(node_key from 7)
            WHEN node_key LIKE 'location:%%' THEN substring(node_key from 10)
            ELSE node_key END
        WHERE node_key LIKE 'concept:%%' OR node_key LIKE 'entity:%%'
           OR node_key LIKE 'event:%%' OR node_key LIKE 'location:%%'
    """))


def downgrade() -> None:
    raise NotImplementedError(
        "Cannot reverse type prefix stripping — type data was discarded"
    )
