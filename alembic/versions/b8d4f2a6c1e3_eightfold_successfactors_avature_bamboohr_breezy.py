"""eightfold, successfactors, avature, bamboohr, breezy sources

Revision ID: b8d4f2a6c1e3
Revises: a7c3e1f9b2d4
Create Date: 2026-11-02 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d4f2a6c1e3'
down_revision: Union[str, Sequence[str], None] = 'a7c3e1f9b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Hand-written: autogenerate doesn't detect CHECK constraint changes.
OLD = ('greenhouse', 'lever', 'ashby', 'workday', 'ukg', 'paylocity', 'oracle', 'cats', 'manual')
NEW = ('greenhouse', 'lever', 'ashby', 'workday', 'ukg', 'paylocity', 'oracle', 'cats',
       'eightfold', 'successfactors', 'avature', 'bamboohr', 'breezy', 'manual')
TABLES = ('companies', 'jobs', 'excluded_postings')


def _swap_source_check(values: tuple[str, ...]) -> None:
    clause = "source IN (" + ", ".join(f"'{v}'" for v in values) + ")"
    for table in TABLES:
        op.drop_constraint(op.f(f'ck_{table}_source'), table, type_='check')
        op.create_check_constraint(op.f(f'ck_{table}_source'), table, clause)


def upgrade() -> None:
    """Upgrade schema."""
    _swap_source_check(NEW)


def downgrade() -> None:
    """Downgrade schema."""
    _swap_source_check(OLD)
