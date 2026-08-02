"""add approvals table

Durable, DB-backed human-in-the-loop spend gate. The SSE stream inserts a
`pending` row and polls it; POST /approve flips the row in a separate request,
so the await and the resolve no longer have to run in the same process. See the
Approval model in app/models.py for the ownership/authorization model.

Idempotent: guarded so a re-run (or a table already built by init_db's
create_all on the SQLite dev path) is a no-op instead of an error.

Revision ID: 41234027fbea
Revises: 1132c99cc5af
Create Date: 2026-08-02 21:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41234027fbea'
down_revision: Union[str, Sequence[str], None] = '1132c99cc5af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Idempotent guard: only create the table (and its index) if absent. The
    # SQLite dev path builds the schema from ORM metadata via init_db's
    # create_all, so this migration must not fail when the table is already
    # there — and a re-run against prod must be safe too.
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if 'approvals' in existing:
        return

    op.create_table(
        'approvals',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('scope', sa.String(length=64), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scope', 'run_id', name='uq_approvals_scope_run'),
    )
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approvals_scope'), ['scope'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if 'approvals' not in existing:
        return

    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_approvals_scope'))

    op.drop_table('approvals')
