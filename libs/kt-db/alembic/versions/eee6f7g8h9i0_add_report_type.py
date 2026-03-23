"""Add report_type to research_reports.

Revision ID: eee6f7g8h9i0
Revises: ddd5e6f7g8h9
Create Date: 2026-03-17

"""

from alembic import op
import sqlalchemy as sa

revision = "eee6f7g8h9i0"
down_revision = "ddd5e6f7g8h9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_reports",
        sa.Column(
            "report_type",
            sa.String(30),
            nullable=False,
            server_default="research",
        ),
    )


def downgrade() -> None:
    op.drop_column("research_reports", "report_type")
