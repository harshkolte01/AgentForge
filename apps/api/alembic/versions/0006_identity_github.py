"""Durable GitHub identity, sessions, exact approvals and operation ledger."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import Text
revision = "0006_identity_github"
down_revision = "0005_phase4_coding"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('auth_sessions',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_auth_sessions_token_hash'))
    )
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('oauth_transactions',
    sa.Column('state_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('binding_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('verifier_encrypted', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_transactions')),
    sa.UniqueConstraint('state_hash', name=op.f('uq_oauth_transactions_state_hash'))
    )
    op.create_table('github_credentials',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('access_encrypted', sa.Text(), nullable=False),
    sa.Column('refresh_encrypted', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refresh_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_github_credentials_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_github_credentials')),
    sa.UniqueConstraint('user_id', name=op.f('uq_github_credentials_user_id'))
    )
    op.create_table('github_installations',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=False),
    sa.Column('account_login', sa.Text(), nullable=False),
    sa.Column('permissions_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('repository_selection', sa.Text(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_github_installations'))
    )
    op.create_table('github_repository_grants',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('installation_id', sa.BigInteger(), nullable=False),
    sa.Column('repository_id', sa.BigInteger(), nullable=False),
    sa.Column('full_name', sa.Text(), nullable=False),
    sa.Column('permissions_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['installation_id'], ['github_installations.id'], name=op.f('fk_github_repository_grants_installation_id_github_installations')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_github_repository_grants_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_github_repository_grants')),
    sa.UniqueConstraint('user_id', 'repository_id', name=op.f('uq_github_repository_grants_user_id'))
    )
    op.create_table('github_webhook_deliveries',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('event', sa.Text(), nullable=False),
    sa.Column('body_sha256', sa.CHAR(length=64), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_github_webhook_deliveries'))
    )
    op.create_table('approvals',
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('action_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('action_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('execution_token', sa.Text(), nullable=True),
    sa.Column('execution_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("state IN ('PENDING','APPROVED','DENIED','EXPIRED','EXECUTING','CONSUMED','INVALIDATED')", name=op.f('ck_approvals_state')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_approvals_task_id_tasks')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_approvals_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_approvals')),
    sa.UniqueConstraint('task_id', 'action_hash', name=op.f('uq_approvals_task_id'))
    )
    op.create_index('approvals_user_state_idx', 'approvals', ['user_id', 'state'], unique=False)
    op.create_table('external_operations',
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('approval_id', sa.Uuid(), nullable=False),
    sa.Column('operation_key', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('input_sha256', sa.CHAR(length=64), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('request_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('response_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('PREPARED','SENT','CONFIRMED','UNKNOWN','FAILED')", name=op.f('ck_external_operations_status')),
    sa.ForeignKeyConstraint(['approval_id'], ['approvals.id'], name=op.f('fk_external_operations_approval_id_approvals')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_external_operations_task_id_tasks')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_external_operations')),
    sa.UniqueConstraint('operation_key', name=op.f('uq_external_operations_operation_key'))
    )
    op.execute("""CREATE FUNCTION agentforge_approval_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.id IS DISTINCT FROM OLD.id OR NEW.task_id IS DISTINCT FROM OLD.task_id OR
        NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.action_hash IS DISTINCT FROM OLD.action_hash OR
        NEW.action_json IS DISTINCT FROM OLD.action_json OR NEW.expires_at IS DISTINCT FROM OLD.expires_at OR
        NEW.created_at IS DISTINCT FROM OLD.created_at
        THEN RAISE EXCEPTION 'approval action is immutable'; END IF; RETURN NEW; END; $$""")
    op.execute("CREATE TRIGGER approvals_immutable BEFORE UPDATE ON approvals FOR EACH ROW EXECUTE FUNCTION agentforge_approval_immutable()")
    op.execute("""CREATE FUNCTION agentforge_external_operation_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.id IS DISTINCT FROM OLD.id OR NEW.task_id IS DISTINCT FROM OLD.task_id OR
        NEW.approval_id IS DISTINCT FROM OLD.approval_id OR NEW.operation_key IS DISTINCT FROM OLD.operation_key OR
        NEW.kind IS DISTINCT FROM OLD.kind OR NEW.input_sha256 IS DISTINCT FROM OLD.input_sha256 OR
        NEW.request_json IS DISTINCT FROM OLD.request_json OR NEW.created_at IS DISTINCT FROM OLD.created_at
        THEN RAISE EXCEPTION 'external operation intent is immutable'; END IF; RETURN NEW; END; $$""")
    op.execute("CREATE TRIGGER external_operations_immutable BEFORE UPDATE ON external_operations FOR EACH ROW EXECUTE FUNCTION agentforge_external_operation_immutable()")

def downgrade():
    op.drop_table('external_operations')
    op.drop_table('approvals')
    op.drop_table('github_webhook_deliveries')
    op.drop_table('github_repository_grants')
    op.drop_table('github_installations')
    op.drop_table('github_credentials')
    op.drop_table('oauth_transactions')
    op.drop_table('auth_sessions')
    op.execute("DROP FUNCTION agentforge_approval_immutable()")
    op.execute("DROP FUNCTION agentforge_external_operation_immutable()")
