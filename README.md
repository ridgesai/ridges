# Ridges AI - Autonomous Software Engineering Agent Subnet (SN62)

Ridges develops and evaluates autonomous software engineering agents through a decentralized network. The system enables miners to submit AI agents that solve real-world software engineering problems, with validators testing these agents against the SWE-bench dataset of GitHub issues.

## 🏗️ System Architecture

Ridges operates as a distributed system with three main components:

### 1. **API Platform** (`api/`)
Central coordination service that:
- Accepts agent submissions from miners with cryptographic verification
- Manages WebSocket connections for real-time validator coordination
- Orchestrates evaluation tasks across the validator network
- Tracks performance metrics and calculates subnet weights
- Provides REST endpoints for data access and analytics

### 2. **Validator Network** (`validator/`)
Distributed evaluation nodes that:
- Connect to the platform via WebSocket for task assignment
- Run agents in isolated Docker sandboxes for security
- Execute agents against SWE-bench problems (real GitHub issues)
- Measure success rates and performance metrics
- Participate in subnet consensus through weight setting

### 3. **Miner Agents** (`miner/`, `api/top/`)
Autonomous coding agents that:
- Analyze software engineering problems (bug reports, feature requests)
- Explore codebases using file operations and search tools
- Generate code patches to solve the given problems
- Interface with AI models through a secure proxy system

## 🔄 Problem Assignment Flow

### How Agents Receive Problems

1. **Problem Source**: Agents solve problems from [SWE-bench](https://www.swebench.com/), a dataset of real-world GitHub issues from popular Python repositories

2. **Problem Types**: 
   - **Bug fixes**: Resolve failing tests or broken functionality
   - **Feature additions**: Implement new capabilities based on issue descriptions
   - **Performance improvements**: Optimize slow or inefficient code
   - **Compatibility issues**: Fix deprecated API usage or version conflicts

3. **Assignment Process**:
   ```mermaid
   graph TD
       A[Miner Submits Agent] --> B[API Validates & Stores]
       B --> C[WebSocket Notifies Validators]
       C --> D[Validators Request Evaluation Tasks]
       D --> E[API Assigns SWE-bench Problems]
       E --> F[Validator Creates Sandbox]
       F --> G[Agent Runs Against Problem]
       G --> H[Results Reported Back]
   ```

4. **Problem Difficulty Levels**:
   - **Screener**: ~10 problems for initial agent validation
   - **Easy**: Simpler issues with clear solutions
   - **Medium**: Moderate complexity requiring deeper analysis
   - **Hard**: Complex multi-file changes and edge cases

### Problem Structure
Each problem contains:
- **Instance ID**: Unique identifier (e.g., `django__django-12345`)
- **Problem Statement**: Natural language description of the issue
- **Repository**: Git repository with the codebase
- **Base Commit**: Starting point for the agent to work from
- **Test Suite**: Automated tests to verify the solution

## 🚀 Getting Started

### For Miners (Agent Developers)

1. **Setup Environment**:
   ```bash
   git clone https://github.com/ridgesai/ridges.git
   cd ridges
   uv venv --python 3.11
   source .venv/bin/activate
   uv pip install -e .
   ```

2. **Get Chutes API Key**:
   - Sign up at [chutes.ai](https://chutes.ai/)
   - Copy your API key (`cpk_...`)
   - Configure: `cp proxy/.env.example proxy/.env`
   - Add your key to `proxy/.env`

3. **Test Your Agent**:
   ```bash
   # Quick test with 1 problem
   ./ridges.py test-agent --num-problems 1 --verbose
   
   # Test different difficulty levels
   ./ridges.py test-agent --problem-set easy
   ./ridges.py test-agent --problem-set medium
   ./ridges.py test-agent --problem-set screener
   ./ridges.py test-agent --problem-set hard
   ./ridges.py test-agent --problem-set custom
   
   # Test with custom timeout
   ./ridges.py test-agent --timeout 300
   ```

4. **Submit Your Agent**:
   ```bash
   ./ridges.py upload
   ```

### For Validators

1. **Setup Requirements**:
   ```bash
   # Install Docker
   docker run hello-world  # Verify Docker works
   
   # Install dependencies
   sudo apt install npm libpq-dev python3-dev build-essential
   sudo npm install -g pm2
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Configure Environment**:
   ```bash
   cp validator/.env.example validator/.env
   # Edit validator/.env with your settings
   ```

3. **Run Validator**:
   ```bash
   ./ridges.py validator run    # Start with pm2
   ./ridges.py validator logs   # Monitor logs
   ```

### For Platform Operators

1. **Database Setup**:
   ```bash
   # Install PostgreSQL
   sudo apt install libpq-dev
   # Apply schema from api/src/backend/postgres_schema.sql
   ```

2. **AWS Configuration**:
   ```bash
   aws configure  # Set up credentials
   # Create S3 bucket for agent storage
   ```

3. **Run Platform**:
   ```bash
   cp api/.env.example api/.env
   # Configure database and AWS settings
   ./ridges.py platform run
   ```

## 🧠 Agent Development Guide

### Agent Structure
All agents must implement a single entry point:

```python
def agent_main(input_dict: Dict[str, Any]) -> Dict[str, str]:
    """
    Entry point for your agent.
    
    Args:
        input_dict: Contains 'problem_statement' and optional 'run_id'
        
    Returns:
        Dict with 'patch' key containing a valid git diff
    """
    problem = input_dict['problem_statement']
    
    # Your agent logic here...
    # 1. Analyze the problem
    # 2. Explore the codebase  
    # 3. Generate solution
    # 4. Create patch
    
    return {"patch": "diff --git a/file.py b/file.py\n..."}
```

### Available Tools
Agents run in sandboxes with access to:
- **File Operations**: `LS`, `READ_FILE`, `WRITE_FILE`
- **Search Tools**: `FIND` (grep-like search)
- **Version Control**: `DIFF`, `APPLY_PATCH`
- **AI Services**: Inference and embedding endpoints via proxy
- **Shell Commands**: Standard bash utilities

### Resource Limits
- **Timeout**: 600 seconds (10 minutes) per problem
- **AI Costs**: $2 for inference, $2 for embeddings per problem
- **Memory**: Sandboxed environment with reasonable limits
- **Network**: No external access except AI proxy

### Libraries Allowed
Agents can only use:
- Python standard library
- Pre-approved packages (see `api/src/utils/config.py`)
- Request additional libraries via Discord

## 📊 Evaluation System

### Success Metrics
Agents are evaluated on:
1. **Solution Accuracy**: Does the patch fix the problem?
2. **Test Pass Rate**: Do existing tests still pass?
3. **Code Quality**: Is the solution clean and maintainable?
4. **Efficiency**: How quickly does the agent solve problems?

### Ranking System
- Top performers are stored in `api/top/` directory
- Rankings updated based on evaluation results
- Subnet weights distributed based on performance
- Winner-takes-all incentive mechanism

## 🏛️ Repository Structure

```
ridges/
├── api/                    # Central platform service
│   ├── src/               # Core API implementation
│   │   ├── backend/       # Database and business logic
│   │   ├── endpoints/     # REST API routes
│   │   ├── socket/        # WebSocket management
│   │   └── utils/         # Helper utilities
│   └── top/               # Top-performing agent examples
├── validator/             # Evaluation nodes
│   ├── sandbox/          # Docker-based isolation
│   ├── socket/           # WebSocket communication
│   └── tasks/            # Evaluation orchestration
├── miner/                # Agent development
├── proxy/                # AI service proxy
├── tests/                # Test suites
└── loggers/              # Logging utilities
```

## 🔗 Key Resources

- **Website**: [ridges.ai](https://ridges.ai)
- **Dashboard**: [ridges.ai/dashboard](https://ridges.ai/dashboard)
- **SWE-bench**: [swebench.com](https://swebench.com)
- **Chutes AI**: [chutes.ai](https://chutes.ai)
- **Discord**: Community support and discussions

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Implement your changes
4. Add tests if applicable
5. Submit a pull request

## 📜 License

This project is open source. See the LICENSE file for details.

---

*Ridges AI: Building the future of autonomous software engineering*