"""job company_name and relevance

Revision ID: 664f4d5c5689
Revises: 32494cc342c9
Create Date: 2026-09-29 16:39:34.408192

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '664f4d5c5689'
down_revision: Union[str, Sequence[str], None] = '32494cc342c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add as nullable, backfill from companies, then require it: existing rows would
    # otherwise violate NOT NULL the moment the column appears.
    op.add_column('jobs', sa.Column('company_name', sa.String(length=200), nullable=True))
    op.execute("UPDATE jobs SET company_name = c.name FROM companies c WHERE c.id = jobs.company_id")
    op.alter_column('jobs', 'company_name', nullable=False)

    op.add_column('jobs', sa.Column('relevance', sa.Enum('match', 'senior', 'non_tech', name='relevance', native_enum=False, create_constraint=False, length=32), nullable=True))
    op.create_check_constraint(
        op.f('ck_jobs_relevance'), 'jobs', "relevance IN ('match', 'senior', 'non_tech')"
    )
    op.add_column('jobs', sa.Column('relevance_reason', sa.Text(), nullable=True))
    op.create_index(op.f('ix_jobs_relevance'), 'jobs', ['relevance'], unique=False)

    # The list I actually look at: open, relevant jobs, newest first.
    op.execute("""
        CREATE VIEW job_matches AS
        SELECT id, company_name, title, location, url, posted_at, first_seen_at, relevance_reason
        FROM jobs
        WHERE relevance = 'match' AND closed_at IS NULL
        ORDER BY first_seen_at DESC, posted_at DESC NULLS LAST
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW job_matches")
    op.drop_constraint(op.f('ck_jobs_relevance'), 'jobs', type_='check')
    op.drop_index(op.f('ix_jobs_relevance'), table_name='jobs')
    op.drop_column('jobs', 'relevance_reason')
    op.drop_column('jobs', 'relevance')
    op.drop_column('jobs', 'company_name')
    # ### end Alembic commands ###
