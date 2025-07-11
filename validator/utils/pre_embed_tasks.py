import os
import json
import gzip
import time
import hashlib
from pathlib import Path
from openai import OpenAI
import asyncio

from validator.config import EASY_INSTANCES, MEDIUM_INSTANCES
from validator.utils.get_swebench_problems import get_swebench_problems
from validator.sandbox.clone_repo import clone_repo
from validator.utils.logging import get_logger

logger = get_logger(__name__)

REPO_EMBEDS_DIR = Path(__file__).parent.parent / 'repo_embeds'

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
    for task_id in tasks:
        # Get problem details
        problems = get_swebench_problems(None)  # Pass None or appropriate arg
        problem = next((p for p in problems if p.instance_id == task_id), None)
        if not problem:
            continue
        # Clone repo
        repo_dir = clone_repo(REPO_EMBEDS_DIR / task_id, problem.repo, problem.base_commit)
        # Collect .py chunks
        chunks = []
        for root, _, files in os.walk(repo_dir):
            for file in files:
                if file.endswith('.py'):
                    file_path = Path(root) / file
                    with open(file_path) as f:
                        text = f.read()
                    chunks.append({
                        'file': str(file_path.relative_to(repo_dir)),
                        'text': text
                    })
        # Batch embed
        batches = [chunks[i:i+50] for i in range(0, len(chunks), 50)]
        for batch in batches:
            texts = [c['text'] for c in batch]
            response = client.embeddings.create(model='text-embedding-3-small', input=texts)
            for i, emb in enumerate(response.data):
                batch[i]['vector'] = emb.embedding
        # Store
        with gzip.open(REPO_EMBEDS_DIR / f'{task_id}.json.gz', 'wt') as f:
            json.dump({'chunks': chunks}, f)
    # Update manifest
    with open(manifest_path, 'w') as f:
        json.dump({'config_hash': config_hash, 'timestamp': time.time()}, f)
    logger.info('Embeddings generated') 