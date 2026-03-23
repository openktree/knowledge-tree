"""Add extraction_role to write_seed_facts.

Revision ID: w021
Revises: w020
"""

from alembic import op
import sqlalchemy as sa

revision = "w021"
down_revision = "w020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "write_seed_facts",
        sa.Column(
            "extraction_role",
            sa.String(30),
            nullable=False,
            server_default="mentioned",
        ),
    )


def downgrade() -> None:
    op.drop_column("write_seed_facts", "extraction_role")
