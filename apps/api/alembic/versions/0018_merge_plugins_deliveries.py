"""Join the plugins/skills line and the delegation/streaming/deliveries line of migrations.

Both lines grew from 0014 at the same time. Neither is rewritten, so a database that already applied either one upgrades by
applying the other; this revision changes no schema.

Revision ID: 0018_merge_plugins_deliveries
Revises: 0016_skills, 0017_deliveries
"""

revision = "0018_merge_plugins_deliveries"
down_revision = ("0016_skills", "0017_deliveries")
branch_labels = depends_on = None


def upgrade():
    pass


def downgrade():
    pass
