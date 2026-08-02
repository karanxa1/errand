"""add mcp_servers + mcp_oauth_sessions

The user's own MCP servers (custom tool providers) and the OAuth credentials for
them. See app/models.py McpServer / McpOAuthSession for the ownership model, and
app/mcp/ for the feature.

Both credential columns (mcp_servers.secret_headers, mcp_oauth_sessions.tokens and
.client_info) hold values ENCRYPTED at the application layer (app/mcp/crypto.py),
which is why they are Text rather than JSON.

Idempotent per table: guarded so a re-run — or a database already built by
init_db's create_all on the SQLite dev path — is a no-op rather than an error, the
same way the approvals migration is. The two tables are guarded SEPARATELY so a
partially-applied state still converges.

Revision ID: 8a1f4c2d9e70
Revises: 41234027fbea
Create Date: 2026-08-03 03:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a1f4c2d9e70'
down_revision: Union[str, Sequence[str], None] = '41234027fbea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if 'mcp_servers' not in existing:
        op.create_table(
            'mcp_servers',
            sa.Column('id', sa.String(length=32), nullable=False),
            sa.Column('user_id', sa.String(length=32), nullable=False),
            sa.Column('name', sa.String(length=64), nullable=False),
            sa.Column('config', sa.JSON(), nullable=False),
            sa.Column('auth_mode', sa.String(length=16), nullable=False),
            # Encrypted at the application layer, hence Text and not JSON.
            sa.Column('secret_headers', sa.Text(), nullable=True),
            sa.Column('enabled', sa.Boolean(), nullable=False),
            sa.Column('tools_json', sa.JSON(), nullable=True),
            sa.Column('tools_updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('last_status', sa.String(length=16), nullable=False),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            # A name is the human half of a namespaced tool id, so it is unique
            # PER USER — two people may both name a server "github".
            sa.UniqueConstraint('user_id', 'name', name='uq_mcp_servers_user_name'),
        )
        with op.batch_alter_table('mcp_servers', schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f('ix_mcp_servers_user_id'), ['user_id'], unique=False
            )

    if 'mcp_oauth_sessions' not in existing:
        op.create_table(
            'mcp_oauth_sessions',
            sa.Column('id', sa.String(length=32), nullable=False),
            sa.Column('server_id', sa.String(length=32), nullable=False),
            sa.Column('server_url', sa.Text(), nullable=False),
            # Both encrypted at the application layer.
            sa.Column('client_info', sa.Text(), nullable=True),
            sa.Column('tokens', sa.Text(), nullable=True),
            # Unique: it is how one in-flight attempt is told from another, so two
            # attempts must never share a value.
            sa.Column('state', sa.String(length=128), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ['server_id'], ['mcp_servers.id'], ondelete='CASCADE'
            ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('state'),
        )
        with op.batch_alter_table('mcp_oauth_sessions', schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f('ix_mcp_oauth_sessions_server_id'),
                ['server_id'],
                unique=False,
            )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if 'mcp_oauth_sessions' in existing:
        with op.batch_alter_table('mcp_oauth_sessions', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_mcp_oauth_sessions_server_id'))
        op.drop_table('mcp_oauth_sessions')

    if 'mcp_servers' in existing:
        with op.batch_alter_table('mcp_servers', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_mcp_servers_user_id'))
        op.drop_table('mcp_servers')
