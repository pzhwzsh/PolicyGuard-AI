"""add resource ownership for tenant isolation

Revision ID: e2a6e34cf91b
Revises: c4d9a12f7e31
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a6e34cf91b"
down_revision: str | None = "c4d9a12f7e31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_ownership",
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("resource_type", "resource_id", "tenant_id"),
    )
    op.create_index(
        "ix_resource_ownership_tenant_id", "resource_ownership", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_resource_ownership_tenant_id", table_name="resource_ownership")
    op.drop_table("resource_ownership")
