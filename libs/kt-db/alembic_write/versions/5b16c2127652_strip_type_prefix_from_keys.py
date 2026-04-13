"""strip_type_prefix_from_keys

Revision ID: 5b16c2127652
Revises: 750a40fc98be
Create Date: 2026-04-13 15:07:24.149149

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b16c2127652'
down_revision: Union[str, None] = '750a40fc98be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BASE_NODE_TYPES = ("concept", "entity", "event", "location")


def _strip_keys_sql(table: str, key_col: str = "key") -> str:
    """Generate SQL to strip type prefix from keys for base node types.

    Transforms 'concept:artificial-intelligence' -> 'artificial-intelligence'.
    Only touches base types — composite types (synthesis, etc.) are unchanged.
    """
    # Build a CASE expression that strips each known prefix
    cases = " ".join(
        f"WHEN {key_col} LIKE '{nt}:%' THEN substring({key_col} from {len(nt) + 2})"
        for nt in BASE_NODE_TYPES
    )
    return f"""
        UPDATE {table}
        SET {key_col} = CASE {cases} ELSE {key_col} END
        WHERE {key_col} LIKE 'concept:%'
           OR {key_col} LIKE 'entity:%'
           OR {key_col} LIKE 'event:%'
           OR {key_col} LIKE 'location:%'
    """


def upgrade() -> None:
    # 1. Strip type prefix from seed keys
    op.execute(_strip_keys_sql("write_seeds"))

    # 2. Strip type prefix from node keys
    op.execute(_strip_keys_sql("write_nodes"))

    # 3. Set all base node types to 'concept'
    for table in ("write_seeds", "write_nodes"):
        op.execute(f"""
            UPDATE {table}
            SET node_type = 'concept'
            WHERE node_type IN ('entity', 'event', 'location')
        """)

    # 4. Strip type prefix from edge keys and collapse cross_type -> related
    # Edge keys have format: "rel_type:node_key_a--node_key_b"
    # We need to strip type prefixes from node keys within edge keys
    # and rename cross_type to related
    op.execute("""
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
        WHERE key LIKE '%concept:%'
           OR key LIKE '%entity:%'
           OR key LIKE '%event:%'
           OR key LIKE '%location:%'
           OR key LIKE 'cross_type:%'
    """)

    # 5. Update edge relationship_type column
    op.execute("""
        UPDATE write_edges
        SET relationship_type = 'related'
        WHERE relationship_type = 'cross_type'
    """)

    # 6. Strip type prefix from dimension keys
    # Dimension keys: "node_key:model_slug:batch_index"
    for nt in BASE_NODE_TYPES:
        op.execute(f"""
            UPDATE write_dimensions
            SET key = substring(key from {len(nt) + 2})
            WHERE key LIKE '{nt}:%'
        """)

    # 7. Strip type prefix from write_node_counters node_key
    op.execute(_strip_keys_sql("write_node_counters", "node_key"))

    # 8. Strip from write_convergence_reports node_key
    op.execute(_strip_keys_sql("write_convergence_reports", "node_key"))


def downgrade() -> None:
    # Not reversible — type information is lost
    raise NotImplementedError("Cannot reverse type prefix stripping — type data was discarded")
