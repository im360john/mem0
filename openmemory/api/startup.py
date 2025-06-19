#!/usr/bin/env python3
"""
Simple database setup script for OpenMemory MCP
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def main():
    """Main setup function"""
    logger.info("🚀 Starting OpenMemory database setup...")
    
    # Get database URL
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("❌ DATABASE_URL environment variable not found")
        sys.exit(1)
    
    logger.info(f"🔗 Connecting to database...")
    
    try:
        # Create engine and test connection
        engine = create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ Database connection successful")
        
        # Setup PostgreSQL extensions if needed
        if 'postgresql' in database_url:
            setup_postgres_extensions(engine)
        
        # Try to import and create tables
        logger.info("🔨 Setting up database tables...")
        import_and_create_tables(engine)
        
        # Setup Alembic tracking
        setup_alembic_tracking(engine)
        
        logger.info("🎉 Database setup completed successfully!")
        
    except Exception as e:
        logger.error(f"❌ Database setup failed: {e}")
        sys.exit(1)

def setup_postgres_extensions(engine):
    """Setup PostgreSQL extensions"""
    try:
        with engine.connect() as conn:
            extensions = [
                'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
                'CREATE EXTENSION IF NOT EXISTS pg_trgm', 
                'CREATE EXTENSION IF NOT EXISTS btree_gin'
            ]
            
            for ext_sql in extensions:
                try:
                    conn.execute(text(ext_sql))
                except Exception as e:
                    logger.warning(f"⚠️ Extension note: {e}")
            
            conn.commit()
        logger.info("✅ PostgreSQL extensions setup")
        
    except Exception as e:
        logger.warning(f"⚠️ Extensions setup warning: {e}")

def import_and_create_tables(engine):
    """Import models and create tables"""
    try:
        # Add the current directory to Python path
        sys.path.insert(0, os.getcwd())
        
        # Import database and models
        from app.database import Base
        import app.models  # This imports all models and registers them with Base
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created successfully")
        
    except ImportError as e:
        logger.error(f"❌ Failed to import models: {e}")
        logger.info("🔄 Trying alternative import...")
        
        # Alternative: try direct SQL creation
        create_tables_with_sql(engine)
    
    except Exception as e:
        logger.error(f"❌ Table creation failed: {e}")
        raise

def create_tables_with_sql(engine):
    """Fallback: create tables with direct SQL"""
    logger.info("🔄 Using fallback SQL table creation...")
    
    # Basic table creation SQL (simplified)
    tables_sql = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id VARCHAR(255) NOT NULL UNIQUE,
            name VARCHAR(255),
            email VARCHAR(255) UNIQUE,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS apps (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            metadata JSONB DEFAULT '{}',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(owner_id, name)
        )
        """,
        """
        CREATE TYPE IF NOT EXISTS memory_state_enum AS ENUM ('active', 'paused', 'archived', 'deleted')
        """,
        """
        CREATE TABLE IF NOT EXISTS memories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            app_id UUID NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            vector TEXT,
            metadata JSONB DEFAULT '{}',
            state memory_state_enum DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            archived_at TIMESTAMPTZ,
            deleted_at TIMESTAMPTZ
        )
        """
    ]
    
    try:
        with engine.connect() as conn:
            for sql in tables_sql:
                conn.execute(text(sql))
            conn.commit()
        logger.info("✅ Basic tables created with SQL")
        
    except Exception as e:
        logger.error(f"❌ SQL table creation failed: {e}")
        raise

def setup_alembic_tracking(engine):
    """Setup Alembic version tracking"""
    try:
        with engine.connect() as conn:
            # Create alembic_version table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
            """))
            
            # Insert current version
            conn.execute(text("""
                INSERT INTO alembic_version (version_num) 
                VALUES ('0b53c747049a') 
                ON CONFLICT (version_num) DO NOTHING
            """))
            
            conn.commit()
        logger.info("✅ Alembic tracking setup")
        
    except Exception as e:
        logger.warning(f"⚠️ Alembic setup warning: {e}")

if __name__ == "__main__":
    main()
