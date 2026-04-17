"""graph type versioning plus read only plus migration runs

Revision ID: e3eb15f51d7c
Revises: 489643109ccd
Create Date: 2026-04-17 15:11:04.684302

Phase 1 of the graph-type versioning initiative:
- Adds graph_type_id/graph_type_version columns to graphs.
- Adds reserved Graph.config JSONB for future user-managed overrides.
- Adds read_only / read_only_reason columns for the migration + owner
  read-only gates.
- Creates graph_migration_runs audit table (populated in Phase 7).
- Backfills existing graphs: graph_type='v1' -> graph_type_id='default',
  graph_type_version=1.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e3eb15f51d7c"
down_revision: Union[str, Sequence[str], None] = "489643109ccd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "graphs",
        sa.Column(
            "graph_type_id",
            sa.String(length=64),
            nullable=False,
            server_default="default",
        ),
    )
    op.add_column(
        "graphs",
        sa.Column(
            "graph_type_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "graphs",
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "graphs",
        sa.Column(
            "read_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "graphs",
        sa.Column(
            "read_only_reason",
            sa.String(length=32),
            nullable=True,
        ),
    )

    # Backfill: legacy graph_type='v1' maps to the default type at v1.
    op.execute("UPDATE graphs SET graph_type_id = 'default', graph_type_version = 1 WHERE graph_type = 'v1'")

    op.create_table(
        "graph_migration_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "graph_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("graphs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("migration_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("workflow_run_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "graph_id",
            "migration_id",
            "to_version",
            name="uq_graph_migration_run",
        ),
    )
    op.create_index(
        "ix_graph_migration_runs_graph_id",
        "graph_migration_runs",
        ["graph_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_graph_migration_runs_graph_id", table_name="graph_migration_runs")
    op.drop_table("graph_migration_runs")
    op.drop_column("graphs", "read_only_reason")
    op.drop_column("graphs", "read_only")
    op.drop_column("graphs", "config")
    op.drop_column("graphs", "graph_type_version")
    op.drop_column("graphs", "graph_type_id")
