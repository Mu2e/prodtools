#!/usr/bin/env python3
"""SQLAlchemy models for POMS monitoring."""

from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, Text, DateTime, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import os
from datetime import datetime

Base = declarative_base()


class Job(Base):
    """POMS job definition."""
    __tablename__ = 'jobs'
    
    id = Column(Integer, primary_key=True)
    tarball = Column(String, index=True)
    fcl_template = Column(String)
    indef = Column(String)
    njobs = Column(Integer, default=0)
    template_mode = Column(Boolean, default=False)
    complete = Column(Boolean, default=False)
    inloc = Column(String)
    source_file = Column(String, index=True)
    # Performance metrics (aggregated per jobdef)
    avg_real_h = Column(Float)
    avg_vmhwm_gb = Column(Float)
    
    # Relationships
    outputs = relationship("JobOutput", back_populates="job", cascade="all, delete-orphan")
    
    @property
    def campaign(self):
        """Extract campaign from tarball."""
        if not self.tarball:
            return None
        parts = self.tarball.split('.')
        if len(parts) >= 4:
            campaign_full = parts[3]
            return campaign_full.split('_')[0]
        return None


class JobOutput(Base):
    """Output dataset for a job."""
    __tablename__ = 'job_outputs'
    
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('jobs.id'), nullable=False)
    dataset = Column(String, index=True)
    location = Column(String)
    
    # Relationship
    job = relationship("Job", back_populates="outputs")


class DatasetInfo(Base):
    """Cached dataset information from SAM."""
    __tablename__ = 'dataset_info'
    
    id = Column(Integer, primary_key=True)
    dataset_name = Column(String, unique=True, index=True)
    nfiles = Column(Integer)
    nevts = Column(Integer)
    total_size = Column(Integer)  # in bytes
    location = Column(String)
    has_children = Column(Boolean, default=False)  # True if any file in dataset has child files
    creation_date = Column(DateTime)  # Creation date from SAM definition
    # Performance metrics (averages from job logs)
    avg_real_h = Column(Float)      # Average wall time in hours
    avg_vmhwm_gb = Column(Float)    # Average high-water-mark memory in GB
    
    @property
    def avg_size_mb(self):
        """Average file size in MB."""
        if self.nfiles and self.nfiles > 0:
            return round(self.total_size / self.nfiles / 1e6, 2)
        return 0


def get_db_session(db_path=None):
    """Get SQLAlchemy session."""
    if db_path is None:
        # Default to in-memory database for now
        db_path = 'sqlite:///:memory:'
    else:
        db_path = f'sqlite:///{db_path}'
    
    engine = create_engine(db_path, echo=False)
    Base.metadata.create_all(engine)

    # Ensure new columns exist when upgrading older databases
    with engine.connect() as conn:
        try:
            result = conn.exec_driver_sql("PRAGMA table_info(dataset_info)")
            columns = [row[1] for row in result]
            if 'location' not in columns:
                conn.exec_driver_sql("ALTER TABLE dataset_info ADD COLUMN location TEXT")
        except Exception:
            pass
    Session = sessionmaker(bind=engine)
    return Session()


