"""drop write_convergence_reports and write_divergent_claims

Revision ID: c5e4c338b952
Revises: 5f38f1bac3ef
Create Date: 2026-04-17 17:29:02.519706

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e4c338b952'
down_revision: Union[str, None] = '5f38f1bac3ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_write_divergent_claims_node_key", table_name="write_divergent_claims")
    op.drop_index("ix_write_divergent_claims_updated_at", table_name="write_divergent_claims")
    op.drop_table("write_divergent_claims")
    op.drop_index("ix_write_convergence_updated_at", table_name="write_convergence_reports")
    op.drop_table("write_convergence_reports")


def downgrade() -> None:
    op.create_table(
        "write_convergence_reports",
        sa.Column("node_key", sa.String(500), primary_key=True),
        sa.Column("convergence_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("converged_claims", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("recommended_content", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_write_convergence_updated_at", "write_convergence_reports", ["updated_at"]
    )
    op.create_table(
        "write_divergent_claims",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("node_key", sa.String(500), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("model_positions", sa.JSON(), nullable=True),
        sa.Column("divergence_type", sa.String(100), nullable=True),
        sa.Column("analysis", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_write_divergent_claims_node_key", "write_divergent_claims", ["node_key"])
    op.create_index(
        "ix_write_divergent_claims_updated_at", "write_divergent_claims", ["updated_at"]
    )
