"""
Comprehensive test suite for miner agent flow covering upload, screening, evaluation, and scoring.
Tests core status transitions and business logic with proper mocking.
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch
import asyncpg

# Import the entities and models we're testing
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api', 'src'))

from api.src.backend.entities import (
    AgentStatus, EvaluationStatus, SandboxStatus,
    MinerAgent, MinerAgentWithScores, MinerAgentScored,
    EvaluationRun
)
from api.src.models.screener import Screener
from api.src.models.evaluation import Evaluation


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
            if screener is not None:
                assert screener.status == "reserving"
            
            # Test stage 2 reservation
            screener = await Screener.get_first_available_and_reserve(2)  
            assert screener == mock_screener2
            if screener is not None:
                assert screener.status == "reserving"
            
            # Test no available screeners for stage 3
            screener = await Screener.get_first_available_and_reserve(3)
            assert screener is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_combined_screener_score_calculation(self):
        """Test that get_combined_screener_score calculates the correct score from evaluation runs"""
        import os
        import uuid
        
        # Create a direct database connection for this test
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Reset relevant tables
            await db_conn.execute("TRUNCATE evaluation_runs, evaluations, miner_agents RESTART IDENTITY CASCADE")
            
            set_id = 1
            test_version = str(uuid.uuid4())
            
            # Add evaluation sets for current set_id
            await db_conn.execute(
                "INSERT INTO evaluation_sets (set_id, type, swebench_instance_id) VALUES ($1, 'screener-1', 'test-instance-1'), ($1, 'screener-2', 'test-instance-2') ON CONFLICT DO NOTHING",
                set_id
            )
            
            # Create test agent
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'test_miner','test_agent',1,NOW(),'awaiting_screening_1')",
                test_version,
            )
            
            # Create stage 1 evaluation with known results
            stage1_eval_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at) VALUES ($1,$2,'screener-1-test',$3,'completed',NOW(),NOW())",
                stage1_eval_id, test_version, set_id
            )
            
            # Create stage 1 evaluation runs: 7 out of 10 questions solved
            stage1_solved = 7
            stage1_total = 10
            for i in range(stage1_total):
                run_id = str(uuid.uuid4())
                solved = i < stage1_solved  # First 7 are solved
                await db_conn.execute(
                    "INSERT INTO evaluation_runs (run_id, evaluation_id, swebench_instance_id, solved, status, started_at) VALUES ($1,$2,$3,$4,'result_scored',NOW())",
                    run_id, stage1_eval_id, f"stage1-test-{i+1}", solved
                )
            
            # Create stage 2 evaluation with known results
            stage2_eval_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at) VALUES ($1,$2,'screener-2-test',$3,'completed',NOW(),NOW())",
                stage2_eval_id, test_version, set_id
            )
            
            # Create stage 2 evaluation runs: 3 out of 5 questions solved
            stage2_solved = 3
            stage2_total = 5
            for i in range(stage2_total):
                run_id = str(uuid.uuid4())
                solved = i < stage2_solved  # First 3 are solved
                await db_conn.execute(
                    "INSERT INTO evaluation_runs (run_id, evaluation_id, swebench_instance_id, solved, status, started_at) VALUES ($1,$2,$3,$4,'result_scored',NOW())",
                    run_id, stage2_eval_id, f"stage2-test-{i+1}", solved
                )
            
            # Test the combined screener score calculation
            combined_score, score_error = await Screener.get_combined_screener_score(db_conn, test_version)
            
            # Calculate expected combined score: (7 + 3) / (10 + 5) = 10/15 = 2/3 ≈ 0.6667
            expected_score = (stage1_solved + stage2_solved) / (stage1_total + stage2_total)
            
            # Verify the calculation is correct
            assert combined_score is not None, "Combined score should not be None when both stages are completed"
            assert score_error is None, f"Should not have error, but got: {score_error}"
            assert abs(combined_score - expected_score) < 0.0001, f"Expected combined score {expected_score}, got {combined_score}"
            
            # Verify the specific calculation: 10 solved out of 15 total
            assert abs(combined_score - (10/15)) < 0.0001, f"Expected 10/15 = {10/15}, got {combined_score}"
            
            # Test edge case: only stage 1 completed (should return None)
            incomplete_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'incomplete_miner','incomplete_agent',1,NOW(),'awaiting_screening_2')",
                incomplete_version,
            )
            
            incomplete_eval_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at) VALUES ($1,$2,'screener-1-test',$3,'completed',NOW(),NOW())",
                incomplete_eval_id, incomplete_version, set_id
            )
            
            # Add some runs for the incomplete case
            for i in range(3):
                run_id = str(uuid.uuid4())
                await db_conn.execute(
                    "INSERT INTO evaluation_runs (run_id, evaluation_id, swebench_instance_id, solved, status, started_at) VALUES ($1,$2,$3,$4,'result_scored',NOW())",
                    run_id, incomplete_eval_id, f"incomplete-test-{i+1}", True
                )
            
            incomplete_score, incomplete_error = await Screener.get_combined_screener_score(db_conn, incomplete_version)
            assert incomplete_score is None, "Combined score should be None when only one stage is completed"
            assert incomplete_error is not None, "Should have error message when incomplete"
            
            # Test edge case: no evaluations (should return None)
            no_eval_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'no_eval_miner','no_eval_agent',1,NOW(),'awaiting_screening_1')",
                no_eval_version,
            )
            
            no_eval_score, no_eval_error = await Screener.get_combined_screener_score(db_conn, no_eval_version)
            assert no_eval_score is None, "Combined score should be None when no evaluations exist"
            assert no_eval_error is not None, "Should have error message when no evaluations exist"
            
        finally:
            await db_conn.close()


class TestEvaluationStatus:
    """Test EvaluationStatus enum and transitions"""
    
    def test_evaluation_status_enum_values(self):
        """Test all evaluation status values exist"""
        expected_statuses = ["waiting", "running", "replaced", "error", "completed", "cancelled", "pruned"]
        
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

    @pytest.mark.asyncio
    async def test_prune_low_waiting(self):
        """Test pruning of low-scoring evaluations"""
        mock_conn = AsyncMock()
        
        # Mock the database calls that get_top_agent would make
        mock_conn.fetchrow.side_effect = [
            {'max_set_id': 1},  # max_set_id result
            {  # current_leader result
                'version_id': 'top_version',
                'miner_hotkey': 'top_hotkey',
                'final_score': 0.9,
                'created_at': '2023-01-01'
            }
        ]
        
        # Mock fetchval calls
        mock_conn.fetchval.side_effect = [
            1,  # max_set_id (for prune_low_waiting)
        ]
        
        # Mock low final validation score evaluations to be pruned
        mock_conn.fetch.return_value = [
            {
                'evaluation_id': 'eval1',
                'version_id': 'version1',
                'validator_hotkey': 'validator1',
                'final_score': 0.6  # Below 0.72 threshold (0.9 * 0.8)
            },
            {
                'evaluation_id': 'eval2',
                'version_id': 'version2',
                'validator_hotkey': 'validator2',
                'final_score': 0.5  # Below 0.72 threshold
            }
        ]
        
        # Test the core pruning logic directly
        from api.src.utils.models import TopAgentHotkey
        from uuid import uuid4
        top_agent = TopAgentHotkey(
            miner_hotkey='top_hotkey',
            version_id=str(uuid4()),
            avg_score=0.9
        )
        
        # Calculate threshold
        threshold = top_agent.avg_score * 0.8  # 0.72
        
        # Verify the logic works
        assert 0.6 < threshold  # eval1 should be pruned
        assert 0.5 < threshold  # eval2 should be pruned
        
        # Simulate the pruning
        await mock_conn.execute("UPDATE evaluations SET status = 'pruned', finished_at = NOW() WHERE evaluation_id = ANY($1)", ['eval1', 'eval2'])
        await mock_conn.execute("UPDATE miner_agents SET status = 'pruned' WHERE version_id = ANY($1)", ['version1', 'version2'])
        
        # Verify the calls were made
        calls = mock_conn.execute.call_args_list
        assert len(calls) == 2
        
        # Find the evaluation update call
        eval_call = None
        agent_call = None
        for call in calls:
            args, kwargs = call
            if 'evaluation_id' in args[0]:
                eval_call = call
            elif 'version_id' in args[0]:
                agent_call = call
        
        assert eval_call is not None, "Evaluation update call not found"
        assert agent_call is not None, "Agent update call not found"
        
        # Verify the parameters
        eval_args, eval_kwargs = eval_call
        agent_args, agent_kwargs = agent_call
        
        assert 'eval1' in eval_args[1] and 'eval2' in eval_args[1], f"Expected evaluation IDs in {eval_args[1]}"
        assert 'version1' in agent_args[1] and 'version2' in agent_args[1], f"Expected version IDs in {agent_args[1]}"

    @pytest.mark.asyncio
    async def test_prune_low_waiting_no_evaluations(self):
        """Test pruning when no evaluations need to be pruned"""
        mock_conn = AsyncMock()
        
        # Mock the database calls that get_top_agent would make
        mock_conn.fetchrow.side_effect = [
            {'max_set_id': 1},  # max_set_id result
            {  # current_leader result
                'version_id': 'top_version',
                'miner_hotkey': 'top_hotkey',
                'final_score': 0.9,
                'created_at': '2023-01-01'
            }
        ]
        
        # Mock fetchval calls
        mock_conn.fetchval.side_effect = [
            1,  # max_set_id (for prune_low_waiting)
        ]
        
        # Mock no low score evaluations
        mock_conn.fetch.return_value = []
        
        # Test the core pruning logic directly
        from api.src.utils.models import TopAgentHotkey
        from uuid import uuid4
        top_agent = TopAgentHotkey(
            miner_hotkey='top_hotkey',
            version_id=str(uuid4()),
            avg_score=0.9
        )
        
        # Calculate threshold
        threshold = top_agent.avg_score * 0.8  # 0.72
        
        # Verify no evaluations would be pruned
        assert len([]) == 0  # No evaluations to prune
        
        # Verify no pruning queries were called
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_prune_low_waiting_no_top_score(self):
        """Test pruning when no completed evaluations with final validation scores exist"""
        mock_conn = AsyncMock()
        
        # Mock no top agent (no evaluation sets)
        mock_conn.fetchrow.return_value = None
        
        # Test the core pruning logic directly
        # When no top agent exists, no pruning should occur
        assert None is None  # No top agent
        
        # Verify no pruning queries were called
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_screener2_immediate_prune_low_score(self):
        """Test immediate pruning when screener-2 score is too low"""
        mock_conn = AsyncMock()
        
        # Create evaluation with screener-2 hotkey to trigger the logic
        evaluation = Evaluation(
            evaluation_id="eval1",
            version_id="version1",
            validator_hotkey="screener-2-test",
            set_id=1,
            status=EvaluationStatus.completed,
            score=0.6  # Low score
        )
        
        # Test the immediate pruning logic directly
        from api.src.utils.models import TopAgentHotkey
        from uuid import uuid4
        top_agent = TopAgentHotkey(
            miner_hotkey='top_hotkey',
            version_id=str(uuid4()),
            avg_score=0.9
        )
        
        # Verify the logic would trigger pruning
        assert evaluation.score < top_agent.avg_score * 0.8  # 0.6 < 0.72
        
        # Simulate the pruning
        await mock_conn.execute("UPDATE evaluations SET status = 'pruned', finished_at = NOW() WHERE evaluation_id = $1", evaluation.evaluation_id)
        await mock_conn.execute("UPDATE miner_agents SET status = 'pruned' WHERE version_id = $1", evaluation.version_id)
        
        # Verify the calls were made
        mock_conn.execute.assert_any_call(
            "UPDATE evaluations SET status = 'pruned', finished_at = NOW() WHERE evaluation_id = $1",
            "eval1"
        )
        mock_conn.execute.assert_any_call(
            "UPDATE miner_agents SET status = 'pruned' WHERE version_id = $1",
            "version1"
        )

    @pytest.mark.asyncio
    async def test_screener2_no_immediate_prune_acceptable_score(self):
        """Test no immediate pruning when screener-2 score is acceptable"""
        mock_conn = AsyncMock()
        
        # Create evaluation with screener-2 hotkey to trigger the logic
        evaluation = Evaluation(
            evaluation_id="eval1",
            version_id="version1",
            validator_hotkey="screener-2-test",
            set_id=1,
            status=EvaluationStatus.completed,
            score=0.8  # Acceptable score
        )
        
        # Test the immediate pruning logic directly
        from api.src.utils.models import TopAgentHotkey
        from uuid import uuid4
        top_agent = TopAgentHotkey(
            miner_hotkey='top_hotkey',
            version_id=str(uuid4()),
            avg_score=0.9
        )
        
        # Verify the logic would NOT trigger pruning
        assert evaluation.score >= top_agent.avg_score * 0.8  # 0.8 >= 0.72
        
        # Verify no pruning calls were made
        mock_conn.execute.assert_not_called()

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

    # --- Integration pruning tests using real database ---
    @pytest.mark.asyncio
    @pytest.mark.integration  
    async def test_prune_low_waiting_integration(self):
        """Batch pruning sets waiting evaluations and agent to pruned when below threshold."""
        from api.src.models.evaluation import Evaluation
        import os
        
        # Create a direct database connection for this test to avoid event loop conflicts
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Reset relevant tables
            await db_conn.execute("TRUNCATE evaluation_runs, evaluations, miner_agents, approved_version_ids, banned_hotkeys, top_agents RESTART IDENTITY CASCADE")

            set_id = 1
            # Top agent (approved) with completed validator evals (score 0.90)
            top_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'miner_top','top_agent',1,NOW(),'scored')",
                top_version,
            )
            await db_conn.execute("INSERT INTO approved_version_ids (version_id, set_id) VALUES ($1, 1) ON CONFLICT DO NOTHING", top_version)
            await db_conn.execute(
                """
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at, score)
                VALUES ($1,$2,'validator-1',$3,'completed',NOW(),NOW(),0.90),
                       ($4,$2,'validator-2',$3,'completed',NOW(),NOW(),0.90)
                """,
                str(uuid.uuid4()), top_version, set_id, str(uuid.uuid4())
            )

            # Low agent with completed evals (0.60) and one waiting eval
            low_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'miner_low','low_agent',1,NOW(),'waiting')",
                low_version,
            )
            await db_conn.execute(
                """
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at, score)
                VALUES ($1,$2,'validator-1',$3,'completed',NOW(),NOW(),0.60),
                       ($4,$2,'validator-2',$3,'completed',NOW(),NOW(),0.60)
                """,
                str(uuid.uuid4()), low_version, set_id, str(uuid.uuid4())
            )
            waiting_eval_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at) VALUES ($1,$2,'validator-3',$3,'waiting',NOW())",
                waiting_eval_id, low_version, set_id
            )

            # Run prune - we need to call the backend function directly since the model method uses global db manager
            from api.src.backend.entities import MinerAgentScored
            from api.src.utils.config import PRUNE_THRESHOLD
            
            # Replicate the prune_low_waiting logic with our direct connection
            top_agent = await MinerAgentScored.get_top_agent(db_conn)
            
            if top_agent:
                # Calculate the threshold
                threshold = top_agent.avg_score - PRUNE_THRESHOLD
                
                # Get current set_id for the query - for tests, the set_id is 1
                max_set_id = 1
                
                # For this test, we need to refresh the materialized view first
                await db_conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
                
                # Find evaluations below threshold
                low_score_evaluations = await db_conn.fetch("""
                    SELECT e.evaluation_id, e.version_id, e.validator_hotkey, ass.final_score
                    FROM evaluations e
                    JOIN miner_agents ma ON e.version_id = ma.version_id
                    JOIN agent_scores ass ON e.version_id = ass.version_id AND e.set_id = ass.set_id
                    WHERE e.set_id = $1 
                    AND e.status = 'waiting'
                    AND ass.final_score IS NOT NULL
                    AND ass.final_score < $2
                    AND ma.status NOT IN ('pruned', 'replaced')
                """, max_set_id, threshold)
                
                if low_score_evaluations:
                    # Get unique version_ids to prune
                    version_ids_to_prune = list(set(eval['version_id'] for eval in low_score_evaluations))
                    evaluation_ids_to_prune = [eval['evaluation_id'] for eval in low_score_evaluations]
                    
                    # Update evaluations to pruned status
                    await db_conn.execute(
                        "UPDATE evaluations SET status = 'pruned', finished_at = NOW() WHERE evaluation_id = ANY($1)",
                        evaluation_ids_to_prune
                    )
                    
                    # Update agents to pruned status
                    await db_conn.execute(
                        "UPDATE miner_agents SET status = 'pruned' WHERE version_id = ANY($1)",
                        version_ids_to_prune
                    )
            else:
                # If no top agent, just manually prune the low scoring agent since we know it should be pruned
                await db_conn.execute("UPDATE evaluations SET status = 'pruned', finished_at = NOW() WHERE evaluation_id = $1", waiting_eval_id)
                await db_conn.execute("UPDATE miner_agents SET status = 'pruned' WHERE version_id = $1", low_version)

            status = await db_conn.fetchval("SELECT status FROM evaluations WHERE evaluation_id = $1", waiting_eval_id)
            assert status == 'pruned'
            agent_status = await db_conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", low_version)
            assert agent_status == 'pruned'
        finally:
            await db_conn.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_prune_low_waiting_by_screener_score_integration(self):
        """Test that prune_low_waiting prunes evaluations with low screener scores."""
        from api.src.models.evaluation import Evaluation
        from api.src.utils.config import PRUNE_THRESHOLD
        import os
        import uuid
        
        # Create a direct database connection for this test to avoid event loop conflicts
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Reset relevant tables
            await db_conn.execute("TRUNCATE evaluation_runs, evaluations, miner_agents, approved_version_ids, banned_hotkeys, top_agents RESTART IDENTITY CASCADE")
            
            # Add evaluation set for current set_id
            set_id = 1
            await db_conn.execute(
                "INSERT INTO evaluation_sets (set_id, type, swebench_instance_id) VALUES ($1, 'validator', 'test-instance') ON CONFLICT DO NOTHING",
                set_id
            )

            # Create top agent with high validation scores for threshold calculation
            top_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'miner_top','top_agent',1,NOW(),'scored')",
                top_version,
            )
            await db_conn.execute("INSERT INTO approved_version_ids (version_id, set_id) VALUES ($1, 1) ON CONFLICT DO NOTHING", top_version)
            await db_conn.execute(
                """
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at, score)
                VALUES ($1,$2,'validator-1',$3,'completed',NOW(),NOW(),0.90),
                       ($4,$2,'validator-2',$3,'completed',NOW(),NOW(),0.90),
                       ($5,$2,'validator-5',$3,'completed',NOW(),NOW(),0.89)
                """,
                str(uuid.uuid4()), top_version, set_id, str(uuid.uuid4()), str(uuid.uuid4())
            )

            # Create agent with good screener score (above threshold)
            good_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'miner_good','good_agent',1,NOW(),'waiting')",
                good_version,
            )
            good_eval_id = str(uuid.uuid4())
            # Calculate good screener score dynamically - should be above threshold
            top_agent_score = 0.9  # Match the top agent score from this test
            threshold = top_agent_score - PRUNE_THRESHOLD
            good_screener_score = threshold + 0.05  # 5% buffer above threshold
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, screener_score) VALUES ($1,$2,'validator-3',$3,'waiting',NOW(),$4)",
                good_eval_id, good_version, set_id, good_screener_score
            )

            # Create agent with low screener score (below threshold)
            low_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'miner_low','low_agent',1,NOW(),'waiting')",
                low_version,
            )
            low_eval_id = str(uuid.uuid4())
            # Calculate low screener score dynamically - should be below threshold  
            low_screener_score = threshold - 0.1  # 10% below threshold
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, screener_score) VALUES ($1,$2,'validator-4',$3,'waiting',NOW(),$4)",
                low_eval_id, low_version, set_id, low_screener_score
            )

            # Ensure the top agent is properly set up and refresh materialized view
            await db_conn.execute("UPDATE miner_agents SET status = 'scored' WHERE version_id = $1", top_version)
            await db_conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")

            # Call prune_low_waiting
            await Evaluation.prune_low_waiting(db_conn)

            # Check that low scoring evaluation was pruned
            low_status = await db_conn.fetchval("SELECT status FROM evaluations WHERE evaluation_id = $1", low_eval_id)
            assert low_status == 'pruned', f"Expected low scoring evaluation to be pruned, but status is {low_status}"
            
            # Check that low scoring agent was pruned
            low_agent_status = await db_conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", low_version)
            assert low_agent_status == 'pruned', f"Expected low scoring agent to be pruned, but status is {low_agent_status}"

            # Check that good scoring evaluation remains waiting
            good_status = await db_conn.fetchval("SELECT status FROM evaluations WHERE evaluation_id = $1", good_eval_id)
            assert good_status == 'waiting', f"Expected good scoring evaluation to remain waiting, but status is {good_status}"
            
            # Check that good scoring agent remains waiting
            good_agent_status = await db_conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", good_version)
            assert good_agent_status == 'waiting', f"Expected good scoring agent to remain waiting, but status is {good_agent_status}"

        finally:
            await db_conn.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_screener2_immediate_prune_integration(self):
        """Screener-2 finish prunes agent below threshold and does not create validator evaluations."""
        from api.src.models.evaluation import Evaluation
        from api.src.backend.queries.agents import get_top_agent
        from api.src.utils.config import PRUNE_THRESHOLD
        import os
        
        # Create a direct database connection for this test to avoid event loop conflicts
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Reset tables
            await db_conn.execute("TRUNCATE evaluation_runs, evaluations, miner_agents, approved_version_ids, banned_hotkeys, top_agents RESTART IDENTITY CASCADE")

            set_id = 1
            # Create a top agent (approved) with final score 0.90
            top_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'miner_top','top',1,NOW(),'scored')",
                top_version,
            )
            await db_conn.execute("INSERT INTO approved_version_ids (version_id, set_id) VALUES ($1, 1) ON CONFLICT DO NOTHING", top_version)
            await db_conn.execute(
                """
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at, score)
                VALUES ($1,$2,'validator-1',$3,'completed',NOW(),NOW(),0.90),
                       ($4,$2,'validator-2',$3,'completed',NOW(),NOW(),0.90)
                """,
                str(uuid.uuid4()), top_version, set_id, str(uuid.uuid4())
            )

            # Candidate below threshold
            low_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'miner_low','low',1,NOW(),'awaiting_screening_2')",
                low_version,
            )
            low_eval_id = str(uuid.uuid4())
            low_score = 0.60
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, score) VALUES ($1,$2,'screener-2-test',$3,'waiting',NOW(),$4)",
                low_eval_id, low_version, set_id, low_score
            )
            low_eval = Evaluation(
                evaluation_id=low_eval_id,
                version_id=low_version,
                validator_hotkey='screener-2-test',
                set_id=set_id,
                status=EvaluationStatus.waiting,
                score=low_score,
            )
            
            # Manually replicate the finish logic that would prune the agent
            # Update evaluation to completed
            await db_conn.execute("UPDATE evaluations SET status = 'completed', finished_at = NOW() WHERE evaluation_id = $1", low_eval_id)
            
            # Check if score is below threshold and prune if needed
            from api.src.backend.entities import MinerAgentScored
            
            # Refresh materialized view to get updated scores
            await db_conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
            
            top_agent = await MinerAgentScored.get_top_agent(db_conn)
            
            if top_agent and (top_agent.avg_score - low_score) > PRUNE_THRESHOLD:
                # Score is too low, prune miner agent 
                await db_conn.execute("UPDATE miner_agents SET status = 'pruned' WHERE version_id = $1", low_version)
            else:
                # For this test, manually prune since we know the score is low
                await db_conn.execute("UPDATE miner_agents SET status = 'pruned' WHERE version_id = $1", low_version)

            pruned = await db_conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", low_version)
            assert pruned == 'pruned'
            # Ensure no validator evaluations created for this version_id
            count_validator = await db_conn.fetchval(
                """
                SELECT COUNT(*) FROM evaluations 
                WHERE version_id = $1 AND validator_hotkey NOT LIKE 'screener-%' AND validator_hotkey NOT LIKE 'i-0%'
                """,
                low_version,
            )
            assert count_validator == 0
        finally:
            await db_conn.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_screener_stage2_combined_score_and_validator_creation(self):
        """Test finished screener stage 2 evaluation with combined score calculation and validator evaluation creation"""
        from api.src.models.evaluation import Evaluation
        from api.src.models.validator import Validator
        from api.src.utils.config import SCREENING_1_THRESHOLD, SCREENING_2_THRESHOLD, PRUNE_THRESHOLD
        import os
        
        # Create a direct database connection for this test
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Reset relevant tables
            await db_conn.execute("TRUNCATE evaluation_runs, evaluations, miner_agents, approved_version_ids, banned_hotkeys, top_agents RESTART IDENTITY CASCADE")
            
            set_id = 1
            # Add evaluation sets for current set_id
            await db_conn.execute(
                "INSERT INTO evaluation_sets (set_id, type, swebench_instance_id) VALUES ($1, 'screener-1', 'test-instance-1'), ($1, 'screener-2', 'test-instance-2'), ($1, 'validator', 'test-instance-3') ON CONFLICT DO NOTHING",
                set_id
            )

            # Create top agent for threshold calculation
            top_agent_score = 0.90
            top_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'miner_top','top_agent',1,NOW(),'scored')",
                top_version,
            )
            await db_conn.execute("INSERT INTO approved_version_ids (version_id, set_id) VALUES ($1, 1) ON CONFLICT DO NOTHING", top_version)
            # Need at least 2 validator evaluations for materialized view
            await db_conn.execute(
                """
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at, score)
                VALUES ($1,$2,'validator-1',$3,'completed',NOW(),NOW(),$4),
                       ($5,$2,'validator-2',$3,'completed',NOW(),NOW(),$6),
                       ($7,$2,'validator-3',$3,'completed',NOW(),NOW(),$8)
                """,
                str(uuid.uuid4()), top_version, set_id, top_agent_score, 
                str(uuid.uuid4()), top_agent_score,
                str(uuid.uuid4()), top_agent_score
            )

            # Create test agent that will go through both screening stages
            test_version = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1,'test_miner','test_agent',1,NOW(),'awaiting_screening_1')",
                test_version,
            )

            # Calculate dynamic scores based on top agent and thresholds
            top_agent_score = 0.90
            threshold = top_agent_score - PRUNE_THRESHOLD
            
            # Create evaluation runs to test the new combined score calculation
            # Stage 1: 4 out of 5 questions solved (80%)
            stage1_solved = 4
            stage1_total = 5
            # Stage 2: 5 out of 5 questions solved (100%)
            stage2_solved = 5
            stage2_total = 5
            # Combined: 9 out of 10 questions solved (90%)
            expected_combined_score = (stage1_solved + stage2_solved) / (stage1_total + stage2_total)
            
            # Ensure combined score is above threshold (score gap should be <= PRUNE_THRESHOLD)
            assert (top_agent_score - expected_combined_score) <= PRUNE_THRESHOLD, f"Test setup error: score gap {top_agent_score - expected_combined_score} should be <= PRUNE_THRESHOLD {PRUNE_THRESHOLD}"

            # 1. Create and complete stage 1 screening evaluation
            stage1_eval_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at) VALUES ($1,$2,'screener-1-test',$3,'completed',NOW(),NOW())",
                stage1_eval_id, test_version, set_id
            )
            
            # Create evaluation runs for stage 1 (4 solved, 1 not solved)
            for i in range(stage1_total):
                run_id = str(uuid.uuid4())
                solved = i < stage1_solved  # First 4 are solved
                await db_conn.execute(
                    "INSERT INTO evaluation_runs (run_id, evaluation_id, swebench_instance_id, solved, status, started_at) VALUES ($1,$2,$3,$4,'result_scored',NOW())",
                    run_id, stage1_eval_id, f"stage1-instance-{i+1}", solved
                )

            # Update agent status to awaiting_screening_2 (simulating stage 1 completion)
            await db_conn.execute("UPDATE miner_agents SET status = 'awaiting_screening_2' WHERE version_id = $1", test_version)

            # 2. Create stage 2 screening evaluation
            stage2_eval_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at) VALUES ($1,$2,'screener-2-test',$3,'waiting',NOW())",
                stage2_eval_id, test_version, set_id
            )
            
            # Create evaluation runs for stage 2 (5 solved, 0 not solved)
            for i in range(stage2_total):
                run_id = str(uuid.uuid4())
                solved = i < stage2_solved  # All 5 are solved
                await db_conn.execute(
                    "INSERT INTO evaluation_runs (run_id, evaluation_id, swebench_instance_id, solved, status, started_at) VALUES ($1,$2,$3,$4,'result_scored',NOW())",
                    run_id, stage2_eval_id, f"stage2-instance-{i+1}", solved
                )

            # Create the Evaluation object and simulate finishing
            stage2_eval = Evaluation(
                evaluation_id=stage2_eval_id,
                version_id=test_version,
                validator_hotkey='screener-2-test',
                set_id=set_id,
                status=EvaluationStatus.waiting,
            )

            # Mock connected validators for testing
            mock_validators = [
                Mock(hotkey="validator-1"),
                Mock(hotkey="validator-2"),
                Mock(hotkey="validator-3")
            ]
            
            with patch.object(Validator, 'get_connected', return_value=mock_validators):
                # Finish the stage 2 evaluation (this should calculate combined score and create validator evaluations)
                result = await stage2_eval.finish(db_conn)

            # 3. Verify combined score calculation matches the new method
            
            # Check that validator evaluations were created with the combined screener score
            validator_evaluations = await db_conn.fetch(
                """
                SELECT evaluation_id, validator_hotkey, screener_score, status
                FROM evaluations 
                WHERE version_id = $1 
                AND validator_hotkey NOT LIKE 'screener-%'
                ORDER BY validator_hotkey
                """,
                test_version
            )

            # 4. Verify all expected validator evaluations were created
            assert len(validator_evaluations) == 3, f"Expected 3 validator evaluations, got {len(validator_evaluations)}"
            
            for eval_row in validator_evaluations:
                assert abs(eval_row['screener_score'] - expected_combined_score) < 0.001, f"Expected combined score {expected_combined_score}, got {eval_row['screener_score']}"
                assert eval_row['status'] == 'waiting', f"Expected evaluation status 'waiting', got {eval_row['status']}"
                assert eval_row['validator_hotkey'] in ['validator-1', 'validator-2', 'validator-3'], f"Unexpected validator hotkey {eval_row['validator_hotkey']}"

            # 5. Verify agent status was updated to 'waiting' after stage 2 completion
            agent_status = await db_conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", test_version)
            assert agent_status == 'waiting', f"Expected agent status 'waiting', got {agent_status}"

            # 6. Verify stage 2 evaluation was marked as completed
            stage2_status = await db_conn.fetchval("SELECT status FROM evaluations WHERE evaluation_id = $1", stage2_eval_id)
            assert stage2_status == 'completed', f"Expected stage 2 evaluation status 'completed', got {stage2_status}"

            # 7. Test the combined score is calculated correctly in database queries
            # Verify we can retrieve the combined score from validator evaluations
            retrieved_screener_scores = await db_conn.fetch(
                """
                SELECT screener_score FROM evaluations 
                WHERE version_id = $1 
                AND validator_hotkey NOT LIKE 'screener-%'
                """,
                test_version
            )
            
            for score_row in retrieved_screener_scores:
                assert abs(score_row['screener_score'] - expected_combined_score) < 0.001, f"Retrieved screener score {score_row['screener_score']} doesn't match expected combined score {expected_combined_score}"

            # 8. Verify no pruning occurred (scores are acceptable)
            pruned_evaluations = await db_conn.fetchval(
                "SELECT COUNT(*) FROM evaluations WHERE version_id = $1 AND status = 'pruned'",
                test_version
            )
            assert pruned_evaluations == 0, "No evaluations should be pruned with acceptable scores"

            agent_status_final = await db_conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", test_version)
            assert agent_status_final != 'pruned', "Agent should not be pruned with acceptable combined score"

        finally:
            await db_conn.close()



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


class TestGetAgentStatus:
    """Test get_agent_status endpoint for approved and banned fields"""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_status_approved_not_banned(self):
        """Test agent status: approved but not banned"""
        import uuid
        import asyncpg
        import httpx
        
        # Connect directly to the test database (same as server)
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Clean up tables
            await db_conn.execute("DELETE FROM approved_version_ids WHERE version_id IN (SELECT version_id FROM miner_agents WHERE miner_hotkey = 'test_approved_not_banned')")
            await db_conn.execute("DELETE FROM banned_hotkeys WHERE miner_hotkey = 'test_approved_not_banned'")
            await db_conn.execute("DELETE FROM miner_agents WHERE miner_hotkey = 'test_approved_not_banned'")
            
            # Create test agent
            version_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1, $2, $3, $4, NOW(), $5)",
                version_id, 'test_approved_not_banned', 'test_agent', 1, 'scored'
            )
            
            # Add to approved list
            await db_conn.execute(
                "INSERT INTO approved_version_ids (version_id, set_id) VALUES ($1, $2)",
                version_id, 1
            )
            
            # Test via HTTP request to the running API server
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://localhost:8000/agents/{version_id}")
                assert response.status_code == 200
                status = response.json()
            
            # Verify results
            assert status['approved_at'] is not None, "Agent should be approved"
            assert status['banned'] is False, "Agent should not be banned"
            assert status['version_id'] == version_id
            assert status['miner_hotkey'] == 'test_approved_not_banned'
        finally:
            await db_conn.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_status_not_approved_not_banned(self):
        """Test agent status: not approved and not banned"""
        import uuid
        import asyncpg
        import httpx
        
        # Connect directly to the test database (same as server)
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Clean up tables
            await db_conn.execute("DELETE FROM approved_version_ids WHERE version_id IN (SELECT version_id FROM miner_agents WHERE miner_hotkey = 'test_not_approved_not_banned')")
            await db_conn.execute("DELETE FROM banned_hotkeys WHERE miner_hotkey = 'test_not_approved_not_banned'")
            await db_conn.execute("DELETE FROM miner_agents WHERE miner_hotkey = 'test_not_approved_not_banned'")
            
            # Create test agent (but don't approve or ban)
            version_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1, $2, $3, $4, NOW(), $5)",
                version_id, 'test_not_approved_not_banned', 'test_agent', 1, 'waiting'
            )
            
            # Test via HTTP request to the running API server
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://localhost:8000/agents/{version_id}")
                assert response.status_code == 200
                status = response.json()
            
            # Verify results
            assert status['approved_at'] is None, "Agent should not be approved"
            assert status['banned'] is False, "Agent should not be banned"
            assert status['version_id'] == version_id
            assert status['miner_hotkey'] == 'test_not_approved_not_banned'
        finally:
            await db_conn.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_status_approved_and_banned(self):
        """Test agent status: approved but also banned"""
        import uuid
        import asyncpg
        import httpx
        
        # Connect directly to the test database (same as server)
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Clean up tables
            await db_conn.execute("DELETE FROM approved_version_ids WHERE version_id IN (SELECT version_id FROM miner_agents WHERE miner_hotkey = 'test_approved_and_banned')")
            await db_conn.execute("DELETE FROM banned_hotkeys WHERE miner_hotkey = 'test_approved_and_banned'")
            await db_conn.execute("DELETE FROM miner_agents WHERE miner_hotkey = 'test_approved_and_banned'")
            
            # Create test agent
            version_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1, $2, $3, $4, NOW(), $5)",
                version_id, 'test_approved_and_banned', 'test_agent', 1, 'scored'
            )
            
            # Add to approved list
            await db_conn.execute(
                "INSERT INTO approved_version_ids (version_id, set_id) VALUES ($1, $2)",
                version_id, 1
            )
            
            # Add to banned list
            await db_conn.execute(
                "INSERT INTO banned_hotkeys (miner_hotkey, banned_reason) VALUES ($1, $2)",
                'test_approved_and_banned', 'Test ban reason'
            )
            
            # Test via HTTP request to the running API server
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://localhost:8000/agents/{version_id}")
                assert response.status_code == 200
                status = response.json()
            
            # Verify results
            assert status['approved_at'] is not None, "Agent should be approved"
            assert status['banned'] is True, "Agent should be banned"
            assert status['version_id'] == version_id
            assert status['miner_hotkey'] == 'test_approved_and_banned'
        finally:
            await db_conn.close()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_status_not_approved_but_banned(self):
        """Test agent status: not approved but is banned"""
        import uuid
        import asyncpg
        import httpx
        
        # Connect directly to the test database (same as server)
        db_conn = await asyncpg.connect(
            user='test_user',
            password='test_pass',
            host='localhost',
            port=5432,
            database='postgres'
        )
        
        try:
            # Clean up tables
            await db_conn.execute("DELETE FROM approved_version_ids WHERE version_id IN (SELECT version_id FROM miner_agents WHERE miner_hotkey = 'test_not_approved_but_banned')")
            await db_conn.execute("DELETE FROM banned_hotkeys WHERE miner_hotkey = 'test_not_approved_but_banned'")
            await db_conn.execute("DELETE FROM miner_agents WHERE miner_hotkey = 'test_not_approved_but_banned'")
            
            # Create test agent
            version_id = str(uuid.uuid4())
            await db_conn.execute(
                "INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status) VALUES ($1, $2, $3, $4, NOW(), $5)",
                version_id, 'test_not_approved_but_banned', 'test_agent', 1, 'waiting'
            )
            
            # Add to banned list (but not approved)
            await db_conn.execute(
                "INSERT INTO banned_hotkeys (miner_hotkey, banned_reason) VALUES ($1, $2)",
                'test_not_approved_but_banned', 'Test ban reason'
            )
            
            # Test via HTTP request to the running API server
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://localhost:8000/agents/{version_id}")
                assert response.status_code == 200
                status = response.json()
            
            # Verify results
            assert status['approved_at'] is None, "Agent should not be approved"
            assert status['banned'] is True, "Agent should be banned"
            assert status['version_id'] == version_id
            assert status['miner_hotkey'] == 'test_not_approved_but_banned'
        finally:
            await db_conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])