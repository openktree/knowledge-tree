"""Add seed routing pipes and phonetic matching

Revision ID: w016
Revises: w015
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w016"
down_revision = "w015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable fuzzystrmatch for dmetaphone()
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")

    # Add phonetic_code and context_hash to write_seeds
    op.add_column("write_seeds", sa.Column("phonetic_code", sa.String(50), nullable=True))
    op.add_column("write_seeds", sa.Column("context_hash", sa.String(64), nullable=True))
    op.create_index("ix_write_seeds_phonetic_code", "write_seeds", ["phonetic_code"])

    # Create write_seed_routes table
    op.create_table(
        "write_seed_routes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("parent_seed_key", sa.String(500), nullable=False),
        sa.Column("child_seed_key", sa.String(500), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("parent_seed_key", "child_seed_key", name="uq_wsr_parent_child"),
    )
    op.create_index("ix_wsr_parent_seed_key", "write_seed_routes", ["parent_seed_key"])
    op.create_index("ix_wsr_child_seed_key", "write_seed_routes", ["child_seed_key"])


def downgrade() -> None:
    op.drop_table("write_seed_routes")
    op.drop_index("ix_write_seeds_phonetic_code", table_name="write_seeds")
    op.drop_column("write_seeds", "context_hash")
    op.drop_column("write_seeds", "phonetic_code")
