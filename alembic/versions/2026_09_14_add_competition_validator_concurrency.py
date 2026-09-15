from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39daf859ec77"
down_revision: Union[str, Sequence[str], None] = "b18d4c7f2e93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Former validator-side caps, backfilled once for competitions that already have a policy.
_LEGACY_HOTKEYS = (
    "5G8iwBWxPjCfu9Fc3jFP37j1Ax5KypDDmUPUSoS9aWAsSCGT",
    "5G3f2z52RVdSdjCftuW5t9WodCaEGik4zt3V8V9tpqqWSL62",
    "5GuRsre3hqm6WKWRCqVxXdM4UtGs457nDhPo9F5wvJ16Ys62",
    "5CAQAgXxoU3NPo1YG5VQKz4Ackh7goNx3TvPD9A6LoqA7FmJ",
    "5Eho9y6iF5aTdKS28Awn2pKTd4dFsJ2o3shGtj1vjnLiaKJ1",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "competition_validator_concurrency",
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("validator_hotkey", sa.Text(), nullable=False),
        sa.Column("max_concurrent_evaluation_runs", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("set_id", "validator_hotkey"),
        sa.ForeignKeyConstraint(["set_id"], ["competitions.set_id"], ondelete="CASCADE"),
        sa.CheckConstraint("max_concurrent_evaluation_runs > 0", name="ck_competition_validator_concurrency_positive"),
    )
    for hotkey in _LEGACY_HOTKEYS:
        op.execute(
            sa.text(
                "INSERT INTO competition_validator_concurrency "
                "(set_id, validator_hotkey, max_concurrent_evaluation_runs) "
                "SELECT set_id, :hotkey, 10 FROM competitions WHERE scoring_mode IS NOT NULL"
            ).bindparams(hotkey=hotkey)
        )
    op.drop_constraint("ck_competition_admin_events_operation", "competition_admin_events", type_="check")
    op.create_check_constraint(
        "ck_competition_admin_events_operation",
        "competition_admin_events",
        "operation IN ('state', 'policy', 'allocation', 'metadata', 'validator_concurrency')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM competition_admin_events WHERE operation = 'validator_concurrency'")
    op.drop_constraint("ck_competition_admin_events_operation", "competition_admin_events", type_="check")
    op.create_check_constraint(
        "ck_competition_admin_events_operation",
        "competition_admin_events",
        "operation IN ('state', 'policy', 'allocation', 'metadata')",
    )
    op.drop_table("competition_validator_concurrency")
