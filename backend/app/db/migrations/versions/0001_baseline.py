from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Existing P0 databases are stamped at this baseline before later revisions run.
    pass

def downgrade():
    pass
