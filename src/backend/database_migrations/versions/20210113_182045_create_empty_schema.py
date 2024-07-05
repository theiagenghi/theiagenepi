"""create empty schema

Create Date: 2021-01-13 18:20:45.683596

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20210113_182045"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create the 'aspen' schema
    op.execute('CREATE SCHEMA IF NOT EXISTS aspen')
    
    # Optionally, you can set the search path
    op.execute('SET search_path TO aspen, public')


def downgrade():
    # Drop the 'aspen' schema
    op.execute('DROP SCHEMA IF EXISTS aspen CASCADE')
    
    # Optionally, reset the search path to default
    op.execute('SET search_path TO public')