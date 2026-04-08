-- Dedup graph-db raw_sources rows that share a content_hash.
--
-- Why: graph-db.raw_sources used to carry UNIQUE(content_hash). When write-db
-- and graph-db drifted to different ids for the same hash, worker-sync wedged
-- forever on the secondary unique violation. The migration that ships with
-- this script drops the unique constraint, but pre-existing duplicate-hash
-- rows must still be merged so future inserts/updates land on a single row
-- per write-db source.
--
-- Run AFTER applying the migration that drops UNIQUE on content_hash. Run
-- BEFORE re-enabling worker-sync (or accept that sync will create one extra
-- canonical row per orphan during the catch-up pass — it will no longer
-- error).
--
-- Run via kubectl in prod:
--   kubectl exec -i -n knowledge-tree knowledge-tree-graph-db-0 -- \
--     psql -U postgres -d knowledge_tree < scripts/dedup_raw_source_hashes.sql
--
-- This transaction is NOT committed by default — review output, then COMMIT.

BEGIN;

-- ── Step 0: Inventory ───────────────────────────────────────────────
SELECT 'Duplicate content_hash groups:' AS label, count(*) AS cnt
FROM (
  SELECT content_hash
  FROM raw_sources
  GROUP BY content_hash
  HAVING count(*) > 1
) g;

SELECT 'Total rows in duplicate groups:' AS label, count(*) AS cnt
FROM raw_sources rs
WHERE EXISTS (
  SELECT 1 FROM raw_sources rs2
  WHERE rs2.content_hash = rs.content_hash AND rs2.id <> rs.id
);

-- ── Step 1: Pick the canonical id per duplicate hash ────────────────
-- Canonical = the row with the most fact_sources references; ties broken
-- by lowest id (deterministic). Materialized into a temp table so the
-- subsequent UPDATEs/DELETEs all agree.
CREATE TEMP TABLE _canonical_raw_sources ON COMMIT DROP AS
WITH dups AS (
  SELECT content_hash
  FROM raw_sources
  GROUP BY content_hash
  HAVING count(*) > 1
),
ranked AS (
  SELECT
    rs.id,
    rs.content_hash,
    COALESCE((SELECT count(*) FROM fact_sources fs WHERE fs.raw_source_id = rs.id), 0) AS fs_count,
    ROW_NUMBER() OVER (
      PARTITION BY rs.content_hash
      ORDER BY
        COALESCE((SELECT count(*) FROM fact_sources fs WHERE fs.raw_source_id = rs.id), 0) DESC,
        rs.id ASC
    ) AS rn
  FROM raw_sources rs
  JOIN dups USING (content_hash)
)
SELECT content_hash, id AS canonical_id
FROM ranked
WHERE rn = 1;

SELECT 'Canonical rows chosen:' AS label, count(*) AS cnt FROM _canonical_raw_sources;

-- ── Step 2: Repoint fact_sources from losers → canonical ─────────────
-- Step 2a: drop fact_sources rows that would collide with an existing
-- (fact_id, canonical raw_source_id) pair after the repoint.
DELETE FROM fact_sources fs
USING raw_sources rs, _canonical_raw_sources c
WHERE fs.raw_source_id = rs.id
  AND rs.content_hash = c.content_hash
  AND rs.id <> c.canonical_id
  AND EXISTS (
    SELECT 1 FROM fact_sources existing
    WHERE existing.fact_id = fs.fact_id
      AND existing.raw_source_id = c.canonical_id
  );

-- Step 2b: repoint the rest.
UPDATE fact_sources fs
SET raw_source_id = c.canonical_id
FROM raw_sources rs, _canonical_raw_sources c
WHERE fs.raw_source_id = rs.id
  AND rs.content_hash = c.content_hash
  AND rs.id <> c.canonical_id;

-- ── Step 3: Repoint prohibited_chunks the same way ──────────────────
DELETE FROM prohibited_chunks pc
USING raw_sources rs, _canonical_raw_sources c
WHERE pc.raw_source_id = rs.id
  AND rs.content_hash = c.content_hash
  AND rs.id <> c.canonical_id
  AND EXISTS (
    SELECT 1 FROM prohibited_chunks existing
    WHERE existing.raw_source_id = c.canonical_id
      AND existing.chunk_text = pc.chunk_text
  );

UPDATE prohibited_chunks pc
SET raw_source_id = c.canonical_id
FROM raw_sources rs, _canonical_raw_sources c
WHERE pc.raw_source_id = rs.id
  AND rs.content_hash = c.content_hash
  AND rs.id <> c.canonical_id;

-- ── Step 4: Delete the now-orphaned losing raw_sources rows ─────────
DELETE FROM raw_sources rs
USING _canonical_raw_sources c
WHERE rs.content_hash = c.content_hash
  AND rs.id <> c.canonical_id;

-- ── Step 5: Recalculate fact_count on the canonical rows ────────────
UPDATE raw_sources rs
SET fact_count = sub.cnt
FROM (
  SELECT raw_source_id, count(*) AS cnt
  FROM fact_sources
  GROUP BY raw_source_id
) sub
WHERE rs.id = sub.raw_source_id
  AND rs.fact_count <> sub.cnt;

UPDATE raw_sources rs
SET fact_count = 0
WHERE NOT EXISTS (SELECT 1 FROM fact_sources WHERE raw_source_id = rs.id)
  AND rs.fact_count <> 0;

-- ── Step 6: Verify no more duplicate-hash groups ────────────────────
SELECT 'Duplicate content_hash groups after dedup:' AS label, count(*) AS cnt
FROM (
  SELECT content_hash
  FROM raw_sources
  GROUP BY content_hash
  HAVING count(*) > 1
) g;

-- ============================================================
-- IMPORTANT: This transaction is NOT committed by default.
-- Review the output above, then run one of:
--   COMMIT;    -- apply the dedup
--   ROLLBACK;  -- discard and start over
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- AFTER COMMITTING the graph-db dedup above, clear the
-- write-db sync_failures and bump the watermark so the
-- sync worker stops replaying the poison row:
--
--   kubectl exec -i -n knowledge-tree knowledge-tree-write-db-0 -- \
--     psql -U postgres -d knowledge_tree_write <<'SQL'
--   BEGIN;
--   DELETE FROM sync_failures WHERE table_name = 'write_raw_sources';
--   UPDATE sync_watermarks
--      SET watermark = (SELECT max(updated_at) FROM write_raw_sources)
--    WHERE table_name = 'write_raw_sources';
--   SELECT * FROM sync_watermarks WHERE table_name = 'write_raw_sources';
--   COMMIT;
--   SQL
-- ────────────────────────────────────────────────────────────
