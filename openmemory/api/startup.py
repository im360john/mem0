#!/usr/bin/env python3
"""
Database setup and initialization script for OpenMemory MCP
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import SQLAlchemyError

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def setup_database():
    """Setup database tables and handle migrations"""
    
    # Get database URL
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("❌ DATABASE_URL environment variable not found")
        return False
    
    logger.info(f"🔗 Connecting to database...")
    
    try:
        # Create engine
        engine = create_engine(database_url)
        
        # Test connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ Database connection successful")
        
        # Import models (after successful connection)
        try:
            from app.database import Base
            from app.models import *
            logger.info("✅ Models imported successfully")
        except ImportError as e:
            logger.error(f"❌ Failed to import models: {e}")
            return False
        
        # Check if tables exist
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()
        logger.info(f"📋 Found {len(existing_tables)} existing tables")
        
        # Create tables
        logger.info("🔨 Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created successfully")
        
        # Setup Alembic version tracking
        setup_alembic_version(engine)
        
        # Verify table creation
        inspector = inspect(engine)
        new_tables = inspector.get_table_names()
        logger.info(f"📊 Total tables after setup: {len(new_tables)}")
        
        return True
        
    except SQLAlchemyError as e:
        logger.error(f"❌ Database error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return False

def setup_alembic_version(engine):
    """Setup Alembic version tracking"""
    try:
        with engine.connect() as conn:
            # Create alembic_version table if it doesn't exist
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
            """))
            
            # Insert current migration version
            conn.execute(text("""
                INSERT INTO alembic_version (version_num) 
                VALUES ('0b53c747049a') 
                ON CONFLICT (version_num) DO NOTHING
            """))
            
            conn.commit()
        logger.info("✅ Alembic version tracking setup complete")
        
    except Exception as e:
        logger.warning(f"⚠️ Alembic version setup failed: {e}")

def setup_extensions():
    """Setup PostgreSQL extensions"""
    database_url = os.getenv('DATABASE_URL', '')
    
    # Only run for PostgreSQL
    if 'postgresql' not in database_url:
        logger.info("ℹ️ Not PostgreSQL, skipping extensions")
        return
    
    try:
        engine = create_engine(database_url)
        with engine.connect() as conn:
            # Enable extensions
            extensions = [
                'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
                'CREATE EXTENSION IF NOT EXISTS pg_trgm',
                'CREATE EXTENSION IF NOT EXISTS btree_gin'
            ]
            
            for ext_sql in extensions:
                try:
                    conn.execute(text(ext_sql))
                    logger.info(f"✅ Extension enabled: {ext_sql.split()[-1]}")
                except Exception as e:
                    logger.warning(f"⚠️ Extension warning: {e}")
            
            conn.commit()
        logger.info("✅ PostgreSQL extensions setup complete")
        
    except Exception as e:
        logger.warning(f"⚠️ Extensions setup failed: {e}")

def run_alembic_upgrade():
    """Try to run Alembic upgrade"""
    try:
        logger.info("🔄 Attempting Alembic upgrade...")
        import subprocess
        result = subprocess.run(
            ['python', '-m', 'alembic', 'upgrade', 'head'],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            logger.info("✅ Alembic upgrade successful")
            return True
        else:
            logger.warning(f"⚠️ Alembic upgrade failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.warning("⚠️ Alembic upgrade timed out")
        return False
    except Exception as e:
        logger.warning(f"⚠️ Alembic upgrade error: {e}")
        return False

def main():
    """Main setup function"""
    logger.info("🚀 Starting OpenMemory database setup...")
    
    # Step 1: Setup PostgreSQL extensions
    setup_extensions()
    
    # Step 2: Try Alembic first
    alembic_success = run_alembic_upgrade()
    
    # Step 3: If Alembic fails, use direct table creation
    if not alembic_success:
        logger.info("🔄 Falling back to direct table creation...")
        success = setup_database()
        if not success:
            logger.error("❌ Database setup failed")
            sys.exit(1)
    
    logger.info("🎉 Database setup completed successfully!")

if __name__ == "__main__":
    main()
