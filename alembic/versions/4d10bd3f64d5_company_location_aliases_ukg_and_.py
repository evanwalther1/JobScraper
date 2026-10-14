"""company location_aliases, ukg and paylocity sources

Revision ID: 4d10bd3f64d5
Revises: 2b0ca664054c
Create Date: 2026-09-29 17:22:30.323212

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d10bd3f64d5'
down_revision: Union[str, Sequence[str], None] = '2b0ca664054c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Autogenerate doesn't see CHECK changes; the source constraint is updated by hand.
OLD = ('greenhouse', 'lever', 'ashby', 'workday', 'manual')
NEW = ('greenhouse', 'lever', 'ashby', 'workday', 'ukg', 'paylocity', 'manual')


def _swap_source_check(values: tuple[str, ...]) -> None:
    clause = "source IN (" + ", ".join(f"'{v}'" for v in values) + ")"
    for table in ('companies', 'jobs'):
        op.drop_constraint(op.f(f'ck_{table}_source'), table, type_='check')
        op.create_check_constraint(op.f(f'ck_{table}_source'), table, clause)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('companies', sa.Column('location_aliases', sa.Text(), nullable=True))
    _swap_source_check(NEW)


def downgrade() -> None:
    """Downgrade schema."""
    _swap_source_check(OLD)
    op.drop_column('companies', 'location_aliases')
