"""oracle source

Revision ID: 0cec5a78375b
Revises: 84cfa7015e2f
Create Date: 2026-09-29 17:41:03.593108

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0cec5a78375b'
down_revision: Union[str, Sequence[str], None] = '84cfa7015e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Hand-written: autogenerate doesn't detect CHECK constraint changes.
OLD = ('greenhouse', 'lever', 'ashby', 'workday', 'ukg', 'paylocity', 'manual')
NEW = ('greenhouse', 'lever', 'ashby', 'workday', 'ukg', 'paylocity', 'oracle', 'manual')
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
