"""Skills: a person's own skills, and their choices about the built-in ones.

Revision ID: 0016_skills
Revises: 0015_plugins
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_skills"
down_revision = "0015_plugins"
branch_labels = depends_on = None


def upgrade():
    op.create_table("user_skills",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "name"),
        sa.CheckConstraint("char_length(name) BETWEEN 3 AND 40", name="name_length"))
    op.create_index("user_skills_user_idx", "user_skills", ["user_id", "created_at"])
    op.create_table("skill_settings",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("skill_name", sa.Text(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))


def downgrade():
    op.drop_table("skill_settings")
    op.drop_index("user_skills_user_idx", table_name="user_skills")
    op.drop_table("user_skills")
