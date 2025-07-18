# Agent State Machine Documentation

```mermaid
sequenceDiagram
    participant API as HTTP Upload
    participant WS as WebSocket Manager
    participant AM as AgentStateMachine
    participant EM as EvaluationStateMachine
    participant DB as Database
    
    Note over API,DB: Agent Upload Flow
    API->>WS: get_available_screener()
    WS-->>API: screener
    API->>AM: agent_upload(screener, hotkey, name, version_num, version_id)
    AM->>AM: replace_older_versions()
    loop For each old agent
        AM->>DB: UPDATE old agent status → replaced
        AM->>EM: replace_for_agent(version_id)
    end
    AM->>DB: INSERT new agent (awaiting_screening)
    AM->>EM: create_screening(version_id, screener.hotkey)
    AM->>DB: INSERT screening evaluation (waiting)
    AM->>WS: send_to_client(screener, screen-agent event)
    AM-->>API: return success
    
    Note over WS,DB: Screening Flow
    WS->>AM: start_screening(screener, evaluation_id)
    AM->>EM: start_evaluation(evaluation_id, screener.hotkey)
    EM->>DB: UPDATE evaluation (waiting → running)
    AM->>DB: UPDATE agent (awaiting_screening → screening)
    AM-->>WS: return success
    
    WS->>AM: finish_screening(screener, evaluation_id, score)
    AM->>EM: complete_with_score(evaluation_id, score)
    EM->>DB: UPDATE evaluation (running → completed)
    
    alt Score >= Threshold (Pass)
        AM->>DB: UPDATE agent (screening → waiting)
        AM->>AM: create_evaluations_for_waiting_agent()
        loop For each connected validator
            AM->>EM: create_evaluation_for_validator()
            AM->>DB: INSERT validator evaluation (waiting)
        end
        AM->>WS: send_to_all_validators(evaluation-available)
    else Score < Threshold (Fail)
        AM->>DB: UPDATE agent (screening → failed_screening)
    end
    AM-->>WS: return success
    
    Note over WS,DB: Validation Flow
    WS->>AM: start_evaluation(validator, evaluation_id)
    AM->>EM: start_evaluation(evaluation_id, validator.hotkey)
    EM->>DB: UPDATE evaluation (waiting → running)
    AM->>DB: UPDATE agent (waiting/evaluating → evaluating)
    AM-->>WS: return success
    
    WS->>AM: finish_evaluation(validator, evaluation_id, score)
    AM->>EM: complete_with_score(evaluation_id, score)
    EM->>DB: UPDATE evaluation (running → completed)
    AM->>EM: should_agent_be_scored(version_id)
    
    alt All evaluations complete
        AM->>DB: UPDATE agent (evaluating → scored)
        Note over AM: ✅ Agent Fully Evaluated
    else More evaluations running
        Note over AM: Agent stays evaluating
    end
    AM-->>WS: return success
    
    Note over AM,DB: Disconnect Handling
    WS->>AM: screener_disconnect(hotkey) / validator_disconnect(hotkey)
    AM->>EM: transition(running → error/waiting)
    AM->>DB: UPDATE evaluation status
    AM->>DB: UPDATE agent status if needed
```

## Overview

The `AgentStateMachine` is the core component managing agent evaluation lifecycle in the ridges system. It provides atomic operations, robust error handling, and seamless integration with WebSocket connections.

## Key Features

### 1. Agent Lifecycle Management
- **Upload** → **Screening** → **Evaluation** → **Scoring**
- Automatic state transitions with validation
- Comprehensive error handling and recovery

### 2. Evaluation Sets Integration
- All evaluations use proper `set_id` for consistency
- Automatic set assignment using latest available set
- Reproducible evaluations across agent versions

### 3. Connection Management
- Automatic screener/validator assignment
- Disconnect handling with state recovery
- Real-time WebSocket notifications
