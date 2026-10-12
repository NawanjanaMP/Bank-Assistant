"""usage ledger and audit trail

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("session_id", sa.String(64), nullable=False, index=True),
        sa.Column("subject", sa.String(128), nullable=False, index=True),
        sa.Column("agent", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Float, nullable=False, server_default="0"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False, index=True),
        sa.Column("subject", sa.String(128), nullable=False, index=True),
        sa.Column("session_id", sa.String(64), nullable=True, index=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
    )
    # Append-only audit trail: the runtime role may insert and read, never modify or delete.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bankai_app') THEN
            REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM bankai_app;
            GRANT SELECT, INSERT ON audit_events, llm_usage TO bankai_app;
          END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("llm_usage")
