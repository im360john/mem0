import enum
import uuid
import datetime
import sqlalchemy as sa
from sqlalchemy import (
    Column, String, Boolean, ForeignKey, Enum, Table,
    DateTime, JSON, Integer, Index, event, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from sqlalchemy.orm import Session
from app.utils.categorization import get_categories_for_memory


def get_current_utc_time():
    """Get current UTC time"""
    return datetime.datetime.now(datetime.UTC)


class MemoryState(enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"
    deleted = "deleted"


class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=True, index=True)
    email = Column(String, unique=True, nullable=True, index=True)
    metadata_ = Column('metadata', JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=get_current_utc_time, index=True)
    updated_at = Column(DateTime(timezone=True),
                        default=get_current_utc_time,
                        onupdate=get_current_utc_time)

    # Relationships
    apps = relationship("App", back_populates="owner", cascade="all, delete-orphan")
    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")


class App(Base):
    __tablename__ = "apps"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text)
    metadata_ = Column('metadata', JSON, default=dict)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), default=get_current_utc_time, index=True)
    updated_at = Column(DateTime(timezone=True),
                        default=get_current_utc_time,
                        onupdate=get_current_utc_time)

    # Relationships
    owner = relationship("User", back_populates="apps")
    memories = relationship("Memory", back_populates="app", cascade="all, delete-orphan")

    __table_args__ = (
        sa.UniqueConstraint('owner_id', 'name', name='idx_app_owner_name'),
    )


class Config(Base):
    __tablename__ = "configs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_current_utc_time)
    updated_at = Column(DateTime(timezone=True),
                        default=get_current_utc_time,
                        onupdate=get_current_utc_time)


class Memory(Base):
    __tablename__ = "memories"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)  # Use Text for longer content
    vector = Column(Text)  # Store vector embeddings as text/JSON
    metadata_ = Column('metadata', JSON, default=dict)
    state = Column(Enum(MemoryState, name='memory_state_enum'), default=MemoryState.active, index=True)
    created_at = Column(DateTime(timezone=True), default=get_current_utc_time, index=True)
    updated_at = Column(DateTime(timezone=True),
                        default=get_current_utc_time,
                        onupdate=get_current_utc_time)
    archived_at = Column(DateTime(timezone=True), nullable=True, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Relationships
    user = relationship("User", back_populates="memories")
    app = relationship("App", back_populates="memories")
    categories = relationship("Category", secondary="memory_categories", back_populates="memories")

    __table_args__ = (
        Index('idx_memory_user_state', 'user_id', 'state'),
        Index('idx_memory_app_state', 'app_id', 'state'),
        Index('idx_memory_user_app', 'user_id', 'app_id'),
        Index('idx_memory_created_at', 'created_at'),
        Index('idx_memory_content_search', 'content', postgresql_using='gin', postgresql_ops={'content': 'gin_trgm_ops'}),
    )


class Category(Base):
    __tablename__ = "categories"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), default=get_current_utc_time, index=True)
    updated_at = Column(DateTime(timezone=True),
                        default=get_current_utc_time,
                        onupdate=get_current_utc_time)

    # Relationships
    memories = relationship("Memory", secondary="memory_categories", back_populates="categories")


# Association table for many-to-many relationship between Memory and Category
memory_categories = Table(
    "memory_categories", Base.metadata,
    Column("memory_id", UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True, index=True),
    Column("category_id", UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True, index=True),
    Index('idx_memory_category', 'memory_id', 'category_id')
)


class AccessControl(Base):
    __tablename__ = "access_controls"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_type = Column(String, nullable=False, index=True)  # 'user', 'app', etc.
    subject_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    object_type = Column(String, nullable=False, index=True)   # 'memory', 'category', etc.
    object_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    effect = Column(String, nullable=False, index=True)        # 'allow', 'deny'
    created_at = Column(DateTime(timezone=True), default=get_current_utc_time, index=True)

    __table_args__ = (
        Index('idx_access_subject', 'subject_type', 'subject_id'),
        Index('idx_access_object', 'object_type', 'object_id'),
        Index('idx_access_subject_object', 'subject_type', 'subject_id', 'object_type', 'object_id'),
    )


class ArchivePolicy(Base):
    __tablename__ = "archive_policies"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    criteria_type = Column(String, nullable=False, index=True)  # 'user', 'app', 'category'
    criteria_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    days_to_archive = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_current_utc_time, index=True)

    __table_args__ = (
        Index('idx_policy_criteria', 'criteria_type', 'criteria_id'),
    )


class MemoryStatusHistory(Base):
    __tablename__ = "memory_status_history"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    memory_id = Column(UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True)
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    old_state = Column(Enum(MemoryState, name='memory_state_enum'), nullable=False, index=True)
    new_state = Column(Enum(MemoryState, name='memory_state_enum'), nullable=False, index=True)
    changed_at = Column(DateTime(timezone=True), default=get_current_utc_time, index=True)
    reason = Column(Text, nullable=True)  # Optional reason for state change

    __table_args__ = (
        Index('idx_history_memory_state', 'memory_id', 'new_state'),
        Index('idx_history_user_time', 'changed_by', 'changed_at'),
        Index('idx_history_changed_at', 'changed_at'),
    )


class MemoryAccessLog(Base):
    __tablename__ = "memory_access_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    memory_id = Column(UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    accessed_at = Column(DateTime(timezone=True), default=get_current_utc_time, index=True)
    access_type = Column(String, nullable=False, index=True)  # 'read', 'write', 'delete', etc.
    ip_address = Column(String, nullable=True)
    user_agent = Column(Text, nullable=True)
    metadata_ = Column('metadata', JSON, default=dict)

    __table_args__ = (
        Index('idx_access_memory_time', 'memory_id', 'accessed_at'),
        Index('idx_access_app_time', 'app_id', 'accessed_at'),
        Index('idx_access_user_time', 'user_id', 'accessed_at'),
        Index('idx_access_type_time', 'access_type', 'accessed_at'),
    )


def categorize_memory(memory: Memory, db: Session) -> None:
    """Categorize a memory using OpenAI and store the categories in the database."""
    try:
        # Get categories from OpenAI
        categories = get_categories_for_memory(memory.content)

        # Get or create categories in the database
        for category_name in categories:
            category = db.query(Category).filter(Category.name == category_name).first()
            if not category:
                category = Category(
                    name=category_name,
                    description=f"Automatically created category for {category_name}"
                )
                db.add(category)
                db.flush()  # Flush to get the category ID

            # Check if the memory-category association already exists
            existing = db.execute(
                memory_categories.select().where(
                    (memory_categories.c.memory_id == memory.id) &
                    (memory_categories.c.category_id == category.id)
                )
            ).first()

            if not existing:
                # Create the association
                db.execute(
                    memory_categories.insert().values(
                        memory_id=memory.id,
                        category_id=category.id
                    )
                )

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error categorizing memory: {e}")


@event.listens_for(Memory, 'after_insert')
def after_memory_insert(mapper, connection, target):
    """Trigger categorization after a memory is inserted."""
    try:
        db = Session(bind=connection)
        categorize_memory(target, db)
    except Exception as e:
        print(f"Error in after_memory_insert: {e}")
    finally:
        if 'db' in locals():
            db.close()


@event.listens_for(Memory, 'after_update')
def after_memory_update(mapper, connection, target):
    """Trigger categorization after a memory is updated."""
    try:
        db = Session(bind=connection)
        categorize_memory(target, db)
    except Exception as e:
        print(f"Error in after_memory_update: {e}")
    finally:
        if 'db' in locals():
            db.close()
