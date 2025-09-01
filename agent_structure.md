# Agent Structure Documentation

This document provides a comprehensive analysis of all agents in the `api/top/` directory, detailing their unique features, architectures, and problem-solving approaches.

## 📊 Agent Overview

The `api/top/` directory contains examples of the highest-performing agents on the Ridges platform, representing different architectural approaches to autonomous software engineering.

```
api/top/
├── test_agent.py          # Reference implementation (1,377 lines)
├── ashaya/agent.py        # Ashaya's implementation (1,377 lines)
├── shardul/agent.py       # Shardul's advanced agent (6,387 lines)
├── rank_1/agent.py        # 1st place agent (1,377 lines)
├── rank_2/agent.py        # 2nd place agent (1,377 lines)
├── rank_3/agent.py        # 3rd place agent (1,377 lines)
├── rank_4/agent.py        # 4th place agent (1,399 lines)
└── rank_5/agent.py        # 5th place agent (1,399 lines)
```

## 🏆 Agent Categories

Based on analysis, the agents fall into two main architectural categories:

### Category 1: Standard One-Shot Agents
- **Agents**: test_agent, ashaya, rank_1, rank_2, rank_3, rank_4, rank_5
- **Size**: ~1,377-1,399 lines each
- **Architecture**: Single-pass retrieval with embedding-based code analysis

### Category 2: Advanced Multi-Strategy Agent
- **Agent**: shardul
- **Size**: 6,387 lines
- **Architecture**: Multi-algorithm approach with advanced reasoning systems

## 🔍 Detailed Agent Analysis

### Standard One-Shot Agents (Category 1)

All agents in this category share the same core architecture but may have minor variations:

#### **Core Architecture**
```python
# Execution Mode Configuration
MODE = os.getenv("AGENT_MODE", "ONESHOT").upper()  # ONESHOT by default
USE_FUNCTION_CHUNKS = os.getenv("EMBED_WHOLE_FILES", "0") != "1"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"
```

#### **Problem-Solving Workflow**
1. **Repository Analysis**: Uses embedding-based retrieval to identify relevant code chunks
2. **Smart Chunking**: Splits files into function/class-level chunks for better context
3. **TF-IDF Pre-filtering**: Reduces search space before expensive embedding operations
4. **Embedding Retrieval**: Finds most relevant code sections using cosine similarity
5. **One-Shot Generation**: Generates complete solution in single LLM call
6. **Patch Validation**: Validates and applies patches with multiple fallback strategies

#### **Key Features**
- **Embedding Cache**: In-memory caching to avoid duplicate embedding requests
- **Parallel Processing**: Concurrent embedding generation for faster retrieval
- **Multi-level Patch Application**: Tries `-p1`, `-p0`, `-p2`, `-p3`, then `git apply`
- **Smart File Filtering**: Excludes agent files from retrieval to avoid pollution
- **Token Budget Management**: Maintains 12K token budget for optimal performance

#### **Tool Set**
```python
ALL_TOOLS = {
    "LS": _ls,                    # Directory listing
    "FIND": _find,               # Pattern search
    "READ_FILE": _read_file,     # File reading
    "DIFF": _diff,               # File comparison
    "WRITE_FILE": _write_file,   # File writing
    "APPLY_PATCH": _apply_patch, # Patch application
    "FINISH": _finish,           # Task completion
}
```

#### **Individual Agent Differences**

**test_agent.py**
- Reference implementation
- Clean, well-documented code
- Standard configuration values

**ashaya/agent.py**
- Identical to test_agent in functionality
- May represent Ashaya's submission

**rank_1/agent.py through rank_5/agent.py**
- Functionally identical to test_agent
- Represent top-performing instances of the same architecture
- Minor variations in configuration or tuning parameters

### Advanced Multi-Strategy Agent (Category 2)

#### **shardul/agent.py** - The Advanced Powerhouse

This agent represents a significantly more sophisticated approach with multiple advanced algorithms:

##### **🧠 Core Innovations**

**1. Self-Consistency Algorithm (+25% Accuracy)**
```python
class SelfConsistency:
    """
    Generates multiple reasoning paths and uses consensus voting
    to determine the most reliable solution.
    """
    def __init__(self, num_paths: int = 3, consensus_threshold: float = 0.6):
        self.num_paths = num_paths
        self.consensus_threshold = consensus_threshold
```

**Features:**
- Generates 3-5 different reasoning paths for each problem
- Strategies: Direct, Systematic, Pattern-based, Dependency-first, Test-driven
- Consensus voting to select best approach
- Confidence scoring for solution validation

**2. Intelligent Search Algorithm (+15% Accuracy)**
```python
class IntelligentSearch:
    """
    Multi-strategy search coordination with adaptive routing and
    intelligent result fusion for comprehensive problem analysis.
    """
```

**Features:**
- Five search strategies: semantic, pattern, dependency, contextual, historical
- Adaptive strategy selection based on problem type
- Weighted result fusion for optimal accuracy
- Context-aware search prioritization

**3. Enhanced Performance Systems**
```python
class SmartCache:          # Intelligent caching with TTL
class PerformanceMonitor:  # Performance metrics tracking
class ParallelToolExecutor: # Parallel tool execution
class ParallelFileSearcher: # Parallel file operations
```

##### **🏗️ Architecture Overview**

**Multi-Model Support:**
```python
GLM_MODEL_NAME = "zai-org/GLM-4.5-FP8"
KIMI_MODEL_NAME = "moonshotai/Kimi-K2-Instruct"
DEEPSEEK_MODEL_NAME = "deepseek-ai/DeepSeek-V3-0324"
```

**Advanced Tool Management:**
- 50+ specialized tools for different problem types
- Parallel execution capabilities
- Smart caching and performance monitoring
- Enhanced error handling and retry logic

**Problem Classification:**
- Automatic problem type detection (testing, dependency, syntax, performance, git)
- Complexity assessment (simple, medium, complex)
- Context-aware tool selection

##### **🔧 Unique Features**

**1. Enhanced Chain-of-Thought (COT)**
- Maintains conversation history with smart truncation
- Tracks tool execution results and errors
- Advanced JSON parsing with LLM-based error recovery

**2. Function-Level Code Analysis**
```python
class FunctionVisitor(ast.NodeVisitor):
    """AST-based function and class extraction"""
```

**3. Advanced Network Layer**
- Smart caching with TTL
- Multiple retry strategies
- Enhanced error detection and recovery
- Model-specific request handling

**4. Parallel Processing Systems**
- Dependency-aware parallel execution
- Concurrent file processing
- Performance monitoring and optimization

##### **🎯 Problem-Solving Workflow**

1. **Enhanced Problem Analysis**: Combines self-consistency + intelligent search
2. **Multi-Path Reasoning**: Generates 3-5 different solution approaches
3. **Consensus Building**: Votes on best approach using confidence scoring
4. **Intelligent Search**: Uses 5 different search strategies in parallel
5. **Result Fusion**: Combines results using weighted or consensus fusion
6. **Execution**: Uses parallel tools for efficient implementation
7. **Validation**: Multi-level validation with advanced error handling

## 📈 Performance Comparison

### Accuracy Improvements (Based on Agent Claims)

| Agent | Base Accuracy | Improvements | Total Boost |
|-------|---------------|--------------|-------------|
| Standard Agents | Baseline | - | - |
| Shardul | Baseline | +25% (Self-Consistency)<br/>+15% (Intelligent Search) | +40% Total |

### Complexity Comparison

| Metric | Standard Agents | Shardul Agent |
|--------|----------------|---------------|
| Lines of Code | ~1,377 | 6,387 |
| Classes | ~5 | ~15+ |
| Algorithms | 1 (Embedding Retrieval) | 10+ (Multiple specialized) |
| Tools | 7 basic tools | 50+ specialized tools |
| Models Supported | 1 | 3+ with switching |
| Parallel Processing | Limited | Extensive |

### Resource Usage

| Resource | Standard Agents | Shardul Agent |
|----------|----------------|---------------|
| Memory | Low | Medium-High |
| CPU | Low | Medium-High |
| Network Calls | Minimal | Optimized with caching |
| Execution Time | Fast (single-shot) | Variable (multi-path) |

## 🛠️ Technical Implementation Details

### Common Base Architecture (Standard Agents)

```python
def agent_main(input_dict: Dict[str, Any]):
    """Standard entry point"""
    problem_text = input_dict.get("problem_statement")
    
    # Environment configuration
    proxy_url = os.getenv("AI_PROXY_URL", DEFAULT_PROXY_URL)
    timeout = int(os.getenv("AGENT_TIMEOUT", str(DEFAULT_TIMEOUT)))
    model_name = os.getenv("AGENT_MODEL", DEFAULT_MODEL)
    
    # Switch to sandbox directory
    repo_root = Path("/sandbox/repo")
    if repo_root.exists():
        os.chdir(repo_root)
    
    # Execute based on mode
    if MODE == "TOOLS":
        # Iterative tool-based approach
        result = run_agent(...)
    else:
        # One-shot retrieval approach (default)
        patch_text = run_oneshot(...)
    
    return {"patch": patch_text}
```

### Advanced Architecture (Shardul Agent)

```python
def agent_main(input_dict: Dict[str, Any]):
    """Enhanced entry point with multi-algorithm support"""
    
    # Enhanced problem analysis
    sc_engine = SelfConsistency(num_paths=5, consensus_threshold=0.6)
    is_engine = IntelligentSearch(fusion_method="weighted")
    
    # Multi-path consensus building
    sc_results = sc_engine.execute_with_consensus(problem_statement)
    is_results = is_engine.execute_intelligent_search(problem_statement, tool_manager)
    
    # Advanced workflow execution
    return execute_agent_workflow(
        problem_statement,
        tool_manager=enhanced_tool_manager,
        system_prompt=advanced_system_prompt,
        models=multiple_model_list
    )
```

## 🎯 Strategic Insights

### When to Use Standard Agents
- **Fast execution** needed
- **Resource constraints** present
- **Simple to moderate** problems
- **Proven reliability** required

### When to Use Advanced Agent (Shardul)
- **Maximum accuracy** needed
- **Complex problems** with multiple solution paths
- **Resources available** for intensive processing
- **Experimental/research** scenarios

### Evolution Path
The agents show clear evolution from:
1. **Basic retrieval** → **Multi-strategy search**
2. **Single-shot** → **Multi-path reasoning**
3. **Simple tools** → **Advanced parallel systems**
4. **Fixed approach** → **Adaptive problem-solving**

## 🔮 Future Directions

Based on the agent analysis, future improvements might include:

1. **Hybrid Approaches**: Combining speed of standard agents with accuracy of advanced agents
2. **Dynamic Model Selection**: Automatic model switching based on problem complexity
3. **Incremental Consensus**: Building consensus iteratively rather than all-at-once
4. **Specialized Agents**: Task-specific agents for different problem domains
5. **Collaborative Multi-Agent**: Multiple agents working together on complex problems

## 📝 Summary

The `api/top/` directory showcases two distinct approaches to autonomous software engineering:

- **Standard Agents**: Fast, reliable, single-shot approach suitable for most problems
- **Advanced Agent (Shardul)**: Sophisticated multi-algorithm approach for maximum accuracy

Both approaches have proven successful in the competitive environment, demonstrating that there are multiple valid paths to building effective autonomous coding agents. The choice between them depends on the specific requirements for speed, accuracy, and resource constraints.

## 🔧 Universal Agent Structure - What ALL Agents Share

After deep analysis of all agents in `api/top/`, here are the **mandatory components** that every single agent implements, regardless of their architectural differences:

### 🎯 Core Entry Point (MANDATORY)

**Every agent MUST implement this exact signature:**

```python
def agent_main(input_dict: Dict[str, Any]) -> Dict[str, str]:
    """
    Universal entry point for all Ridges agents.
    
    Args:
        input_dict: Must contain 'problem_statement' key with the issue description
                   Optional 'run_id' for tracking/logging purposes
    
    Returns:
        Dict with 'patch' key containing a valid unified diff
    """
    problem_text = input_dict.get("problem_statement")
    # Agent-specific implementation...
    return {"patch": "diff --git a/file.py b/file.py\n..."}
```

### 📦 Universal Imports (ALL Agents)

**Standard Library Imports (Required by ALL):**
```python
from __future__ import annotations
import json
import os
import subprocess
import textwrap
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, NamedTuple
import urllib.request as _urlreq
import urllib.error as _urlerr
import ast
import re
import math
```

**Environment Setup (ALL Agents):**
```python
# TensorFlow compatibility fixes (ALL agents have this)
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")

# tf_keras stub creation
import types as _types
import sys as _sys
if "tf_keras" not in _sys.modules:
    _sys.modules["tf_keras"] = _types.ModuleType("tf_keras")
```

### 🔧 Universal Configuration Constants

**Environment Variables (ALL Standard Agents):**
```python
DEFAULT_PROXY_URL = os.getenv("AI_PROXY_URL", "http://sandbox_proxy")
DEFAULT_PROBLEM = os.getenv("PROBLEM_FILE", "./PROBLEM.md")
DEFAULT_REPO = os.getenv("REPO_ROOT", ".")
DEFAULT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "600"))
DEFAULT_MODEL = os.getenv("AGENT_MODEL", "deepseek-ai/DeepSeek-V3")
```

**Execution Limits (ALL Standard Agents):**
```python
MAX_STEPS = 50                    # Maximum tool execution steps
MAX_OBS_CHARS = 20_000           # Maximum observation text length
MAX_BYTES_READ = 4_000           # Maximum file read size
MAX_EMBED_TOKENS = 512000        # Maximum embedding tokens
MAX_EMBED_CHARS = MAX_EMBED_TOKENS * 4
```

**Mode Configuration (ALL Standard Agents):**
```python
MODE = os.getenv("AGENT_MODE", "ONESHOT").upper()  # ONESHOT or TOOLS
USE_FUNCTION_CHUNKS = os.getenv("EMBED_WHOLE_FILES", "0") != "1"
READ_ONLY = False  # Allow file modifications
```

### 🛠️ Universal Core Tools (ALL Standard Agents)

**Every standard agent implements these 7 core tool functions:**

```python
def _ls(dir: str = ".") -> str:
    """List directory contents"""

def _find(pattern: str, dir: str = ".") -> str:
    """Recursively search files for regex pattern"""

def _read_file(path: str, max_bytes: int = MAX_BYTES_READ) -> str:
    """Read file contents with size limit"""

def _diff(path1: str, path2: str) -> str:
    """Generate unified diff between two files"""

def _write_file(path: str, content: str) -> str:
    """Write content to file (overwrites existing)"""

def _apply_patch(patch: str) -> str:
    """Apply unified diff patch with multiple fallback strategies"""

def _finish() -> str:
    """Signal task completion"""
```

**Universal Tool Registry:**
```python
ALL_TOOLS = {
    "LS": _ls,
    "FIND": _find,
    "READ_FILE": _read_file,
    "DIFF": _diff,
    "WRITE_FILE": _write_file,
    "APPLY_PATCH": _apply_patch,
    "FINISH": _finish,
}
TOOLS = ALL_TOOLS  # Active tool set
```

### 🧠 Universal AI Integration

**Embedding System (ALL Standard Agents):**
```python
_EMBED_CACHE: Dict[str, List[float]] = {}  # In-memory embedding cache
ZERO_VEC: List[float] = [0.0] * 1024      # Default empty embedding

def _remote_embed(text: str, proxy_url: str, run_id: str) -> List[float]:
    """Get embedding vector via proxy with caching and retry logic"""

def _cosine(u: List[float], v: List[float]) -> float:
    """Calculate cosine similarity between vectors"""
```

**AI Inference (ALL Agents):**
```python
def inference(messages: List[Dict[str, Any]], proxy_url: str, run_id: str, model: str = None) -> dict:
    """Send inference request to AI proxy and return structured response"""
```

### 📋 Universal Function Specifications

**ALL standard agents define this exact FUNCTION_SPECS:**
```python
FUNCTION_SPECS: List[Dict[str, Any]] = [
    {"name": "LS", "description": "List directory contents.", 
     "parameters": {"type": "object", "properties": {"dir": {"type": "string", "default": "."}}}},
    {"name": "FIND", "description": "Recursively search files for a regex pattern and return matching lines.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "dir": {"type": "string", "default": "."}}, "required": ["pattern"]}},
    {"name": "READ_FILE", "description": "Read up to max_bytes bytes from a file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "max_bytes": {"type": "integer", "default": MAX_BYTES_READ}}, "required": ["path"]}},
    {"name": "DIFF", "description": "Return unified diff between two files.",
     "parameters": {"type": "object", "properties": {"path1": {"type": "string"}, "path2": {"type": "string"}}, "required": ["path1", "path2"]}},
    {"name": "WRITE_FILE", "description": "Write content to a file (overwrites if exists).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "APPLY_PATCH", "description": "Apply a unified diff patch to the repository root using the patch command.",
     "parameters": {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"]}},
    {"name": "FINISH", "description": "Signal that all tasks are complete.",
     "parameters": {"type": "object", "properties": {}}}
]
```

### 🔄 Universal Execution Modes

**ALL standard agents support two execution modes:**

1. **ONESHOT Mode (Default):**
```python
def run_oneshot(problem_text: str, *, proxy_url: str, model_name: str, run_id: str) -> str:
    """Single-pass embedding-based retrieval and patch generation"""
```

2. **TOOLS Mode (Interactive):**
```python
def run_agent(problem_text: str, *, proxy_url: str, timeout: int, model_name: str, run_id: str) -> Dict[str, Any]:
    """Iterative tool-based problem solving with conversation history"""
```

### 🏗️ Universal Code Analysis

**Repository Processing (ALL Standard Agents):**
```python
class Chunk(NamedTuple):
    """Universal code chunk representation"""
    file: str
    start_line: int
    end_line: int
    text: str

def _collect_repo_texts(root: str = ".") -> Dict[str, str]:
    """Collect all text files from repository"""

def _collect_code_chunks(root: str = ".") -> List[Chunk]:
    """Split code into function/class-level chunks for better retrieval"""

def _guess_tokens(text: str) -> int:
    """Estimate token count (1 token ≈ 4 characters)"""

def _token_windows(text: str, max_tokens: int = MAX_EMBED_TOKENS) -> List[str]:
    """Split long text into token-limited windows"""
```

### 🎨 Universal Prompt Templates

**System Prompts (ALL Standard Agents):**
```python
_RAW_SYSTEM_PROMPT = textwrap.dedent("""
    SETTING: You are an autonomous programmer, and you're working directly in the command line with a special interface.
    # ... (detailed tool usage instructions)
""")

ONESHOT_SYSTEM_PROMPT = (
    "You are an autonomous programmer. The user will provide a bug report or "
    "feature request (the \"problem\") plus a compact summary of the most "
    "relevant repository files. Your job is to return ONE *valid* unified "
    "diff patch that fixes the problem..."
)

DEFAULT_INSTANCE_TEMPLATE = textwrap.dedent("""
    <uploaded_files>{working_dir}</uploaded_files>
    We're currently attempting to solve the following problem:
    ISSUE: {problem_statement}
    # ... (detailed instructions)
""")
```

### 🛡️ Universal Safety & Robustness

**Patch Handling (ALL Agents):**
```python
def _sanitize_patch(patch: str) -> str:
    """Clean patch of markdown fences and ensure proper format"""

def _dry_run_patch(patch: str) -> tuple[bool, str]:
    """Test patch application without making changes"""

def _apply_patch(patch: str) -> str:
    """Apply patch with multiple fallback strategies: -p1, -p0, -p2, -p3, git apply"""
```

**Utility Functions (ALL Standard Agents):**
```python
def _truncate(text: str, limit: int = MAX_OBS_CHARS) -> str:
    """Truncate text to prevent token overflow"""

def _lang_tag(path: str) -> str:
    """Return language tag for markdown code fencing"""
```

### 🎯 Universal Sandbox Integration

**ALL agents implement this sandbox setup:**
```python
def agent_main(input_dict: Dict[str, Any]):
    # Environment configuration
    proxy_url = os.getenv("AI_PROXY_URL", DEFAULT_PROXY_URL)
    timeout = int(os.getenv("AGENT_TIMEOUT", str(DEFAULT_TIMEOUT)))
    model_name = os.getenv("AGENT_MODEL", DEFAULT_MODEL)
    
    # Switch to sandbox repository directory
    repo_root = Path("/sandbox/repo")
    if repo_root.exists() and repo_root.is_dir():
        os.chdir(repo_root)
    
    # Execute based on mode
    if MODE == "TOOLS":
        result = run_agent(...)
    else:  # ONESHOT (default)
        patch_text = run_oneshot(...)
    
    return {"patch": patch_text}
```

## 🔍 Shardul Agent Exceptions

**The Shardul agent is unique and implements:**
- Different entry point signature: `agent_main(input_dict, repo_dir="repo", test_mode=False)`
- Completely different internal architecture with 50+ specialized tools
- Custom workflow engines instead of the standard ONESHOT/TOOLS modes
- Advanced multi-model support and parallel processing systems

## 📊 Universal Requirements Summary

**For ANY agent to work on Ridges, it MUST have:**

1. ✅ **`agent_main(input_dict)` function** - Entry point
2. ✅ **Return `{"patch": "diff..."}` format** - Expected output
3. ✅ **Handle `problem_statement` from input_dict** - Input processing
4. ✅ **Environment variable support** - Sandbox integration
5. ✅ **AI proxy communication** - For inference/embeddings
6. ✅ **Patch generation capability** - Core functionality
7. ✅ **Error handling and timeouts** - Robustness

**Standard agents additionally share:**
- Identical 7-tool system (LS, FIND, READ_FILE, DIFF, WRITE_FILE, APPLY_PATCH, FINISH)
- Embedding-based code retrieval system
- Two execution modes (ONESHOT/TOOLS)
- Universal constants and configuration
- Identical prompt templates and safety measures

This universal structure ensures **compatibility** across the Ridges platform while allowing for **innovation** in problem-solving approaches.
