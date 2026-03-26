"""Add synthesis document tables and node visibility.

Revision ID: zzae
Revises: hhh9i0j1k2l3
Create Date: 2026-03-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "zzae"
down_revision = "hhh9i0j1k2l3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add visibility and creator_id to nodes
    op.add_column(
        "nodes",
        sa.Column("visibility", sa.String(20), server_default="public", nullable=False),
    )
    op.add_column(
        "nodes",
        sa.Column("creator_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )

    # Synthesis sentences
    op.create_table(
        "synthesis_sentences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "synthesis_node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("nodes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("sentence_text", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # Sentence-fact links (embedding distance)
    op.create_table(
        "sentence_facts",
        sa.Column(
            "sentence_id",
            UUID(as_uuid=True),
            sa.ForeignKey("synthesis_sentences.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "fact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("embedding_distance", sa.Float, nullable=False),
    )

    # Sentence-node links (text matching)
    op.create_table(
        "sentence_node_links",
        sa.Column(
            "sentence_id",
            UUID(as_uuid=True),
            sa.ForeignKey("synthesis_sentences.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("nodes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("link_type", sa.String(20), nullable=False),
    )

    # Supersynthesis -> synthesis child links
    op.create_table(
        "synthesis_children",
        sa.Column(
            "supersynthesis_node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("nodes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "synthesis_node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("nodes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("synthesis_children")
    op.drop_table("sentence_node_links")
    op.drop_table("sentence_facts")
    op.drop_table("synthesis_sentences")
    op.drop_column("nodes", "creator_id")
    op.drop_column("nodes", "visibility")
