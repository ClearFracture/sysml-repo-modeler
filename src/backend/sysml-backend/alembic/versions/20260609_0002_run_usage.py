"""add run usage metadata

Revision ID: 20260609_0002
Revises: 20260608_0001
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260609_0002"
down_revision = "20260608_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("opencode_cost", sa.Float(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("output_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column(
            "opencode_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("analysis_runs", "opencode_usage", server_default=None)


def downgrade() -> None:
    op.drop_column("analysis_runs", "opencode_usage")
    op.drop_column("analysis_runs", "total_tokens")
    op.drop_column("analysis_runs", "output_tokens")
    op.drop_column("analysis_runs", "input_tokens")
    op.drop_column("analysis_runs", "opencode_cost")
