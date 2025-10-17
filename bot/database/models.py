from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import enum

Base = declarative_base()

class Priority(enum.Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    CRITICAL = 'critical'

class Status(enum.Enum):
    NEW = 'new'
    IN_PROGRESS = 'in_progress'
    ON_HOLD = 'on_hold'
    RESOLVED = 'resolved'
    CLOSED = 'closed'

class Category(enum.Enum):
    HARDWARE = 'hardware'
    SOFTWARE = 'software'
    NETWORK = 'network'
    ACCOUNT = 'account'
    OTHER = 'other'

class ITRequest(Base):
    __tablename__ = 'it_requests'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    username = Column(String(100))
    full_name = Column(String(200))
    category = Column(Enum(Category), nullable=False)
    priority = Column(Enum(Priority), default=Priority.MEDIUM)
    status = Column(Enum(Status), default=Status.NEW)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    location = Column(String(100))
    contact_phone = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    assigned_to = Column(String(100))
    solution = Column(Text)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'full_name': self.full_name,
            'category': self.category.value,
            'priority': self.priority.value,
            'status': self.status.value,
            'title': self.title,
            'description': self.description,
            'location': self.location,
            'contact_phone': self.contact_phone,
            'created_at': self.created_at,
            'assigned_to': self.assigned_to,
            'solution': self.solution
        }

class Database:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
    
    def get_session(self):
        return self.Session()
