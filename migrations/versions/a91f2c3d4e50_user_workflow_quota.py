"""add per-user workflow quota

Revision ID: a91f2c3d4e50
Revises: f6c719a34d10
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91f2c3d4e50"
down_revision: str | None = "f6c719a34d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("workflow_uses", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "users", sa.Column("workflow_limit", sa.Integer(), nullable=False, server_default="10")
    )


def downgrade() -> None:
    op.drop_column("users", "workflow_limit")
    op.drop_column("users", "workflow_uses")
