import datetime
import os
import logging
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi_pagination import add_pagination
from sqlalchemy import text

from app.database import engine, Base, SessionLocal, get_db
from app.mcp_server import setup_mcp_server
from app.routers import memories_router, apps_router, stats_router, config_router
from app.models import User, App
from app.config import USER_ID, DEFAULT_APP_ID

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Vector store configuration for pgvector
VECTOR_STORE_CONFIG = {
    "provider": "pgvector",
    "config": {
        "url": os.getenv("DATABASE_URL"),
        "collection_name": "memories",
        "embedding_dimension": 384  # Default for sentence-transformers
    }
}

def create_database_tables():
    """Create database tables with proper error handling"""
    try:
        # Only create tables if environment allows it
        if os.getenv("CREATE_TABLES", "true").lower() == "true":
            Base.metadata.create_all(bind=engine)
            logger.info("✅ Database tables created successfully")
        else:
            logger.info("⏭️  Skipping table creation (CREATE_TABLES=false)")
    except Exception as e:
        logger.error(f"❌ Error creating database tables: {e}")
        # Don't raise here to allow the app to start even if tables exist


def create_default_user():
    """Create default user if it doesn't exist"""
    db = SessionLocal()
    try:
        # Check if user exists
        user = db.query(User).filter(User.user_id == USER_ID).first()
        if not user:
            # Create default user
            user = User(
                id=uuid4(),
                user_id=USER_ID,
                name="Default User",
                email=f"{USER_ID}@openmemory.local",
                metadata_={
                    "created_by": "system",
                    "default_user": True
                },
                created_at=datetime.datetime.now(datetime.UTC)
            )
            db.add(user)
            db.commit()
            logger.info(f"✅ Created default user: {USER_ID}")
        else:
            logger.info(f"👤 Default user already exists: {USER_ID}")
            
    except Exception as e:
        logger.error(f"❌ Error creating default user: {e}")
        db.rollback()
    finally:
        db.close()


def create_default_app():
    """Create default app for the default user"""
    db = SessionLocal()
    try:
        # Get the default user
        user = db.query(User).filter(User.user_id == USER_ID).first()
        if not user:
            logger.error(f"❌ Default user {USER_ID} not found, cannot create default app")
            return

        # Check if app already exists
        existing_app = db.query(App).filter(
            App.name == DEFAULT_APP_ID,
            App.owner_id == user.id
        ).first()
        
        if existing_app:
            logger.info(f"📱 Default app already exists: {DEFAULT_APP_ID}")
            return

        # Create default app
        app = App(
            id=uuid4(),
            name=DEFAULT_APP_ID,
            description="Default OpenMemory MCP app",
            owner_id=user.id,
            metadata_={
                "created_by": "system",
                "default_app": True
            },
            is_active=True,
            created_at=datetime.datetime.now(datetime.UTC),
            updated_at=datetime.datetime.now(datetime.UTC),
        )
        db.add(app)
        db.commit()
        logger.info(f"✅ Created default app: {DEFAULT_APP_ID}")
        
    except Exception as e:
        logger.error(f"❌ Error creating default app: {e}")
        db.rollback()
    finally:
        db.close()


def setup_static_files(app: FastAPI):
    """Setup static file serving for the UI"""
    static_dir = Path("static")
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory="static"), name="static")
        logger.info("📁 Static files mounted at /static")
        return True
    else:
        logger.warning("⚠️  Static directory not found, UI will not be available")
        return False


def create_cors_middleware():
    """Create CORS middleware with environment-based configuration"""
    allowed_origins = os.getenv("CORS_ORIGINS", "*").split(",")
    
    # For production, use more restrictive CORS
    if os.getenv("ENV") == "production":
        allowed_origins = [origin.strip() for origin in allowed_origins if origin.strip() != "*"]
        if not allowed_origins:
            allowed_origins = ["https://*.onrender.com"]
    
    return CORSMiddleware, {
        "allow_origins": allowed_origins,
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["*"],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("🚀 Starting OpenMemory MCP Server...")
    
    # Create database tables
    create_database_tables()
    
    # Create default user and app
    create_default_user()
    create_default_app()
    
    logger.info("✅ OpenMemory MCP Server startup complete")
    
    yield
    
    # Shutdown
    logger.info("🔄 Shutting down OpenMemory MCP Server...")


# Create FastAPI app with lifespan management
app = FastAPI(
    title="OpenMemory MCP API",
    description="Local-first memory layer for AI applications",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
cors_class, cors_kwargs = create_cors_middleware()
app.add_middleware(cors_class, **cors_kwargs)

# Setup static file serving
has_static_files = setup_static_files(app)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint with database connectivity test"""
    try:
        # Test database connection
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        
        # Check memory service status
        try:
            from app.memory import get_memory_client
            memory_client = get_memory_client()
            memory_status = "initialized" if memory_client else "fallback_mode"
        except Exception as e:
            memory_status = f"error: {str(e)}"
        
        return {
            "status": "healthy",
            "database": "postgresql" if "postgresql" in os.getenv("DATABASE_URL", "") else "unknown",
            "vector_store": "qdrant_cloud",
            "memory_service": memory_status,
            "static_files": has_static_files,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Database connection failed")


# UI routes (only if static files exist)
if has_static_files:
    @app.get("/")
    async def serve_ui():
        """Serve the main UI"""
        return FileResponse("static/index.html")

    @app.get("/{path:path}")
    async def serve_ui_routes(path: str):
        """Serve UI routes with fallback to index.html for SPA"""
        file_path = Path(f"static/{path}")
        
        # Serve static files directly
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        
        # Fallback to index.html for SPA routing
        return FileResponse("static/index.html")
else:
    @app.get("/")
    async def api_info():
        """API information when UI is not available"""
        return {
            "message": "OpenMemory MCP API",
            "docs": "/docs",
            "health": "/health",
            "version": "1.0.0"
        }


# Setup MCP server
try:
    setup_mcp_server(app)
    logger.info("✅ MCP server setup complete")
except Exception as e:
    logger.error(f"❌ Error setting up MCP server: {e}")


# Include API routers
app.include_router(memories_router, prefix="/api/v1", tags=["memories"])
app.include_router(apps_router, prefix="/api/v1", tags=["apps"])
app.include_router(stats_router, prefix="/api/v1", tags=["stats"])
app.include_router(config_router, prefix="/api/v1", tags=["config"])

# Add pagination support
add_pagination(app)

# Log configuration on startup
logger.info(f"🔧 Configuration:")
logger.info(f"   Database: {'PostgreSQL' if 'postgresql' in os.getenv('DATABASE_URL', '') else 'Unknown'}")
logger.info(f"   Vector Store: pgvector")
logger.info(f"   User ID: {USER_ID}")
logger.info(f"   Default App: {DEFAULT_APP_ID}")
logger.info(f"   Environment: {os.getenv('ENV', 'development')}")
logger.info(f"   Static Files: {'Enabled' if has_static_files else 'Disabled'}")

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"🌐 Starting server on {host}:{port}")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=os.getenv("ENV") != "production",
        log_level="info"
    )
