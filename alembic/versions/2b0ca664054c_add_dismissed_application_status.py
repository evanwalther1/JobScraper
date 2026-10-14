"""add dismissed application status

Revision ID: 2b0ca664054c
Revises: 664f4d5c5689
Create Date: 2026-09-29 16:50:42.142293

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b0ca664054c'
down_revision: Union[str, Sequence[str], None] = '664f4d5c5689'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Autogenerate doesn't detect CHECK constraint changes, so this one is hand-written.
# This is the payoff of VARCHAR + CHECK over a native ENUM: swap the constraint, done.
OLD = ('saved', 'applied', 'interviewing', 'offer', 'rejected', 'withdrawn')
NEW = OLD + ('dismissed',)


def _in(values: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f('ck_applications_status'), 'applications', type_='check')
    op.create_check_constraint(op.f('ck_applications_status'), 'applications', _in(NEW))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM applications WHERE status = 'dismissed'")
    op.drop_constraint(op.f('ck_applications_status'), 'applications', type_='check')
    op.create_check_constraint(op.f('ck_applications_status'), 'applications', _in(OLD))
