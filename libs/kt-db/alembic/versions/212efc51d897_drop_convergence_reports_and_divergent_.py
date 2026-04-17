"""drop convergence_reports and divergent_claims

Revision ID: 212efc51d897
Revises: 489643109ccd
Create Date: 2026-04-17 17:29:09.860226

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '212efc51d897'
down_revision: Union[str, Sequence[str], None] = '489643109ccd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("divergent_claims")
    op.drop_table("convergence_reports")
    op.drop_column("nodes", "convergence_score")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "nodes",
        sa.Column("convergence_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_table(
        "convergence_reports",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "node_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("nodes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("convergence_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("converged_claims", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("recommended_content", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "divergent_claims",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "report_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("convergence_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("model_positions", sa.JSON(), nullable=True),
        sa.Column("divergence_type", sa.String(100), nullable=True),
        sa.Column("analysis", sa.Text(), nullable=True),
    )
