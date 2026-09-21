"""initial schema

Revision ID: 20260608_0001
Revises:
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20260608_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _table_exists("sysml_backend_documents"):
        op.create_table(
            "sysml_backend_documents",
            sa.Column("name", sa.Text(), primary_key=True),
            sa.Column(
                "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    if not _table_exists("projects"):
        op.create_table(
            "projects",
            sa.Column("slug", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("workspace_path", sa.Text(), nullable=False),
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

    if not _table_exists("project_repositories"):
        op.create_table(
            "project_repositories",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "project_slug",
                sa.Text(),
                sa.ForeignKey("projects.slug", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("role", sa.Text(), nullable=False, server_default="unknown"),
            sa.Column("branch", sa.Text(), nullable=True),
            sa.Column("path", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("git", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
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
            sa.UniqueConstraint(
                "project_slug", "name", name="uq_project_repositories_project_name"
            ),
        )
    if not _index_exists(
        "project_repositories", "ix_project_repositories_project_slug"
    ):
        op.create_index(
            "ix_project_repositories_project_slug",
            "project_repositories",
            ["project_slug"],
        )

    if not _table_exists("analysis_runs"):
        op.create_table(
            "analysis_runs",
            sa.Column("run_id", sa.Text(), primary_key=True),
            sa.Column(
                "project_slug",
                sa.Text(),
                sa.ForeignKey("projects.slug", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("project_name", sa.Text(), nullable=False),
            sa.Column("trigger", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("repository_count", sa.Integer(), nullable=False),
            sa.Column("changed_count", sa.Integer(), nullable=False),
            sa.Column("unchanged_count", sa.Integer(), nullable=False),
            sa.Column(
                "repositories", postgresql.JSONB(astext_type=sa.Text()), nullable=False
            ),
            sa.Column("opencode_session_id", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
    if not _index_exists("analysis_runs", "ix_analysis_runs_project_slug"):
        op.create_index(
            "ix_analysis_runs_project_slug", "analysis_runs", ["project_slug"]
        )
    if not _index_exists("analysis_runs", "ix_analysis_runs_started_at"):
        op.create_index("ix_analysis_runs_started_at", "analysis_runs", ["started_at"])

    if not _table_exists("run_artifacts"):
        op.create_table(
            "run_artifacts",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "run_id",
                sa.Text(),
                sa.ForeignKey("analysis_runs.run_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("pass_id", sa.Text(), nullable=False),
            sa.Column("suite_model", sa.Text(), nullable=False),
            sa.Column(
                "suite_evidence",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column("unresolved_services", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("run_id", "pass_id", name="uq_run_artifacts_run_pass"),
        )
    if not _index_exists("run_artifacts", "ix_run_artifacts_run_id"):
        op.create_index("ix_run_artifacts_run_id", "run_artifacts", ["run_id"])

    if not _table_exists("run_events"):
        op.create_table(
            "run_events",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("phase", sa.Text(), nullable=False),
            sa.Column("level", sa.Text(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("entity", sa.Text(), nullable=True),
            sa.Column("reasoning_summary", sa.Text(), nullable=True),
            sa.Column(
                "evidence_refs",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column("confidence", sa.Float(), nullable=True),
        )
    if not _index_exists("run_events", "ix_run_events_run_id"):
        op.create_index("ix_run_events_run_id", "run_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_run_artifacts_run_id", table_name="run_artifacts")
    op.drop_table("run_artifacts")
    op.drop_index("ix_analysis_runs_started_at", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_project_slug", table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index(
        "ix_project_repositories_project_slug", table_name="project_repositories"
    )
    op.drop_table("project_repositories")
    op.drop_table("projects")
    op.drop_table("sysml_backend_documents")


def _table_exists(table_name: str) -> bool:
    if context.is_offline_mode():
        return False
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    if context.is_offline_mode() or not _table_exists(table_name):
        return False
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)
