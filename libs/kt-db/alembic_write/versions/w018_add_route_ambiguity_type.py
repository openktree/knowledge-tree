"""Add ambiguity_type to write_seed_routes.

Revision ID: w018
Revises: w017
"""

from alembic import op
import sqlalchemy as sa

revision = "w018"
down_revision = "w017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_seed_routes",
        sa.Column(
            "ambiguity_type",
            sa.String(20),
            nullable=False,
            server_default="text",
        ),
    )


def downgrade() -> None:
    op.drop_column("write_seed_routes", "ambiguity_type")
