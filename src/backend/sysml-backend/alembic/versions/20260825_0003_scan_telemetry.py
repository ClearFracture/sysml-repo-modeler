"""add scan telemetry export metadata

Revision ID: 20260825_0003
Revises: 20260609_0002
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260825_0003"
down_revision = "20260609_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column(
            "telemetry_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("telemetry_export_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("telemetry_export_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("telemetry_exported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("analysis_runs", "telemetry_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("analysis_runs", "telemetry_exported_at")
    op.drop_column("analysis_runs", "telemetry_export_status")
    op.drop_column("analysis_runs", "telemetry_export_path")
    op.drop_column("analysis_runs", "telemetry_enabled")
