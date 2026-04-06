-- Repair phantom sources in graph-db.
--
-- Phantom sources are RawSource records with NULL raw_content created by the
-- sync engine's _sync_one_fact_source when it couldn't find a matching
-- content_hash in graph-db. These have random UUIDs, no content, and
-- incorrectly hold fact_source linkages that should point to the real source.
--
-- Run this AFTER deploying the sync engine fix (change #1) to prevent new
-- phantoms. Execute via:
--
--   kubectl exec -i -n knowledge-tree knowledge-tree-graph-db-1 -- \
--     psql -U postgres -d knowledge_tree < scripts/repair_phantom_sources.sql
--
-- Or paste into an interactive psql session inside a transaction.

BEGIN;

-- Step 0: Count phantoms before repair
SELECT 'Phantom sources before repair:' AS label, count(*) AS cnt
FROM raw_sources WHERE raw_content IS NULL;

-- Step 1a: Delete duplicate fact_source rows that would conflict when
-- reassigning from phantom to real source (the real source already has
-- a fact_source row for the same fact).
DELETE FROM fact_sources fs
USING raw_sources phantom
JOIN raw_sources real_src
  ON real_src.uri = phantom.uri
  AND real_src.raw_content IS NOT NULL
  AND real_src.id != phantom.id
WHERE phantom.raw_content IS NULL
  AND fs.raw_source_id = phantom.id
  AND EXISTS (
    SELECT 1 FROM fact_sources existing
    WHERE existing.fact_id = fs.fact_id
      AND existing.raw_source_id = real_src.id
  );

-- Step 1b: Same dedup for content_hash matches.
DELETE FROM fact_sources fs
USING raw_sources phantom
JOIN raw_sources real_src
  ON real_src.content_hash = phantom.content_hash
  AND real_src.raw_content IS NOT NULL
  AND real_src.id != phantom.id
WHERE phantom.raw_content IS NULL
  AND fs.raw_source_id = phantom.id
  AND EXISTS (
    SELECT 1 FROM fact_sources existing
    WHERE existing.fact_id = fs.fact_id
      AND existing.raw_source_id = real_src.id
  );

-- Step 2a: Reassign remaining fact_sources from phantom to real source (by URI).
UPDATE fact_sources fs
SET raw_source_id = real_src.id
FROM raw_sources phantom
JOIN raw_sources real_src
  ON real_src.uri = phantom.uri
  AND real_src.raw_content IS NOT NULL
  AND real_src.id != phantom.id
WHERE phantom.raw_content IS NULL
  AND fs.raw_source_id = phantom.id;

-- Step 2b: Reassign remaining phantoms by content_hash match.
UPDATE fact_sources fs
SET raw_source_id = real_src.id
FROM raw_sources phantom
JOIN raw_sources real_src
  ON real_src.content_hash = phantom.content_hash
  AND real_src.raw_content IS NOT NULL
  AND real_src.id != phantom.id
WHERE phantom.raw_content IS NULL
  AND fs.raw_source_id = phantom.id;

SELECT 'Fact sources on real sources:' AS label, count(*) AS cnt
FROM fact_sources fs
JOIN raw_sources rs ON rs.id = fs.raw_source_id
WHERE rs.raw_content IS NOT NULL;

-- Step 3: Recalculate fact_count on ALL sources (idempotent).
UPDATE raw_sources rs
SET fact_count = sub.cnt
FROM (
  SELECT raw_source_id, count(*) AS cnt
  FROM fact_sources
  GROUP BY raw_source_id
) sub
WHERE rs.id = sub.raw_source_id
  AND rs.fact_count != sub.cnt;

-- Also zero out sources that lost all their facts
UPDATE raw_sources rs
SET fact_count = 0
WHERE NOT EXISTS (SELECT 1 FROM fact_sources WHERE raw_source_id = rs.id)
  AND rs.fact_count != 0;

-- Step 4: Delete phantoms that are now orphaned (no fact_sources pointing to them).
DELETE FROM raw_sources
WHERE raw_content IS NULL
  AND NOT EXISTS (SELECT 1 FROM fact_sources WHERE raw_source_id = raw_sources.id);

-- Step 5: Verify
SELECT 'Phantom sources after repair:' AS label, count(*) AS cnt
FROM raw_sources WHERE raw_content IS NULL;

-- ============================================================
-- IMPORTANT: This transaction is NOT committed by default.
-- Review the output above, then run one of:
--   COMMIT;    -- to apply the changes
--   ROLLBACK;  -- to discard and start over
-- ============================================================
