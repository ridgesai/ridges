"""Add competition work cursors.

Revision ID: 06f4bede4ef6
Revises: 6c63d9349cfc
Create Date: 2026-08-21 07:41:54.786534

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "06f4bede4ef6"
down_revision: Union[str, Sequence[str], None] = "6c63d9349cfc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cursor_table = op.create_table(
        "competition_work_cursors",
        sa.Column("family", sa.Text(), nullable=False),
        sa.Column("last_served_set_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "family IN ('screener_1', 'screener_2', 'validator', 'pre_screening_judge', 'approval_judge')",
            name="ck_competition_work_cursors_family",
        ),
        sa.PrimaryKeyConstraint("family"),
    )
    op.bulk_insert(
        cursor_table,
        [
            {"family": "screener_1", "last_served_set_id": None},
            {"family": "screener_2", "last_served_set_id": None},
            {"family": "validator", "last_served_set_id": None},
            {"family": "pre_screening_judge", "last_served_set_id": None},
            {"family": "approval_judge", "last_served_set_id": None},
        ],
    )


def downgrade() -> None:
    op.drop_table("competition_work_cursors")
