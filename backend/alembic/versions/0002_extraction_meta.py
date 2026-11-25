"""documents.used_ocr and documents.extraction_warnings (Stage 4)

Revision ID: 0002_extraction_meta
Revises: 0001_initial
Create Date: 2026-11-25
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_extraction_meta"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("used_ocr", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "documents",
        sa.Column("extraction_warnings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "extraction_warnings")
    op.drop_column("documents", "used_ocr")
