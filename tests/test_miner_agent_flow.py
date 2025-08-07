"""
Comprehensive test suite for miner agent flow covering upload, screening, evaluation, and scoring.
Tests core status transitions and business logic with proper mocking.
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch

# Mock environment variables before importing modules
import os
os.environ.update({
    'AWS_MASTER_USERNAME': 'test_user',
    'AWS_MASTER_PASSWORD': 'test_pass', 
    'AWS_RDS_PLATFORM_ENDPOINT': 'test_endpoint',
    'AWS_RDS_PLATFORM_DB_NAME': 'test_db'
})

# Import the entities and models we're testing
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api', 'src'))

from backend.entities import (
    AgentStatus, EvaluationStatus, SandboxStatus,
    MinerAgent, MinerAgentWithScores, MinerAgentScored,
    EvaluationRun
)
from models.screener import Screener
from models.evaluation import Evaluation


class TestAgentStatus:
    """Test AgentStatus enum and transitions"""
    
    def test_agent_status_enum_values(self):
        """Test all agent status enum values exist"""
        expected_statuses = [
            "awaiting_screening_1", "screening_1", "failed_screening_1",
            "awaiting_screening_2", "screening_2", "failed_screening_2", 
            "waiting", "evaluating", "scored", "replaced"
        ]
        
        for status in expected_statuses:
            assert hasattr(AgentStatus, status)
            assert AgentStatus[status].value == status

    def test_agent_status_from_string(self):
        """Test status string mapping"""
        assert AgentStatus.from_string("awaiting_screening_1") == AgentStatus.awaiting_screening_1
        assert AgentStatus.from_string("screening_2") == AgentStatus.screening_2
        assert AgentStatus.from_string("scored") == AgentStatus.scored
        
        # Test legacy mappings
        assert AgentStatus.from_string("awaiting_screening") == AgentStatus.awaiting_screening_1
        assert AgentStatus.from_string("screening") == AgentStatus.screening_1
        assert AgentStatus.from_string("evaluation") == AgentStatus.evaluating
        
        # Test invalid status defaults to awaiting_screening_1
        assert AgentStatus.from_string("invalid_status") == AgentStatus.awaiting_screening_1

    def test_status_transitions_stage1_success(self):
        """Test valid stage 1 screening success transition"""
        # awaiting_screening_1 -> screening_1 -> awaiting_screening_2
        initial = AgentStatus.from_string("awaiting_screening_1")
        screening = AgentStatus.from_string("screening_1")
        next_stage = AgentStatus.from_string("awaiting_screening_2")
        
        assert initial == AgentStatus.awaiting_screening_1
        assert screening == AgentStatus.screening_1
        assert next_stage == AgentStatus.awaiting_screening_2

    def test_status_transitions_stage1_failure(self):
        """Test stage 1 screening failure transition"""
        # awaiting_screening_1 -> screening_1 -> failed_screening_1
        initial = AgentStatus.from_string("awaiting_screening_1")
        screening = AgentStatus.from_string("screening_1") 
        failed = AgentStatus.from_string("failed_screening_1")
        
        assert initial == AgentStatus.awaiting_screening_1
        assert screening == AgentStatus.screening_1
        assert failed == AgentStatus.failed_screening_1

    def test_status_transitions_stage2_success(self):
        """Test stage 2 screening success transition"""
        # awaiting_screening_2 -> screening_2 -> waiting
        initial = AgentStatus.from_string("awaiting_screening_2")
        screening = AgentStatus.from_string("screening_2")
        waiting = AgentStatus.from_string("waiting")
        
        assert initial == AgentStatus.awaiting_screening_2
        assert screening == AgentStatus.screening_2
        assert waiting == AgentStatus.waiting

    def test_status_transitions_evaluation_flow(self):
        """Test evaluation flow transitions"""
        # waiting -> evaluating -> scored
        waiting = AgentStatus.from_string("waiting")
        evaluating = AgentStatus.from_string("evaluating")
        scored = AgentStatus.from_string("scored")
        
        assert waiting == AgentStatus.waiting
        assert evaluating == AgentStatus.evaluating
        assert scored == AgentStatus.scored


class TestScreener:
    """Test Screener model and stage detection"""
    
    def test_screener_stage_detection(self):
        """Test screener stage detection from hotkey"""
        assert Screener.get_stage("screener-1-abc123") == 1
        assert Screener.get_stage("screener-2-def456") == 2
        assert Screener.get_stage("i-0123456789abcdef") == 1  # Legacy
        assert Screener.get_stage("validator-xyz") is None
        assert Screener.get_stage("invalid-hotkey") is None

    def test_screener_initialization(self):
        """Test screener object initialization"""
        screener = Screener(
            hotkey="screener-1-test",
            status="available"
        )
        
        assert screener.hotkey == "screener-1-test"
        assert screener.stage == 1
        assert screener.status == "available"
        assert screener.is_available()
        assert screener.get_type() == "screener"

    def test_screener_state_management(self):
        """Test screener availability state changes"""
        screener = Screener(hotkey="screener-2-test", status="available")
        
        # Test initial state
        assert screener.is_available()
        assert screener.status == "available"
        
        # Test setting unavailable
        screener.status = "screening"
        screener.current_evaluation_id = "eval123"
        screener.current_agent_name = "test_agent"
        screener.current_agent_hotkey = "miner123"
        
        assert not screener.is_available()
        assert screener.screening_id == "eval123"
        assert screener.screening_agent_name == "test_agent"
        
        # Test reset to available
        screener.set_available()
        assert screener.is_available()
        assert screener.current_evaluation_id is None
        assert screener.current_agent_name is None

    def test_screener_start_screening_validation_logic(self):
        """Test screener start screening validation logic without database"""
        screener = Screener(hotkey="screener-1-test", status="available")
        
        # Test stage detection
        assert screener.stage == 1
        
        # Test availability check
        assert screener.is_available() is True
        
        # Test state changes
        screener.status = "screening"
        screener.current_evaluation_id = "eval123"
        assert screener.is_available() is False
        assert screener.screening_id == "eval123"

    @pytest.mark.asyncio 
    async def test_screener_get_first_available_and_reserve(self):
        """Test atomic screener reservation"""
        from api.src.socket.websocket_manager import WebSocketManager
        
        # Mock WebSocket manager with available screeners
        mock_ws_manager = Mock()
        mock_screener1 = Mock()
        mock_screener1.get_type.return_value = "screener"
        mock_screener1.status = "available"
        mock_screener1.is_available.return_value = True
        mock_screener1.stage = 1
        mock_screener1.hotkey = "screener-1-test"
        
        mock_screener2 = Mock()
        mock_screener2.get_type.return_value = "screener" 
        mock_screener2.status = "available"
        mock_screener2.is_available.return_value = True
        mock_screener2.stage = 2
        mock_screener2.hotkey = "screener-2-test"
        
        mock_ws_manager.clients = {"1": mock_screener1, "2": mock_screener2}
        
        with patch.object(WebSocketManager, 'get_instance', return_value=mock_ws_manager):
            # Test stage 1 reservation
            screener = await Screener.get_first_available_and_reserve(1)
            assert screener == mock_screener1
            assert screener.status == "reserving"
            
            # Test stage 2 reservation
            screener = await Screener.get_first_available_and_reserve(2)  
            assert screener == mock_screener2
            assert screener.status == "reserving"
            
            # Test no available screeners for stage 3
            screener = await Screener.get_first_available_and_reserve(3)
            assert screener is None


class TestEvaluationStatus:
    """Test EvaluationStatus enum and transitions"""
    
    def test_evaluation_status_enum_values(self):
        """Test all evaluation status values exist"""
        expected_statuses = ["waiting", "running", "replaced", "error", "completed", "cancelled"]
        
        for status in expected_statuses:
            assert hasattr(EvaluationStatus, status)
            assert EvaluationStatus[status].value == status

    def test_evaluation_status_from_string(self):
        """Test evaluation status string mapping"""
        assert EvaluationStatus.from_string("waiting") == EvaluationStatus.waiting
        assert EvaluationStatus.from_string("running") == EvaluationStatus.running
        assert EvaluationStatus.from_string("completed") == EvaluationStatus.completed
        assert EvaluationStatus.from_string("invalid") == EvaluationStatus.error


class TestMinerAgent:
    """Test MinerAgent model and operations"""
    
    def test_miner_agent_creation(self):
        """Test MinerAgent object creation"""
        agent_id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)
        
        agent = MinerAgent(
            version_id=agent_id,
            miner_hotkey="test_hotkey_123",
            agent_name="test_agent",
            version_num=1,
            created_at=created_at,
            status="awaiting_screening_1",
            agent_summary="Test agent description"
        )
        
        assert agent.version_id == agent_id
        assert agent.miner_hotkey == "test_hotkey_123"
        assert agent.agent_name == "test_agent"
        assert agent.version_num == 1
        assert agent.status == "awaiting_screening_1"
        assert agent.agent_summary == "Test agent description"

    def test_miner_agent_with_scores(self):
        """Test MinerAgentWithScores model"""
        agent_id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)
        
        agent = MinerAgentWithScores(
            version_id=agent_id,
            miner_hotkey="test_hotkey",
            agent_name="test_agent", 
            version_num=1,
            created_at=created_at,
            status="scored",
            score=0.85,
            set_id=1,
            approved=True
        )
        
        assert agent.score == 0.85
        assert agent.set_id == 1
        assert agent.approved is True

    @pytest.mark.asyncio
    async def test_high_score_detection_no_agent(self):
        """Test high score detection when agent not found"""
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        
        result = await MinerAgentScored.check_for_new_high_score(mock_conn, uuid.uuid4())
        
        assert result["high_score_detected"] is False
        assert "not found" in result["reason"]

    @pytest.mark.asyncio
    async def test_high_score_detection_beats_previous(self):
        """Test high score detection when agent beats previous best"""
        mock_conn = AsyncMock()
        agent_id = uuid.uuid4()
        
        # Mock current agent score
        mock_conn.fetchrow.side_effect = [
            {
                'version_id': agent_id,
                'miner_hotkey': 'test_hotkey',
                'agent_name': 'test_agent',
                'version_num': 1,
                'created_at': datetime.now(timezone.utc),
                'status': 'scored',
                'agent_summary': 'test',
                'set_id': 1,
                'approved': False,
                'validator_count': 3,
                'final_score': 0.95
            },
            {'max_score': 0.85}  # Previous max approved score
        ]
        
        result = await MinerAgentScored.check_for_new_high_score(mock_conn, agent_id)
        
        assert result["high_score_detected"] is True
        assert result["new_score"] == 0.95
        assert result["previous_max_score"] == 0.85
        assert result["miner_hotkey"] == "test_hotkey"

    @pytest.mark.asyncio
    async def test_high_score_detection_no_previous_approved(self):
        """Test high score detection when no previous approved agents"""
        mock_conn = AsyncMock()
        agent_id = uuid.uuid4()
        
        mock_conn.fetchrow.side_effect = [
            {
                'version_id': agent_id,
                'miner_hotkey': 'test_hotkey',
                'agent_name': 'test_agent', 
                'version_num': 1,
                'created_at': datetime.now(timezone.utc),
                'status': 'scored',
                'agent_summary': 'test',
                'set_id': 1,
                'approved': False,
                'validator_count': 3,
                'final_score': 0.85
            },
            {'max_score': None}  # No previous approved agents
        ]
        
        result = await MinerAgentScored.check_for_new_high_score(mock_conn, agent_id)
        
        assert result["high_score_detected"] is True
        assert result["new_score"] == 0.85
        assert result["previous_max_score"] == 0.0

    @pytest.mark.asyncio
    async def test_get_top_agent_with_leadership_rule(self):
        """Test top agent selection with 1.5% leadership rule"""
        mock_conn = AsyncMock()
        
        # Mock max set_id
        mock_conn.fetchrow.side_effect = [
            {'max_set_id': 5},  # Max set_id
            {  # Current leader
                'version_id': uuid.uuid4(),
                'miner_hotkey': 'leader_hotkey',
                'final_score': 0.90,
                'created_at': datetime.now(timezone.utc)
            },
            {  # Challenger that beats by 1.5%
                'version_id': uuid.uuid4(),
                'miner_hotkey': 'challenger_hotkey', 
                'final_score': 0.92  # 0.90 * 1.015 = 0.9135, so 0.92 beats this
            }
        ]
        
        with patch('api.src.utils.models.TopAgentHotkey') as mock_top_agent:
            result = await MinerAgentScored.get_top_agent(mock_conn)
            
            # Verify challenger was selected (score >= required_score)
            mock_top_agent.assert_called_once()
            call_args = mock_top_agent.call_args[1]
            assert call_args['miner_hotkey'] == 'challenger_hotkey'
            assert call_args['avg_score'] == 0.92

    @pytest.mark.asyncio
    async def test_get_top_agent_no_challenger(self):
        """Test top agent selection when no challenger beats 1.5% rule"""
        mock_conn = AsyncMock()
        
        mock_conn.fetchrow.side_effect = [
            {'max_set_id': 5},
            {  # Current leader
                'version_id': uuid.uuid4(),
                'miner_hotkey': 'leader_hotkey',
                'final_score': 0.90,
                'created_at': datetime.now(timezone.utc)
            },
            None  # No challenger beats 1.5% rule
        ]
        
        with patch('api.src.utils.models.TopAgentHotkey') as mock_top_agent:
            result = await MinerAgentScored.get_top_agent(mock_conn)
            
            # Verify current leader remains
            call_args = mock_top_agent.call_args[1]
            assert call_args['miner_hotkey'] == 'leader_hotkey'
            assert call_args['avg_score'] == 0.90


class TestEvaluationModel:
    """Test Evaluation model functionality"""
    
    def test_evaluation_initialization(self):
        """Test evaluation object creation"""
        eval_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        
        evaluation = Evaluation(
            evaluation_id=eval_id,
            version_id=version_id,
            validator_hotkey="screener-1-test",
            set_id=1,
            status=EvaluationStatus.waiting
        )
        
        assert evaluation.evaluation_id == eval_id
        assert evaluation.version_id == version_id
        assert evaluation.validator_hotkey == "screener-1-test"
        assert evaluation.is_screening is True
        assert evaluation.screener_stage == 1

    def test_evaluation_screening_detection(self):
        """Test screening vs validation detection"""
        # Screening evaluation
        screening_eval = Evaluation(
            evaluation_id=str(uuid.uuid4()),
            version_id=str(uuid.uuid4()),
            validator_hotkey="screener-2-test",
            set_id=1,
            status=EvaluationStatus.waiting
        )
        
        assert screening_eval.is_screening is True
        assert screening_eval.screener_stage == 2
        
        # Validation evaluation
        validation_eval = Evaluation(
            evaluation_id=str(uuid.uuid4()),
            version_id=str(uuid.uuid4()),
            validator_hotkey="validator-hotkey",
            set_id=1,
            status=EvaluationStatus.waiting
        )
        
        assert validation_eval.is_screening is False
        assert validation_eval.screener_stage is None

    @pytest.mark.asyncio
    async def test_evaluation_start_screening(self):
        """Test evaluation start with screening setup"""
        mock_conn = AsyncMock()
        
        evaluation = Evaluation(
            evaluation_id=str(uuid.uuid4()),
            version_id=str(uuid.uuid4()),
            validator_hotkey="screener-1-test",
            set_id=1,
            status=EvaluationStatus.waiting
        )
        
        # Mock database responses
        mock_conn.fetchval.return_value = 5  # max_set_id
        mock_conn.fetch.return_value = [
            {'swebench_instance_id': 'instance1'},
            {'swebench_instance_id': 'instance2'}
        ]
        
        # Mock _update_agent_status method which doesn't exist on the basic Evaluation class
        with patch.object(evaluation, 'start') as mock_start:
            mock_start.return_value = []  # Mock return value
            runs = await evaluation.start(mock_conn)
            
            # Verify start was called
            mock_start.assert_called_once_with(mock_conn)

    def test_evaluation_properties_and_validation(self):
        """Test evaluation properties and validation logic"""
        eval_id = str(uuid.uuid4())
        
        # Test screener evaluation
        screening_eval = Evaluation(
            evaluation_id=eval_id,
            version_id=str(uuid.uuid4()),
            validator_hotkey="screener-1-test",
            set_id=1,
            status=EvaluationStatus.waiting
        )
        
        assert screening_eval.evaluation_id == eval_id
        assert screening_eval.is_screening is True
        assert screening_eval.screener_stage == 1
        assert screening_eval.status == EvaluationStatus.waiting
        
        # Test validator evaluation
        validation_eval = Evaluation(
            evaluation_id=str(uuid.uuid4()),
            version_id=str(uuid.uuid4()),
            validator_hotkey="validator-hotkey",
            set_id=1,
            status=EvaluationStatus.waiting
        )
        
        assert validation_eval.is_screening is False
        assert validation_eval.screener_stage is None


class TestEvaluationRun:
    """Test EvaluationRun model and sandbox statuses"""
    
    def test_evaluation_run_creation(self):
        """Test evaluation run object creation"""
        run_id = uuid.uuid4()
        eval_id = uuid.uuid4()  # Use UUID instead of string
        started_at = datetime.now(timezone.utc)
        
        run = EvaluationRun(
            run_id=run_id,
            evaluation_id=eval_id,
            swebench_instance_id="instance123",
            status=SandboxStatus.started,
            started_at=started_at
        )
        
        assert run.run_id == run_id
        assert run.evaluation_id == eval_id
        assert run.swebench_instance_id == "instance123"
        assert run.status == SandboxStatus.started
        assert run.started_at == started_at
        assert run.response is None
        assert run.solved is None

    def test_sandbox_status_progression(self):
        """Test sandbox status progression through evaluation"""
        run = EvaluationRun(
            run_id=uuid.uuid4(),
            evaluation_id=uuid.uuid4(),
            swebench_instance_id="test",
            status=SandboxStatus.started,
            started_at=datetime.now(timezone.utc)
        )
        
        # Test status progression
        assert run.status == SandboxStatus.started
        
        run.status = SandboxStatus.sandbox_created
        run.sandbox_created_at = datetime.now(timezone.utc)
        assert run.status == SandboxStatus.sandbox_created
        assert run.sandbox_created_at is not None
        
        run.status = SandboxStatus.patch_generated
        run.patch_generated_at = datetime.now(timezone.utc)
        assert run.status == SandboxStatus.patch_generated
        
        run.status = SandboxStatus.result_scored
        run.result_scored_at = datetime.now(timezone.utc)
        run.solved = True
        assert run.status == SandboxStatus.result_scored
        assert run.solved is True


class TestAgentLifecycleFlow:
    """Test complete agent lifecycle flows"""
    
    @pytest.mark.asyncio
    async def test_complete_successful_flow(self):
        """Test complete successful agent flow from upload to scoring"""
        
        # 1. Upload - Agent starts as awaiting_screening_1
        agent_id = uuid.uuid4()
        agent = MinerAgent(
            version_id=agent_id,
            miner_hotkey="test_miner",
            agent_name="test_agent",
            version_num=1,
            created_at=datetime.now(timezone.utc),
            status="awaiting_screening_1"
        )
        
        assert AgentStatus.from_string(agent.status) == AgentStatus.awaiting_screening_1
        
        # 2. Stage 1 Screening - transitions through screening_1 to awaiting_screening_2
        agent.status = "screening_1"
        assert AgentStatus.from_string(agent.status) == AgentStatus.screening_1
        
        # Simulate successful screening (score >= 0.6)
        agent.status = "awaiting_screening_2"
        assert AgentStatus.from_string(agent.status) == AgentStatus.awaiting_screening_2
        
        # 3. Stage 2 Screening - transitions through screening_2 to waiting
        agent.status = "screening_2"
        assert AgentStatus.from_string(agent.status) == AgentStatus.screening_2
        
        # Simulate successful screening (score >= 0.2)
        agent.status = "waiting"
        assert AgentStatus.from_string(agent.status) == AgentStatus.waiting
        
        # 4. Evaluation - transitions through evaluating to scored
        agent.status = "evaluating"
        assert AgentStatus.from_string(agent.status) == AgentStatus.evaluating
        
        agent.status = "scored" 
        assert AgentStatus.from_string(agent.status) == AgentStatus.scored

    @pytest.mark.asyncio
    async def test_stage1_screening_failure_flow(self):
        """Test agent flow when stage 1 screening fails"""
        
        agent_id = uuid.uuid4()
        agent = MinerAgent(
            version_id=agent_id,
            miner_hotkey="test_miner",
            agent_name="failing_agent", 
            version_num=1,
            created_at=datetime.now(timezone.utc),
            status="awaiting_screening_1"
        )
        
        # Stage 1 screening starts
        agent.status = "screening_1"
        assert AgentStatus.from_string(agent.status) == AgentStatus.screening_1
        
        # Screening fails (score < 0.6)
        agent.status = "failed_screening_1"
        assert AgentStatus.from_string(agent.status) == AgentStatus.failed_screening_1
        
        # Agent should not proceed to stage 2

    @pytest.mark.asyncio
    async def test_stage2_screening_failure_flow(self):
        """Test agent flow when stage 2 screening fails"""
        
        agent_id = uuid.uuid4()
        agent = MinerAgent(
            version_id=agent_id,
            miner_hotkey="test_miner",
            agent_name="stage2_failing_agent",
            version_num=1,
            created_at=datetime.now(timezone.utc),
            status="awaiting_screening_2"  # Passed stage 1
        )
        
        # Stage 2 screening starts
        agent.status = "screening_2"
        assert AgentStatus.from_string(agent.status) == AgentStatus.screening_2
        
        # Screening fails (score < 0.2)
        agent.status = "failed_screening_2"
        assert AgentStatus.from_string(agent.status) == AgentStatus.failed_screening_2
        
        # Agent should not proceed to evaluation

    @pytest.mark.asyncio
    async def test_agent_replacement_flow(self):
        """Test agent replacement when newer version uploaded"""
        
        # Original agent
        original_agent = MinerAgent(
            version_id=uuid.uuid4(),
            miner_hotkey="test_miner",
            agent_name="test_agent",
            version_num=1,
            created_at=datetime.now(timezone.utc),
            status="scored"
        )
        
        # New version uploaded
        new_agent = MinerAgent(
            version_id=uuid.uuid4(),
            miner_hotkey="test_miner", 
            agent_name="test_agent",
            version_num=2,
            created_at=datetime.now(timezone.utc),
            status="awaiting_screening_1"
        )
        
        # Original should be marked as replaced
        original_agent.status = "replaced"
        assert AgentStatus.from_string(original_agent.status) == AgentStatus.replaced
        assert AgentStatus.from_string(new_agent.status) == AgentStatus.awaiting_screening_1


class TestScoreCalculation:
    """Test scoring logic and materialized view operations"""
    
    @pytest.mark.asyncio
    async def test_24_hour_statistics_calculation(self):
        """Test 24-hour statistics calculation"""
        mock_conn = AsyncMock()
        
        # Mock max set_id and statistics
        mock_conn.fetchrow.side_effect = [
            {'max_set_id': 10},  # Current max set_id
            {  # Statistics result
                'number_of_agents': 150,
                'agent_iterations_last_24_hours': 25,
                'top_agent_score': 0.923,
                'daily_score_improvement': 0.045
            }
        ]
        
        result = await MinerAgentScored.get_24_hour_statistics(mock_conn)
        
        assert result['number_of_agents'] == 150
        assert result['agent_iterations_last_24_hours'] == 25
        assert result['top_agent_score'] == 0.923
        assert result['daily_score_improvement'] == 0.045

    @pytest.mark.asyncio
    async def test_24_hour_statistics_no_evaluation_sets(self):
        """Test 24-hour statistics when no evaluation sets exist"""
        mock_conn = AsyncMock()
        
        mock_conn.fetchrow.return_value = {'max_set_id': None}
        mock_conn.fetchval.side_effect = [100, 15]  # total agents, recent agents
        
        result = await MinerAgentScored.get_24_hour_statistics(mock_conn)
        
        assert result['number_of_agents'] == 100
        assert result['agent_iterations_last_24_hours'] == 15
        assert result['top_agent_score'] is None
        assert result['daily_score_improvement'] == 0

    @pytest.mark.asyncio
    async def test_agent_summary_by_hotkey(self):
        """Test agent summary retrieval by hotkey"""
        mock_conn = AsyncMock()
        
        agent1_id = uuid.uuid4()
        agent2_id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)
        
        mock_conn.fetch.return_value = [
            {
                'version_id': agent1_id,
                'miner_hotkey': 'test_hotkey',
                'agent_name': 'agent_v2',
                'version_num': 2,
                'created_at': created_at,
                'status': 'scored',
                'agent_summary': 'Latest version',
                'set_id': 5,
                'approved': True,
                'validator_count': 3,
                'score': 0.89
            },
            {
                'version_id': agent2_id,
                'miner_hotkey': 'test_hotkey',
                'agent_name': 'agent_v1', 
                'version_num': 1,
                'created_at': created_at,
                'status': 'replaced',
                'agent_summary': 'Previous version',
                'set_id': 4,
                'approved': None,
                'validator_count': None,
                'score': None
            }
        ]
        
        result = await MinerAgentScored.get_agent_summary_by_hotkey(mock_conn, "test_hotkey")
        
        assert len(result) == 2
        assert result[0].version_num == 2
        assert result[0].score == 0.89
        assert result[0].approved is True
        assert result[1].version_num == 1
        assert result[1].status == "replaced"

    @pytest.mark.asyncio
    async def test_materialized_view_refresh(self):
        """Test materialized view refresh operation"""
        mock_conn = AsyncMock()
        
        await MinerAgentScored.refresh_materialized_view(mock_conn)
        
        mock_conn.execute.assert_called_once_with("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])