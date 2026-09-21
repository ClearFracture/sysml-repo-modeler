"""persist classified architecture inventory

Revision ID: 20260921_0003
Revises: 20260609_0002
Create Date: 2026-09-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260921_0003"
down_revision = "20260609_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_architecture",
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("analysis_runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "inventory",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("run_architecture")
