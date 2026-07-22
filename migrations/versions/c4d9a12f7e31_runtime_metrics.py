"""add batched runtime metrics

Revision ID: c4d9a12f7e31
Revises: 7b835d0aef2b
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d9a12f7e31"
down_revision: str | None = "7b835d0aef2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_metrics_trace_id", "runtime_metrics", ["trace_id"])
    op.create_index("ix_runtime_metrics_path", "runtime_metrics", ["path"])
    op.create_index("ix_runtime_metrics_status_code", "runtime_metrics", ["status_code"])
    op.create_index("ix_runtime_metrics_created_at", "runtime_metrics", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_metrics_created_at", table_name="runtime_metrics")
    op.drop_index("ix_runtime_metrics_status_code", table_name="runtime_metrics")
    op.drop_index("ix_runtime_metrics_path", table_name="runtime_metrics")
    op.drop_index("ix_runtime_metrics_trace_id", table_name="runtime_metrics")
    op.drop_table("runtime_metrics")
