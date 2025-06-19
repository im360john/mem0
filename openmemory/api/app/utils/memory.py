"""
Memory client utilities for OpenMemory with enhanced Qdrant index management.

This module provides functionality to initialize and manage the Mem0 memory client
with automatic configuration management, Docker environment support, and Qdrant index management.
"""

import os
import json
import hashlib
import socket
import platform
import logging

from mem0 import Memory
from app.database import SessionLocal
from app.models import Config as ConfigModel

logger = logging.getLogger(__name__)

_memory_client = None
_config_hash = None
_qdrant_client = None
_indexes_created = False


def _get_config_hash(config_dict):
    """Generate a hash of the config to detect changes."""
    config_str = json.dumps(config_dict, sort_keys=True)
    return hashlib.md5(config_str.encode()).hexdigest()


def _get_docker_host_url():
    """
    Determine the appropriate host URL to reach host machine from inside Docker container.
    Returns the best available option for reaching the host from inside a container.
    """
    # Check for custom environment variable first
    custom_host = os.environ.get('OLLAMA_HOST')
    if custom_host:
        print(f"Using custom Ollama host from OLLAMA_HOST: {custom_host}")
        return custom_host.replace('http://', '').replace('https://', '').split(':')[0]
    
    # Check if we're running inside Docker
    if not os.path.exists('/.dockerenv'):
        # Not in Docker, return localhost as-is
        return "localhost"
    
    print("Detected Docker environment, adjusting host URL for Ollama...")
    
    # Try different host resolution strategies
    host_candidates = []
    
    # 1. host.docker.internal (works on Docker Desktop for Mac/Windows)
    try:
        socket.gethostbyname('host.docker.internal')
        host_candidates.append('host.docker.internal')
        print("Found host.docker.internal")
    except socket.gaierror:
        pass
    
    # 2. Docker bridge gateway (typically 172.17.0.1 on Linux)
    try:
        with open('/proc/net/route', 'r') as f:
            for line in f:
                fields = line.strip().split()
                if fields[1] == '00000000':  # Default route
                    gateway_hex = fields[2]
                    gateway_ip = socket.inet_ntoa(bytes.fromhex(gateway_hex)[::-1])
                    host_candidates.append(gateway_ip)
                    print(f"Found Docker gateway: {gateway_ip}")
                    break
    except (FileNotFoundError, IndexError, ValueError):
        pass
    
    # 3. Fallback to common Docker bridge IP
    if not host_candidates:
        host_candidates.append('172.17.0.1')
        print("Using fallback Docker bridge IP: 172.17.0.1")
    
    # Return the first available candidate
    return host_candidates[0]


def _fix_ollama_urls(config_section):
    """
    Fix Ollama URLs for Docker environment.
    Replaces localhost URLs with appropriate Docker host URLs.
    Sets default ollama_base_url if not provided.
    """
    if not config_section or "config" not in config_section:
        return config_section
    
    ollama_config = config_section["config"]
    
    # Set default ollama_base_url if not provided
    if "ollama_base_url" not in ollama_config:
        ollama_config["ollama_base_url"] = "http://host.docker.internal:11434"
    else:
        # Check for ollama_base_url and fix if it's localhost
        url = ollama_config["ollama_base_url"]
        if "localhost" in url or "127.0.0.1" in url:
            docker_host = _get_docker_host_url()
            if docker_host != "localhost":
                new_url = url.replace("localhost", docker_host).replace("127.0.0.1", docker_host)
                ollama_config["ollama_base_url"] = new_url
                print(f"Adjusted Ollama URL from {url} to {new_url}")
    
    return config_section


def _get_qdrant_config():
    """
    Get Qdrant configuration based on environment variables.
    Supports both local Qdrant and Qdrant Cloud.
    Only includes fields allowed by Mem0's MemoryConfig.
    """
    qdrant_url = os.environ.get('QDRANT_URL', '')
    qdrant_api_key = os.environ.get('QDRANT_API_KEY', '')
    qdrant_collection = os.environ.get('QDRANT_COLLECTION_NAME', 'openmemory')
    
    # Check if Qdrant is disabled
    if qdrant_url.lower() in ['disabled', 'false', 'off']:
        print("⚠️ Qdrant disabled via environment variable")
        return None
    
    # Qdrant Cloud configuration
    if qdrant_url and qdrant_api_key:
        # For Qdrant Cloud, we can use the full URL directly
        if not qdrant_url.startswith(('http://', 'https://')):
            # Add https:// if not present for cloud URLs
            qdrant_url = f"https://{qdrant_url}"
        
        config = {
            "url": qdrant_url,
            "api_key": qdrant_api_key,
            "collection_name": qdrant_collection,
        }
        
        print(f"🔗 Configured Qdrant Cloud: {qdrant_url}")
        return config
    
    # Local Qdrant configuration (fallback)
    elif qdrant_url:
        print(f"🔗 Configured local Qdrant: {qdrant_url}")
        if qdrant_url.startswith(('http://', 'https://')):
            # Use URL format for local with protocol
            return {
                "url": qdrant_url,
                "collection_name": qdrant_collection,
            }
        else:
            # Use host:port format for local without protocol
            if ':' in qdrant_url:
                host, port = qdrant_url.split(':', 1)
                port = int(port)
            else:
                host = qdrant_url
                port = 6333
                
            return {
                "host": host,
                "port": port,
                "collection_name": qdrant_collection,
            }
    
    # Default local configuration
    else:
        print("🔗 Using default local Qdrant configuration")
        return {
            "host": "mem0_store",
            "port": 6333,
            "collection_name": qdrant_collection,
        }


def _setup_qdrant_client():
    """Setup direct Qdrant client for index management"""
    global _qdrant_client
    
    try:
        from qdrant_client import QdrantClient
        
        config = _get_qdrant_config()
        if not config:
            return False
        
        if "url" in config:
            # URL-based config (Qdrant Cloud)
            _qdrant_client = QdrantClient(
                url=config["url"],
                api_key=config.get("api_key")
            )
        else:
            # Host/port-based config (local)
            _qdrant_client = QdrantClient(
                host=config.get("host", "localhost"),
                port=config.get("port", 6333)
            )
        
        logger.info("✅ Qdrant client initialized for index management")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to setup Qdrant client: {e}")
        _qdrant_client = None
        return False


def _ensure_qdrant_indexes():
    """Ensure required indexes exist in Qdrant"""
    global _indexes_created, _qdrant_client
    
    if _indexes_created or not _qdrant_client:
        return
    
    try:
        from qdrant_client.models import PayloadSchemaType
        
        config = _get_qdrant_config()
        collection_name = config.get("collection_name", "openmemory")
        
        # Check if collection exists
        try:
            collections = _qdrant_client.get_collections()
            collection_exists = any(col.name == collection_name for col in collections.collections)
            
            if not collection_exists:
                logger.info(f"Collection '{collection_name}' doesn't exist yet - indexes will be created when first memory is added")
                return
            
        except Exception as e:
            logger.warning(f"Could not check collections: {e}")
            return
        
        # Create index for user_id field
        try:
            _qdrant_client.create_payload_index(
                collection_name=collection_name,
                field_name="user_id",
                field_schema=PayloadSchemaType.KEYWORD
            )
            logger.info("✅ Created index for user_id field")
            
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.info("ℹ️ Index for user_id already exists")
            else:
                logger.warning(f"⚠️ Could not create user_id index: {e}")
        
        # Create index for app_id field (if used)
        try:
            _qdrant_client.create_payload_index(
                collection_name=collection_name,
                field_name="app_id", 
                field_schema=PayloadSchemaType.KEYWORD
            )
            logger.info("✅ Created index for app_id field")
            
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.info("ℹ️ Index for app_id already exists")
            else:
                logger.warning(f"⚠️ Could not create app_id index: {e}")
        
        _indexes_created = True
        
    except Exception as e:
        logger.error(f"❌ Failed to ensure indexes: {e}")


def reset_memory_client():
    """Reset the global memory client to force reinitialization with new config."""
    global _memory_client, _config_hash, _qdrant_client, _indexes_created
    _memory_client = None
    _config_hash = None
    _qdrant_client = None
    _indexes_created = False


def get_default_memory_config():
    """Get default memory client configuration with sensible defaults."""
    
    # Get Qdrant configuration
    qdrant_config = _get_qdrant_config()
    
    base_config = {
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gpt-4o-mini",
                "temperature": 0.1,
                "max_tokens": 2000,
                "api_key": "env:OPENAI_API_KEY"
            }
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "text-embedding-3-small",
                "api_key": "env:OPENAI_API_KEY"
            }
        },
        "version": "v1.1"
    }
    
    # Add vector store config if Qdrant is available
    if qdrant_config:
        base_config["vector_store"] = {
            "provider": "qdrant",
            "config": qdrant_config
        }
        print("✅ Vector store (Qdrant) included in configuration")
    else:
        print("⚠️ No vector store configured - running in basic mode")
    
    return base_config


def _parse_environment_variables(config_dict):
    """
    Parse environment variables in config values.
    Converts 'env:VARIABLE_NAME' to actual environment variable values.
    """
    if isinstance(config_dict, dict):
        parsed_config = {}
        for key, value in config_dict.items():
            if isinstance(value, str) and value.startswith("env:"):
                env_var = value.split(":", 1)[1]
                env_value = os.environ.get(env_var)
                if env_value:
                    parsed_config[key] = env_value
                    print(f"Loaded {env_var} from environment for {key}")
                else:
                    print(f"Warning: Environment variable {env_var} not found, keeping original value")
                    parsed_config[key] = value
            elif isinstance(value, dict):
                parsed_config[key] = _parse_environment_variables(value)
            else:
                parsed_config[key] = value
        return parsed_config
    return config_dict


def _test_qdrant_connection():
    """Test Qdrant connection to verify it's working."""
    qdrant_config = _get_qdrant_config()
    
    if not qdrant_config:
        return False
    
    try:
        import requests
        
        # Build URL based on config format
        if "url" in qdrant_config:
            # URL-based config (Qdrant Cloud)
            base_url = qdrant_config["url"]
            if not base_url.endswith('/'):
                base_url += '/'
            test_url = f"{base_url}collections"
        else:
            # Host/port-based config (local)
            host = qdrant_config.get("host", "localhost")
            port = qdrant_config.get("port", 6333)
            test_url = f"http://{host}:{port}/collections"
        
        api_key = qdrant_config.get("api_key")
        
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
        
        print(f"🔍 Testing Qdrant connection: {test_url}")
        
        response = requests.get(test_url, headers=headers, timeout=10)
        
        if response.status_code in [200, 404]:  # 404 is OK (no collections yet)
            print(f"✅ Qdrant connection successful (status: {response.status_code})")
            return True
        else:
            print(f"❌ Qdrant connection failed (status: {response.status_code})")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Qdrant connection test failed: {e}")
        return False


def get_memory_client(custom_instructions: str = None):
    """
    Get or initialize the Mem0 client with enhanced error handling and index management.

    Args:
        custom_instructions: Optional instructions for the memory project.

    Returns:
        Initialized Mem0 client instance or None if initialization fails.

    Raises:
        Exception: If required API keys are not set or critical configuration is missing.
    """
    global _memory_client, _config_hash, _qdrant_client

    try:
        # Start with default configuration
        config = get_default_memory_config()
        
        # Variable to track custom instructions
        db_custom_instructions = None
        
        # Load configuration from database
        try:
            db = SessionLocal()
            db_config = db.query(ConfigModel).filter(ConfigModel.key == "main").first()
            
            if db_config:
                json_config = db_config.value
                
                # Extract custom instructions from openmemory settings
                if "openmemory" in json_config and "custom_instructions" in json_config["openmemory"]:
                    db_custom_instructions = json_config["openmemory"]["custom_instructions"]
                
                # Override defaults with configurations from the database
                if "mem0" in json_config:
                    mem0_config = json_config["mem0"]
                    
                    # Update LLM configuration if available
                    if "llm" in mem0_config and mem0_config["llm"] is not None:
                        config["llm"] = mem0_config["llm"]
                        
                        # Fix Ollama URLs for Docker if needed
                        if config["llm"].get("provider") == "ollama":
                            config["llm"] = _fix_ollama_urls(config["llm"])
                    
                    # Update Embedder configuration if available
                    if "embedder" in mem0_config and mem0_config["embedder"] is not None:
                        config["embedder"] = mem0_config["embedder"]
                        
                        # Fix Ollama URLs for Docker if needed
                        if config["embedder"].get("provider") == "ollama":
                            config["embedder"] = _fix_ollama_urls(config["embedder"])
            else:
                print("No configuration found in database, using defaults")
                    
            db.close()
                            
        except Exception as e:
            print(f"Warning: Error loading configuration from database: {e}")
            print("Using default configuration")
            # Continue with default configuration if database config can't be loaded

        # Use custom_instructions parameter first, then fall back to database value
        instructions_to_use = custom_instructions or db_custom_instructions
        if instructions_to_use:
            config["custom_fact_extraction_prompt"] = instructions_to_use

        # ALWAYS parse environment variables in the final config
        # This ensures that even default config values like "env:OPENAI_API_KEY" get parsed
        print("Parsing environment variables in final config...")
        config = _parse_environment_variables(config)

        # Test Qdrant connection and setup client if vector store is configured
        if "vector_store" in config:
            if not _test_qdrant_connection():
                print("⚠️ Qdrant connection failed, removing vector store from config")
                config.pop("vector_store", None)
            else:
                # Setup Qdrant client for index management
                _setup_qdrant_client()

        # Check if config has changed by comparing hashes
        current_config_hash = _get_config_hash(config)
        
        # Only reinitialize if config changed or client doesn't exist
        if _memory_client is None or _config_hash != current_config_hash:
            print(f"Initializing memory client with config hash: {current_config_hash}")
            try:
                _memory_client = Memory.from_config(config_dict=config)
                _config_hash = current_config_hash
                
                # Ensure indexes exist if we have Qdrant
                if "vector_store" in config and _qdrant_client:
                    _ensure_qdrant_indexes()
                
                # Log the configuration mode
                if "vector_store" in config:
                    print("✅ Memory client initialized with vector store (Qdrant)")
                else:
                    print("✅ Memory client initialized in basic mode (no vector store)")
                    
            except Exception as init_error:
                print(f"Warning: Failed to initialize memory client: {init_error}")
                print("Server will continue running with limited memory functionality")
                _memory_client = None
                _config_hash = None
                return None
        
        return _memory_client
        
    except Exception as e:
        print(f"Warning: Exception occurred while initializing memory client: {e}")
        print("Server will continue running with limited memory functionality")
        return None


def ensure_indexes_after_add():
    """Ensure indexes exist after adding memories (call this after successful memory add)"""
    global _qdrant_client, _indexes_created
    
    if _qdrant_client and not _indexes_created:
        _ensure_qdrant_indexes()


def get_default_user_id():
    return "default_user"
