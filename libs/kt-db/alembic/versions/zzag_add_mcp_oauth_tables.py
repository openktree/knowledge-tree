"""Add MCP OAuth 2.1 tables.

Revision ID: zzag
Revises: zzaf
Create Date: 2026-03-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "zzag"
down_revision: str | None = "zzaf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(200), primary_key=True),
        sa.Column("client_secret", sa.String(200), nullable=True),
        sa.Column("client_id_issued_at", sa.Integer, nullable=True),
        sa.Column("client_secret_expires_at", sa.Integer, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("code", sa.String(200), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(200),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("redirect_uri", sa.Text, nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean, server_default="true"),
        sa.Column("scopes", postgresql.JSONB, server_default="[]"),
        sa.Column("code_challenge", sa.String(200), nullable=False),
        sa.Column("resource", sa.String(500), nullable=True),
        sa.Column("state", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("csrf_token", sa.String(100), nullable=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "oauth_access_tokens",
        sa.Column("token", sa.String(200), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(200),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scopes", postgresql.JSONB, server_default="[]"),
        sa.Column("expires_at", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("token", sa.String(200), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(200),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scopes", postgresql.JSONB, server_default="[]"),
        sa.Column("expires_at", sa.Integer, nullable=True),
        sa.Column("access_token", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_oauth_refresh_tokens_access_token",
        "oauth_refresh_tokens",
        ["access_token"],
    )


def downgrade() -> None:
    op.drop_index("ix_oauth_refresh_tokens_access_token", table_name="oauth_refresh_tokens")
    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_access_tokens")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_clients")
