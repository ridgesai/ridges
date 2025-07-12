import os
import json
import gzip
import time
import hashlib
from pathlib import Path
from openai import OpenAI
import asyncio

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
        # Collect .py chunks
        chunks = []
        for root, _, files in os.walk(repo_dir):
            for file in files:
                if file.endswith('.py'):
                    file_path = Path(root) / file
                    with open(file_path) as f:
                        text = f.read()
                    if not text.strip():
                        continue
                    if len(text) > 400:
                        lines = text.splitlines()
                        chunk_size = 20
                        sub_texts = ['\n'.join(lines[i:i+chunk_size]) for i in range(0, len(lines), chunk_size)]
                    else:
                        sub_texts = [text]
                    chunks.append({
                        'file': str(file_path.relative_to(repo_dir)),
                        'text': text  # Store original full text
                    })
        # Batch embed
        batches = [chunks[i:i+50] for i in range(0, len(chunks), 50)]
        for batch in batches:
            # For each chunk, embed its sub_texts if split
            sub_responses = []
            for chunk in batch:
                texts = chunk.get('sub_texts', [chunk['text']])  # Use sub_texts if present
                if not texts:
                    continue
                response = client.embeddings.create(model='text-embedding-3-large', input=texts)
                sub_vectors = [emb.embedding for emb in response.data]
                chunk['vector'] = average_vectors(sub_vectors) if len(sub_vectors) > 1 else sub_vectors[0]
        # Remove sub_texts if not needed
        for chunk in chunks:
            chunk.pop('sub_texts', None)
        # Store
        with gzip.open(REPO_EMBEDS_DIR / f'{task_id}.json.gz', 'wt') as f:
            json.dump({'chunks': chunks}, f)
    # Update manifest
    with open(manifest_path, 'w') as f:
        json.dump({'config_hash': config_hash, 'timestamp': time.time()}, f)
    logger.info('Embeddings generated') 