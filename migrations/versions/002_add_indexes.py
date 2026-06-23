"""add indexes

Revision ID: 002
Revises: 001
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_logs_event", "logs", ["event"])
    op.create_index("ix_logs_created_at", "logs", ["created_at"])
    op.create_index("ix_logs_user_id", "logs", ["user_id"])
    op.create_index("ix_logs_level", "logs", ["level"])
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_index("ix_logs_level", table_name="logs")
    op.drop_index("ix_logs_user_id", table_name="logs")
    op.drop_index("ix_logs_created_at", table_name="logs")
    op.drop_index("ix_logs_event", table_name="logs")
