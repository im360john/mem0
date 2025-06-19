"""
MCP Server for OpenMemory with resilient memory client handling.

This module implements an MCP (Model Context Protocol) server that provides
memory operations for OpenMemory. The memory client is initialized lazily
to prevent server crashes when external dependencies (like Ollama) are
unavailable. If the memory client cannot be initialized, the server will
continue running with limited functionality and appropriate error messages.

Key features:
- Lazy memory client initialization
- Graceful error handling for unavailable dependencies
- Fallback to database-only mode when vector store is unavailable
- Proper logging for debugging connection issues
- Environment variable parsing for API keys
"""

import logging
import json
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from app.memory import get_memory_client
from fastapi import FastAPI, Request
from fastapi.routing import APIRouter
import contextvars
import os
from dotenv import load_dotenv
from app.database import SessionLocal
from app.models import Memory, MemoryState, MemoryStatusHistory, MemoryAccessLog
from app.utils.db import get_user_and_app
import uuid
import datetime
from app.utils.permissions import check_memory_access_permissions
from qdrant_client import models as qdrant_models

# Load environment variables
load_dotenv()

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MCP
mcp = FastMCP("mem0-mcp-server")

# Don't initialize memory client at import time - do it lazily when needed
def get_memory_client_safe():
    """Get memory client with error handling. Returns None if client cannot be initialized."""
    try:
        client = get_memory_client()
        if client:
            # Ensure indexes exist after getting client
            from app.memory import ensure_indexes_after_add
            ensure_indexes_after_add()
        return client
    except Exception as e:
        logger.warning(f"Failed to get memory client: {e}")
        return None

# Context variables for user_id and client_name
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id")
client_name_var: contextvars.ContextVar[str] = contextvars.ContextVar("client_name")

# Create a router for MCP endpoints
mcp_router = APIRouter(prefix="/mcp")

# Initialize SSE transport
sse = SseServerTransport("/mcp/messages/")

@mcp.tool(description="Add a new memory. This method is called everytime the user informs anything about themselves, their preferences, or anything that has any relevant information which can be useful in the future conversation. This can also be called when the user asks you to remember something.")
async def add_memories(text: str) -> str:
    uid = user_id_var.get(None)
    client_name = client_name_var.get(None)

    if not uid:
        return json.dumps({"error": "user_id not provided", "success": False})
    if not client_name:
        return json.dumps({"error": "client_name not provided", "success": False})
    if not text or not text.strip():
        return json.dumps({"error": "text is required and cannot be empty", "success": False})

    logger.info(f"🔍 Adding memory for user {uid}: '{text[:50]}...'")

    # Get memory client safely
    memory_client = get_memory_client_safe()
    if not memory_client:
        return json.dumps({"error": "Memory system is currently unavailable. Please try again later.", "success": False})

    try:
        db = SessionLocal()
        try:
            # Get or create user and app
            user, app = get_user_and_app(db, user_id=uid, app_id=client_name)

            # Check if app is active
            if not app.is_active:
                return json.dumps({
                    "error": f"App {app.name} is currently paused on OpenMemory. Cannot create new memories.", 
                    "success": False
                })

            # Add enhanced metadata
            enhanced_metadata = {
                "user_id": uid,  # Ensure user_id is in metadata for filtering
                "source_app": "openmemory",
                "mcp_client": client_name,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat()
            }

            # Add memory
            response = memory_client.add(
                messages=[{"role": "user", "content": text}],
                user_id=uid,
                metadata=enhanced_metadata
            )

            logger.info(f"✅ Memory client response: {response}")
            logger.info(f"✅ Response type: {type(response)}")

            # Process the response and update database
            memories_added = []
            
            if isinstance(response, dict) and 'results' in response:
                # Handle Mem0 response format with results array
                for result in response['results']:
                    memory_id = uuid.UUID(result['id']) if 'id' in result else uuid.uuid4()
                    memory = db.query(Memory).filter(Memory.id == memory_id).first()

                    if result.get('event') == 'ADD':
                        if not memory:
                            memory = Memory(
                                id=memory_id,
                                user_id=user.id,
                                app_id=app.id,
                                content=result.get('memory', text),
                                state=MemoryState.active,
                                created_at=datetime.datetime.now(datetime.UTC)
                            )
                            db.add(memory)
                        else:
                            memory.state = MemoryState.active
                            memory.content = result.get('memory', text)
                            memory.updated_at = datetime.datetime.now(datetime.UTC)

                        # Create history entry
                        history = MemoryStatusHistory(
                            memory_id=memory_id,
                            changed_by=user.id,
                            old_state=MemoryState.deleted if memory else None,
                            new_state=MemoryState.active,
                            changed_at=datetime.datetime.now(datetime.UTC)
                        )
                        db.add(history)
                        
                        memories_added.append({
                            "id": str(memory_id),
                            "content": result.get('memory', text),
                            "event": "ADD"
                        })

                    elif result.get('event') == 'DELETE':
                        if memory:
                            memory.state = MemoryState.deleted
                            memory.deleted_at = datetime.datetime.now(datetime.UTC)
                            # Create history entry
                            history = MemoryStatusHistory(
                                memory_id=memory_id,
                                changed_by=user.id,
                                old_state=MemoryState.active,
                                new_state=MemoryState.deleted,
                                changed_at=datetime.datetime.now(datetime.UTC)
                            )
                            db.add(history)
                            
                            memories_added.append({
                                "id": str(memory_id),
                                "content": result.get('memory', text),
                                "event": "DELETE"
                            })

                db.commit()
                
                logger.info(f"✅ Successfully processed {len(memories_added)} memory operations")
                
                return json.dumps({
                    "success": True,
                    "message": f"Successfully processed {len(memories_added)} memory operations",
                    "memories": memories_added,
                    "user_id": uid,
                    "original_response": response
                })
                
            elif isinstance(response, dict):
                # Handle simple response format
                memory_id = uuid.uuid4()
                
                memory = Memory(
                    id=memory_id,
                    user_id=user.id,
                    app_id=app.id,
                    content=text,
                    state=MemoryState.active,
                    created_at=datetime.datetime.now(datetime.UTC)
                )
                db.add(memory)
                
                # Create history entry
                history = MemoryStatusHistory(
                    memory_id=memory_id,
                    changed_by=user.id,
                    old_state=None,
                    new_state=MemoryState.active,
                    changed_at=datetime.datetime.now(datetime.UTC)
                )
                db.add(history)
                
                db.commit()
                
                logger.info(f"✅ Successfully added memory with ID {memory_id}")
                
                return json.dumps({
                    "success": True,
                    "message": "Memory added successfully",
                    "memory": {
                        "id": str(memory_id),
                        "content": text
                    },
                    "user_id": uid,
                    "original_response": response
                })
            else:
                # Handle unexpected response format
                logger.warning(f"⚠️ Unexpected response format: {type(response)}")
                
                # Still create a database entry
                memory_id = uuid.uuid4()
                memory = Memory(
                    id=memory_id,
                    user_id=user.id,
                    app_id=app.id,
                    content=text,
                    state=MemoryState.active,
                    created_at=datetime.datetime.now(datetime.UTC)
                )
                db.add(memory)
                db.commit()
                
                return json.dumps({
                    "success": True,
                    "message": "Memory added successfully (unknown response format)",
                    "memory": {
                        "id": str(memory_id),
                        "content": text
                    },
                    "user_id": uid,
                    "original_response": str(response)
                })

        finally:
            db.close()
            
    except Exception as e:
        logger.exception(f"❌ Error adding memory: {e}")
        return json.dumps({
            "error": f"Failed to add memory: {str(e)}",
            "success": False,
            "user_id": uid
        })


@mcp.tool(description="Search through stored memories. This method is called EVERYTIME the user asks anything.")
async def search_memory(query: str) -> str:
    uid = user_id_var.get(None)
    client_name = client_name_var.get(None)
    
    if not uid:
        return json.dumps({"error": "user_id not provided", "results": []})
    if not client_name:
        return json.dumps({"error": "client_name not provided", "results": []})
    if not query or not query.strip():
        return json.dumps({"error": "query is required and cannot be empty", "results": []})

    logger.info(f"🔍 Searching memories for user {uid}: '{query}'")

    # Get memory client safely
    memory_client = get_memory_client_safe()
    if not memory_client:
        return json.dumps({"error": "Memory system is currently unavailable. Please try again later.", "results": []})

    try:
        db = SessionLocal()
        try:
            # Get or create user and app
            user, app = get_user_and_app(db, user_id=uid, app_id=client_name)

            # Try memory client search first
            try:
                # Use memory client search with error handling
                results = memory_client.search(
                    query=query,
                    user_id=uid,
                    limit=10
                )
                
                # Handle different result formats
                if isinstance(results, dict) and "results" in results:
                    search_results = results["results"]
                elif isinstance(results, list):
                    search_results = results
                else:
                    search_results = []
                
                logger.info(f"✅ Memory client search found {len(search_results)} results")
                
                # Log access for found memories
                for result in search_results:
                    if isinstance(result, dict) and 'id' in result:
                        try:
                            memory_id = uuid.UUID(result['id'])
                            access_log = MemoryAccessLog(
                                memory_id=memory_id,
                                app_id=app.id,
                                access_type="search",
                                metadata_={
                                    "query": query,
                                    "score": result.get('score'),
                                    "method": "memory_client"
                                }
                            )
                            db.add(access_log)
                        except (ValueError, KeyError) as e:
                            logger.warning(f"Could not log access for result: {e}")
                
                db.commit()
                
                return json.dumps({
                    "results": search_results,
                    "query": query,
                    "user_id": uid,
                    "count": len(search_results),
                    "method": "memory_client"
                })
                
            except Exception as search_error:
                logger.warning(f"⚠️ Memory client search failed: {search_error}")
                
                # Fallback to manual Qdrant search (your original code)
                if hasattr(memory_client, 'vector_store') and hasattr(memory_client, 'embedding_model'):
                    try:
                        # Get accessible memory IDs based on ACL
                        user_memories = db.query(Memory).filter(Memory.user_id == user.id).all()
                        accessible_memory_ids = [memory.id for memory in user_memories if check_memory_access_permissions(db, memory, app.id)]
                        
                        conditions = [qdrant_models.FieldCondition(key="user_id", match=qdrant_models.MatchValue(value=uid))]
                        
                        if accessible_memory_ids:
                            # Convert UUIDs to strings for Qdrant
                            accessible_memory_ids_str = [str(memory_id) for memory_id in accessible_memory_ids]
                            conditions.append(qdrant_models.HasIdCondition(has_id=accessible_memory_ids_str))

                        filters = qdrant_models.Filter(must=conditions)
                        embeddings = memory_client.embedding_model.embed(query, "search")
                        
                        hits = memory_client.vector_store.client.query_points(
                            collection_name=memory_client.vector_store.collection_name,
                            query=embeddings,
                            query_filter=filters,
                            limit=10,
                        )

                        # Process search results
                        memories = hits.points
                        memories = [
                            {
                                "id": memory.id,
                                "memory": memory.payload["data"],
                                "hash": memory.payload.get("hash"),
                                "created_at": memory.payload.get("created_at"),
                                "updated_at": memory.payload.get("updated_at"),
                                "score": memory.score,
                            }
                            for memory in memories
                        ]

                        # Log memory access for each memory found
                        for memory in memories:
                            if 'id' in memory:
                                try:
                                    memory_id = uuid.UUID(memory['id'])
                                    access_log = MemoryAccessLog(
                                        memory_id=memory_id,
                                        app_id=app.id,
                                        access_type="search",
                                        metadata_={
                                            "query": query,
                                            "score": memory.get('score'),
                                            "hash": memory.get('hash'),
                                            "method": "direct_qdrant"
                                        }
                                    )
                                    db.add(access_log)
                                except (ValueError, KeyError) as e:
                                    logger.warning(f"Could not log access for memory: {e}")
                        
                        db.commit()
                        
                        logger.info(f"✅ Direct Qdrant search found {len(memories)} results")
                        
                        return json.dumps({
                            "results": memories,
                            "query": query,
                            "user_id": uid,
                            "count": len(memories),
                            "method": "direct_qdrant"
                        })
                        
                    except Exception as qdrant_error:
                        logger.error(f"❌ Direct Qdrant search also failed: {qdrant_error}")
                        
                # Final fallback: return empty results
                return json.dumps({
                    "results": [],
                    "query": query,
                    "user_id": uid,
                    "count": 0,
                    "method": "fallback",
                    "error": f"Search failed: {str(search_error)}"
                })

        finally:
            db.close()
            
    except Exception as e:
        logger.exception(f"❌ Error in search_memory: {e}")
        return json.dumps({
            "error": f"Search failed: {str(e)}",
            "results": [],
            "query": query,
            "user_id": uid
        })


@mcp.tool(description="List all memories in the user's memory")
async def list_memories() -> str:
    uid = user_id_var.get(None)
    client_name = client_name_var.get(None)
    
    if not uid:
        return json.dumps({"error": "user_id not provided", "memories": []})
    if not client_name:
        return json.dumps({"error": "client_name not provided", "memories": []})

    logger.info(f"📋 Listing memories for user {uid}")

    # Get memory client safely
    memory_client = get_memory_client_safe()
    if not memory_client:
        return json.dumps({"error": "Memory system is currently unavailable. Please try again later.", "memories": []})

    try:
        db = SessionLocal()
        try:
            # Get or create user and app
            user, app = get_user_and_app(db, user_id=uid, app_id=client_name)

            # Get all memories
            memories = memory_client.get_all(user_id=uid)
            filtered_memories = []

            # Filter memories based on permissions
            user_memories = db.query(Memory).filter(Memory.user_id == user.id).all()
            accessible_memory_ids = [memory.id for memory in user_memories if check_memory_access_permissions(db, memory, app.id)]
            
            if isinstance(memories, dict) and 'results' in memories:
                for memory_data in memories['results']:
                    if 'id' in memory_data:
                        try:
                            memory_id = uuid.UUID(memory_data['id'])
                            if memory_id in accessible_memory_ids:
                                # Create access log entry
                                access_log = MemoryAccessLog(
                                    memory_id=memory_id,
                                    app_id=app.id,
                                    access_type="list",
                                    metadata_={
                                        "hash": memory_data.get('hash')
                                    }
                                )
                                db.add(access_log)
                                filtered_memories.append(memory_data)
                        except ValueError as e:
                            logger.warning(f"Invalid memory ID: {e}")
            else:
                for memory in memories:
                    if isinstance(memory, dict) and 'id' in memory:
                        try:
                            memory_id = uuid.UUID(memory['id'])
                            memory_obj = db.query(Memory).filter(Memory.id == memory_id).first()
                            if memory_obj and check_memory_access_permissions(db, memory_obj, app.id):
                                # Create access log entry
                                access_log = MemoryAccessLog(
                                    memory_id=memory_id,
                                    app_id=app.id,
                                    access_type="list",
                                    metadata_={
                                        "hash": memory.get('hash')
                                    }
                                )
                                db.add(access_log)
                                filtered_memories.append(memory)
                        except ValueError as e:
                            logger.warning(f"Invalid memory ID: {e}")
            
            db.commit()
            
            logger.info(f"✅ Listed {len(filtered_memories)} accessible memories")
            
            return json.dumps({
                "memories": filtered_memories,
                "user_id": uid,
                "count": len(filtered_memories)
            })
            
        finally:
            db.close()
            
    except Exception as e:
        logger.exception(f"❌ Error getting memories: {e}")
        return json.dumps({
            "error": f"Failed to get memories: {str(e)}",
            "memories": [],
            "user_id": uid
        })


@mcp.tool(description="Delete all memories in the user's memory")
async def delete_all_memories() -> str:
    uid = user_id_var.get(None)
    client_name = client_name_var.get(None)
    
    if not uid:
        return json.dumps({"error": "user_id not provided", "success": False})
    if not client_name:
        return json.dumps({"error": "client_name not provided", "success": False})

    logger.info(f"🗑️ Deleting all memories for user {uid}")

    # Get memory client safely
    memory_client = get_memory_client_safe()
    if not memory_client:
        return json.dumps({"error": "Memory system is currently unavailable. Please try again later.", "success": False})

    try:
        db = SessionLocal()
        try:
            # Get or create user and app
            user, app = get_user_and_app(db, user_id=uid, app_id=client_name)

            user_memories = db.query(Memory).filter(Memory.user_id == user.id).all()
            accessible_memory_ids = [memory.id for memory in user_memories if check_memory_access_permissions(db, memory, app.id)]

            deleted_count = 0
            # delete the accessible memories only
            for memory_id in accessible_memory_ids:
                try:
                    memory_client.delete(memory_id)
                    deleted_count += 1
                except Exception as delete_error:
                    logger.warning(f"Failed to delete memory {memory_id} from vector store: {delete_error}")

            # Update each memory's state and create history entries
            now = datetime.datetime.now(datetime.UTC)
            for memory_id in accessible_memory_ids:
                memory = db.query(Memory).filter(Memory.id == memory_id).first()
                if memory:
                    # Update memory state
                    memory.state = MemoryState.deleted
                    memory.deleted_at = now

                    # Create history entry
                    history = MemoryStatusHistory(
                        memory_id=memory_id,
                        changed_by=user.id,
                        old_state=MemoryState.active,
                        new_state=MemoryState.deleted,
                        changed_at=now
                    )
                    db.add(history)

                    # Create access log entry
                    access_log = MemoryAccessLog(
                        memory_id=memory_id,
                        app_id=app.id,
                        access_type="delete_all",
                        metadata_={"operation": "bulk_delete"}
                    )
                    db.add(access_log)

            db.commit()
            
            logger.info(f"✅ Successfully deleted {len(accessible_memory_ids)} memories")
            
            return json.dumps({
                "success": True,
                "message": f"Successfully deleted {len(accessible_memory_ids)} memories",
                "deleted_count": len(accessible_memory_ids),
                "user_id": uid
            })
            
        finally:
            db.close()
            
    except Exception as e:
        logger.exception(f"❌ Error deleting memories: {e}")
        return json.dumps({
            "error": f"Failed to delete memories: {str(e)}",
            "success": False,
            "user_id": uid
        })


@mcp_router.get("/{client_name}/sse/{user_id}")
async def handle_sse(request: Request):
    """Handle SSE connections for a specific user and client"""
    # Extract user_id and client_name from path parameters
    uid = request.path_params.get("user_id")
    user_token = user_id_var.set(uid or "")
    client_name = request.path_params.get("client_name")
    client_token = client_name_var.set(client_name or "")

    try:
        # Handle SSE connection
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as (read_stream, write_stream):
            await mcp._mcp_server.run(
                read_stream,
                write_stream,
                mcp._mcp_server.create_initialization_options(),
            )
    finally:
        # Clean up context variables
        user_id_var.reset(user_token)
        client_name_var.reset(client_token)


@mcp_router.post("/messages/")
async def handle_get_message(request: Request):
    return await handle_post_message(request)


@mcp_router.post("/{client_name}/sse/{user_id}/messages/")
async def handle_post_message(request: Request):
    return await handle_post_message(request)

async def handle_post_message(request: Request):
    """Handle POST messages for SSE"""
    try:
        body = await request.body()

        # Create a simple receive function that returns the body
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        # Create a simple send function that does nothing
        async def send(message):
            return {}

        # Call handle_post_message with the correct arguments
        await sse.handle_post_message(request.scope, receive, send)

        # Return a success response
        return {"status": "ok"}
    finally:
        pass
        # Clean up context variable
        # client_name_var.reset(client_token)

def setup_mcp_server(app: FastAPI):
    """Setup MCP server with the FastAPI application"""
    mcp._mcp_server.name = f"mem0-mcp-server"

    # Include MCP router in the FastAPI app
    app.include_router(mcp_router)
