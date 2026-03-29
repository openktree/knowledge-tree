"""Add RBAC roles and user_roles tables with default roles.

Revision ID: zzag
Revises: zzaf
Create Date: 2026-03-28
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "zzag"
down_revision = "zzaf"
branch_labels = None
depends_on = None

# Default permission sets
_ALL_PERMISSIONS = {
    "nodes.read": True,
    "nodes.write": True,
    "nodes.delete": True,
    "facts.read": True,
    "facts.write": True,
    "edges.read": True,
    "edges.write": True,
    "syntheses.create": True,
    "syntheses.read": True,
    "sources.ingest": True,
    "sources.read": True,
    "research.create": True,
    "research.read": True,
    "admin.users": True,
    "admin.settings": True,
    "admin.usage": True,
    "admin.roles": True,
    "plugins.manage": True,
}

_EDITOR_PERMISSIONS = {
    "nodes.read": True,
    "nodes.write": True,
    "facts.read": True,
    "facts.write": True,
    "edges.read": True,
    "edges.write": True,
    "syntheses.create": True,
    "syntheses.read": True,
    "sources.ingest": True,
    "sources.read": True,
    "research.create": True,
    "research.read": True,
}

_VIEWER_PERMISSIONS = {
    "nodes.read": True,
    "facts.read": True,
    "edges.read": True,
    "syntheses.read": True,
    "sources.read": True,
    "research.read": True,
}

# Fixed UUIDs for system roles (deterministic so migrations are idempotent)
ADMIN_ROLE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
EDITOR_ROLE_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
VIEWER_ROLE_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")


def upgrade() -> None:
    # Create roles table
    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("permissions", JSONB, nullable=False, server_default="{}"),
        sa.Column("is_system", sa.Boolean, server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    # Create user_roles table
    op.create_table(
        "user_roles",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    # Seed default system roles
    roles_table = sa.table(
        "roles",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("permissions", JSONB),
        sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(
        roles_table,
        [
            {"id": ADMIN_ROLE_ID, "name": "admin", "permissions": _ALL_PERMISSIONS, "is_system": True},
            {"id": EDITOR_ROLE_ID, "name": "editor", "permissions": _EDITOR_PERMISSIONS, "is_system": True},
            {"id": VIEWER_ROLE_ID, "name": "viewer", "permissions": _VIEWER_PERMISSIONS, "is_system": True},
        ],
    )

    # Assign admin role to all existing superusers
    op.execute(
        sa.text(
            """
            INSERT INTO user_roles (user_id, role_id)
            SELECT id, :admin_role_id FROM "user" WHERE is_superuser = true
            ON CONFLICT DO NOTHING
            """
        ).bindparams(admin_role_id=ADMIN_ROLE_ID)
    )

    # Assign editor role to all existing non-superusers
    op.execute(
        sa.text(
            """
            INSERT INTO user_roles (user_id, role_id)
            SELECT id, :editor_role_id FROM "user" WHERE is_superuser = false
            ON CONFLICT DO NOTHING
            """
        ).bindparams(editor_role_id=EDITOR_ROLE_ID)
    )


def downgrade() -> None:
    op.drop_table("user_roles")
    op.drop_table("roles")
