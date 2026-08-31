from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("companies",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("name",sa.String(255),nullable=False),sa.Column("contact_email",sa.String(320),nullable=False,server_default=""),sa.Column("phone",sa.String(80),nullable=False,server_default=""),sa.Column("active",sa.Integer(),nullable=False,server_default="1"),sa.Column("created_at",sa.String(40),nullable=False))
    op.create_index("uq_companies_name_ci","companies",[sa.text("lower(name)")],unique=True)
    op.create_table("users",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("company_id",sa.Integer(),sa.ForeignKey("companies.id")),sa.Column("role",sa.String(20),nullable=False),sa.Column("first_name",sa.String(120),nullable=False),sa.Column("last_name",sa.String(120),nullable=False),sa.Column("email",sa.String(320),nullable=False),sa.Column("password_hash",sa.Text(),nullable=False),sa.Column("active",sa.Integer(),nullable=False,server_default="1"),sa.Column("created_at",sa.String(40),nullable=False),sa.CheckConstraint("role IN ('supervisor','operator','customer')",name="ck_users_role"))
    op.create_index("uq_users_email_ci","users",[sa.text("lower(email)")],unique=True)
    op.create_table("sessions",sa.Column("token",sa.String(128),primary_key=True),sa.Column("user_id",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),sa.Column("acting_company_id",sa.Integer(),sa.ForeignKey("companies.id")),sa.Column("csrf_token",sa.String(128),nullable=False),sa.Column("created_at",sa.String(40),nullable=False),sa.Column("expires_at",sa.String(40),nullable=False))
    op.create_table("audit_log",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("company_id",sa.Integer(),sa.ForeignKey("companies.id")),sa.Column("actor_user_id",sa.Integer(),sa.ForeignKey("users.id")),sa.Column("actor_role",sa.String(20)),sa.Column("acting_company_id",sa.Integer(),sa.ForeignKey("companies.id")),sa.Column("action",sa.String(120),nullable=False),sa.Column("entity_type",sa.String(120),nullable=False),sa.Column("entity_id",sa.String(120)),sa.Column("before_json",sa.Text()),sa.Column("after_json",sa.Text()),sa.Column("created_at",sa.String(40),nullable=False))


def downgrade() -> None:
    op.drop_table("audit_log");op.drop_table("sessions");op.drop_index("uq_users_email_ci",table_name="users");op.drop_table("users");op.drop_index("uq_companies_name_ci",table_name="companies");op.drop_table("companies")
