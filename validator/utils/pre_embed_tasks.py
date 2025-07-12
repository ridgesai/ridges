import os
import json
import gzip
import time
import hashlib
from pathlib import Path
from openai import OpenAI
import asyncio
import ast
from typing import List, NamedTuple

Chunk = NamedTuple('Chunk', [('file', str), ('start_line', int), ('end_line', int), ('text', str)])

from validator.config import EASY_INSTANCES, MEDIUM_INSTANCES
from swebench.harness.run_evaluation import load_swebench_dataset
from validator.sandbox.clone_repo import clone_repo
from validator.utils.logging import get_logger
from validator.sandbox.schema import AgentVersion
from datetime import datetime

logger = get_logger(__name__)

REPO_EMBEDS_DIR = Path(__file__).parent.parent / 'repo_embeds'

def average_vectors(vectors):
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(len(vectors[0]))]

def _collect_code_chunks(repo_dir: Path) -> List[Chunk]:
    chunks = []
    for root, _, files in os.walk(repo_dir):
        for file in files:
            if file.endswith('.py'):
                file_path = Path(root) / file
                with open(file_path, 'r') as f:
                    code = f.read()
                try:
                    tree = ast.parse(code)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            start = node.lineno
                            end = max((getattr(n, 'end_lineno', node.lineno) for n in ast.walk(node)), default=node.lineno)
                            text = ast.unparse(node)
                            chunks.append(Chunk(str(file_path.relative_to(repo_dir)), start, end, text))
                except Exception:
                    # Fallback to whole file if AST fails
                    chunks.append(Chunk(str(file_path.relative_to(repo_dir)), 1, code.count('\n') + 1, code))
    return chunks

async def generate_embeddings():
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    if not client:
        logger.error('OpenAI API key not set')
        return
    tasks = EASY_INSTANCES + MEDIUM_INSTANCES
    REPO_EMBEDS_DIR.mkdir(exist_ok=True)
    config_hash = hashlib.sha256(str(tasks).encode()).hexdigest()
    manifest_path = REPO_EMBEDS_DIR / 'manifest.json'
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
        if manifest.get('config_hash') == config_hash:
            logger.info('Embeddings up to date')
            return
    instances = load_swebench_dataset('SWE-bench/SWE-bench_Verified', 'test', tasks)
    for instance in instances:
        task_id = instance['instance_id']
        repo = instance['repo']
        base_commit = instance['base_commit']
        # Clone repo
        repo_dir = clone_repo(REPO_EMBEDS_DIR / task_id, repo, base_commit)
        # Collect function chunks
        chunks = _collect_code_chunks(repo_dir)
        # Batch embed
        batches = [chunks[i:i+50] for i in range(0, len(chunks), 50)]
        for batch in batches:
            texts = [c.text for c in batch]
            if not texts:
                continue
            response = client.embeddings.create(model='text-embedding-3-large', input=texts)
            for i, emb in enumerate(response.data):
                batch[i].vector = emb.embedding  # Add to Chunk (assume extend Chunk with vector attr or store separately)
        # Store
        with gzip.open(REPO_EMBEDS_DIR / f'{task_id}.json.gz', 'wt') as f:
            json.dump({'chunks': [c._asdict() for c in chunks]}, f)  # If using NamedTuple
    # Update manifest
    with open(manifest_path, 'w') as f:
        json.dump({'config_hash': config_hash, 'timestamp': time.time()}, f)
    logger.info('Embeddings generated') 