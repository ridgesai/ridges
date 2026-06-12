"""Add trigger to create competition row on new set_id in evaluation_sets

Revision ID: e7f3a1b2c905
Revises: 353d6b475738
Create Date: 2026-06-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e7f3a1b2c905"
down_revision: Union[str, Sequence[str], None] = "353d6b475738"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION create_competition_for_new_set_id()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO competitions (set_id, start_date)
            VALUES (NEW.set_id, NOW())
            ON CONFLICT (set_id) DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_evaluation_sets_new_set_id
        AFTER INSERT ON evaluation_sets
        FOR EACH ROW
        EXECUTE FUNCTION create_competition_for_new_set_id();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_evaluation_sets_new_set_id ON evaluation_sets;")
    op.execute("DROP FUNCTION IF EXISTS create_competition_for_new_set_id();")
