"""collapse_cross_type_to_related

Revision ID: aa6136a97700
Revises: 38859c06de60
Create Date: 2026-04-13 15:07:29.268891

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'aa6136a97700'
down_revision: Union[str, Sequence[str], None] = '38859c06de60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Set all base node types to 'concept'
    op.execute("""
        UPDATE nodes
        SET node_type = 'concept'
        WHERE node_type IN ('entity', 'event', 'location')
    """)

    # 2. Collapse cross_type edges into related
    op.execute("""
        UPDATE edges
        SET relationship_type = 'related'
        WHERE relationship_type = 'cross_type'
    """)


def downgrade() -> None:
    # Not reversible — type information is lost
    raise NotImplementedError("Cannot reverse node type collapse — original types were discarded")
