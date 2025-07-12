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
import tempfile
import shutil

# Add missing imports
from validator.config import EASY_INSTANCES, MEDIUM_INSTANCES
from validator.utils.logging import get_logger
from validator.sandbox.clone_repo import clone_repo
from swebench.harness.run_evaluation import load_swebench_dataset

# Define REPO_EMBEDS_DIR
REPO_EMBEDS_DIR = Path(__file__).parent.parent / 'repo_embeds'

# Add EMBED_VERSION to match main.py
EMBED_VERSION = "1.0"

# Initialize logger
logger = get_logger(__name__)

def average_vectors(vectors):
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(len(vectors[0]))]

def _collect_code_chunks(repo_dir: Path) -> List[dict]:
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
                            if len(text) > 2000:
                                lines = text.splitlines()
                                chunk_size = 50
                                sub_texts = ['\n'.join(lines[i:i+chunk_size]) for i in range(0, len(lines), chunk_size)]
                            else:
                                sub_texts = [text]
                            chunks.append({'file': str(file_path.relative_to(repo_dir)), 'start_line': start, 'end_line': end, 'text': text, 'sub_texts': sub_texts})
                except Exception:
                    chunks.append({'file': str(file_path.relative_to(repo_dir)), 'start_line': 1, 'end_line': code.count('\n') + 1, 'text': code})
                    if len(code) > 2000:
                        lines = code.splitlines()
                        chunk_size = 50
                        sub_texts = ['\n'.join(lines[i:i+chunk_size]) for i in range(0, len(lines), chunk_size)]
                    else:
                        sub_texts = [code]
                    chunks[-1]['sub_texts'] = sub_texts
    return chunks

def generate_embeddings():
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    if not client:
        logger.error('OpenAI API key not set')
        return
    tasks = EASY_INSTANCES + MEDIUM_INSTANCES
    REPO_EMBEDS_DIR.mkdir(exist_ok=True)
    config_hash = hashlib.sha256((str(tasks) + EMBED_VERSION).encode()).hexdigest()
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
        # Clone to temp dir
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = clone_repo(Path(temp_dir) / task_id, repo, base_commit)
            # Collect function chunks
            chunks = _collect_code_chunks(repo_dir)
            # Batch embed
            batches = [chunks[i:i+50] for i in range(0, len(chunks), 50)]
            for batch in batches:
                for chunk in batch:
                    texts = chunk.get('sub_texts', [chunk['text']])
                    if not texts:
                        continue
                    response = client.embeddings.create(model='text-embedding-3-large', input=texts)
                    sub_vectors = [emb.embedding for emb in response.data]
                    chunk['vector'] = average_vectors(sub_vectors) if len(sub_vectors) > 1 else sub_vectors[0]
                for chunk in batch:
                    chunk.pop('sub_texts', None)
            # Store
            with gzip.open(REPO_EMBEDS_DIR / f'{task_id}.json.gz', 'wt') as f:
                json.dump({'chunks': chunks}, f)  # If using NamedTuple
    # Update manifest
    with open(manifest_path, 'w') as f:
        json.dump({'config_hash': config_hash, 'timestamp': time.time()}, f)
    logger.info('Embeddings generated') 