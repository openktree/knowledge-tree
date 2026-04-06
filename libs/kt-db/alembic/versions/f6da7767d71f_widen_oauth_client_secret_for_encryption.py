"""Widen oauth_clients.client_secret to TEXT for Fernet encryption.

Fernet ciphertext is ~2.4x the plaintext length plus overhead,
so the old VARCHAR(200) column is too narrow.

Revision ID: f6da7767d71f
Revises: a30d56a6720e
Create Date: 2026-04-06
"""

import sqlalchemy as sa
from alembic import op

revision = "f6da7767d71f"
down_revision = "a30d56a6720e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "oauth_clients",
        "client_secret",
        type_=sa.Text(),
        existing_type=sa.String(200),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "oauth_clients",
        "client_secret",
        type_=sa.String(200),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
