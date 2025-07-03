from sqlalchemy import Column, String, Integer, Float, DateTime, Boolean, Text, ForeignKey, JSON, Interval
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()

class Agent(Base):
    __tablename__ = 'agents'
    
    agent_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    miner_hotkey = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    latest_version = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)
    last_updated = Column(DateTime, nullable=False)
    
    # Relationships
    versions = relationship("AgentVersion", back_populates="agent")

class AgentVersion(Base):
    __tablename__ = 'agent_versions'
    
    version_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('agents.agent_id'), nullable=False)
    version_num = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)
    score = Column(Float)
    
    # Relationships
    agent = relationship("Agent", back_populates="versions")
    evaluations = relationship("Evaluation", back_populates="agent_version")

class Evaluation(Base):
    __tablename__ = 'evaluations'
    
    evaluation_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id = Column(UUID(as_uuid=True), ForeignKey('agent_versions.version_id'), nullable=False)
    validator_hotkey = Column(Text, nullable=False)
    status = Column(Text, nullable=False)  # waiting, running, completed, replaced
    terminated_reason = Column(Text)
    created_at = Column(DateTime, nullable=False)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    score = Column(Float)
    
    # Relationships
    agent_version = relationship("AgentVersion", back_populates="evaluations")
    evaluation_runs = relationship("EvaluationRun", back_populates="evaluation")

class EvaluationRun(Base):
    __tablename__ = 'evaluation_runs'
    
    run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id = Column(UUID(as_uuid=True), ForeignKey('evaluations.evaluation_id'), nullable=False)
    swebench_instance_id = Column(Text, nullable=False)
    response = Column(Text)
    error = Column(Text)
    pass_to_fail_success = Column(Text)
    fail_to_pass_success = Column(Text)
    pass_to_pass_success = Column(Text)
    fail_to_fail_success = Column(Text)
    solved = Column(Boolean)
    status = Column(Text, nullable=False)  # started, sandbox_created, patch_generated, eval_started, result_scored
    started_at = Column(DateTime, nullable=False)
    sandbox_created_at = Column(DateTime)
    patch_generated_at = Column(DateTime)
    eval_started_at = Column(DateTime)
    result_scored_at = Column(DateTime)
    
    # Relationships
    evaluation = relationship("Evaluation", back_populates="evaluation_runs")

class WeightsHistory(Base):
    __tablename__ = 'weights_history'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime, nullable=False, default=datetime.now)
    time_since_last_update = Column(Interval)
    miner_weights = Column(JSON, nullable=False)

class BannedHotkey(Base):
    __tablename__ = 'banned_hotkeys'
    
    miner_hotkey = Column(Text, primary_key=True, nullable=False)
    banned_reason = Column(Text)
    banned_at = Column(DateTime, nullable=False, default=datetime.now)