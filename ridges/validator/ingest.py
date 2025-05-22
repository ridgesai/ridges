"""
Takes in a sample repo, passed in as a path on local machine.

It first tries to check cache to see if file pairs are saved, 
otherwise it walks the dir and refetches based on heuristics 
youve passed in (e.g. how to select files) and saves to cache + returns

If you've changed heuristics, run get_all_filepairs(local_path, refresh=True) 
to regen the cache
"""

import json
import os
import pickle
from dataclasses import asdict
from pathlib import Path
from typing import *
from typing import List

import numpy as np
import openai
import tiktoken

from agentao.helpers.classes import EmbeddedFile, FilePair, IngestionHeuristics
from logging import Logger

from agentao.helpers.clients import LogContext

OPENAI_CLIENT: Final[openai.Client] = openai.Client(api_key=os.getenv("OPENAI_API_KEY"))

SAMPLE_INGESTION_HEURISTICS = IngestionHeuristics(
    min_files_to_consider_dir_for_problems=5,
    min_file_content_len=50
)


def walk_repository(repo_path: Path) -> Dict:
    """
    Picks files to generate problem statements from
    """
    repo_map = {}

    for root, dirs, files in os.walk(str(repo_path)):
        # Convert absolute path to relative path from repo root
        rel_path = os.path.relpath(root, str(repo_path))
        if rel_path == '.':
            rel_path = ''

        # Filter out common files/directories to ignore
        dirs[:] = [d for d in dirs if not d.startswith(('.', '__'))]
        files = [f for f in files if not f.startswith(('.', '__')) and not f.endswith(('.pyc', '.pyo')) and f.endswith('.py')]

        # Add to map
        repo_map[rel_path] = {
            'dirs': dirs,
            'files': files
        }

    return repo_map


def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

def evaluate_for_context(dir_path, repo_structure, heuristics: IngestionHeuristics, logger: Logger):

    def _retrieve_files_in_dir():
        # Get all files in the current directory
        files = []
        for file_name in repo_structure['files']:
            path = os.path.join(dir_path, file_name)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    contents = f.read()
                files.append(
                    {
                        'path': path,
                        'contents': contents
                    }
                )
            except (UnicodeDecodeError, IOError):
                logger.exception(f"Warning: Could not read file {path}")
                continue

        return files

    def _embed_code(raw_codes: List[str]) -> List[List[float]]:
        encoding = tiktoken.get_encoding('cl100k_base')
        truncated_inputs = [encoding.encode(json.dumps(code))[:8191] for code in raw_codes]
        response = OPENAI_CLIENT.embeddings.create(
            model="text-embedding-3-small",
            input=truncated_inputs
        )
        # Return list of embedding vectors
        return [data.embedding for data in response.data]

    def _find_most_similar_files(embedded_files: List[EmbeddedFile]) -> Optional[FilePair]:
        max_similarity = -1
        most_similar_pair = None

        # Compare each pair of files
        for i in range(len(embedded_files)):
            for j in range(i + 1, len(embedded_files)):
                similarity = cosine_similarity(
                    embedded_files[i].embedding,
                    embedded_files[j].embedding
                )

                if similarity > max_similarity:
                    max_similarity = similarity
                    most_similar_pair = [embedded_files[i], embedded_files[j]]

        if most_similar_pair is None:
            return None

        return FilePair(
            cosine_similarity=max_similarity,
            files=most_similar_pair
        )

    if len(repo_structure['files']) >= heuristics.min_files_to_consider_dir_for_problems:
        files = _retrieve_files_in_dir()
        embeddings = _embed_code(list(map(lambda file: file['contents'], files)))
        embedded_files = [
            EmbeddedFile(
                path=file['path'],
                contents=file['contents'],
                embedding=embeddings[i]
            )
            for i, file in enumerate(files)
            if len(file['contents']) > heuristics.min_file_content_len
        ]

        most_similar_files = _find_most_similar_files(embedded_files)

        return most_similar_files
    else:
        return []

def save_filepairs_to_cache(filepairs: List[FilePair], cache_path: str) -> None:
    """Save list of FilePairs to local cache."""
    cache_dir = Path(cache_path).parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Convert FilePairs to serializable format
    serializable_pairs = [
        {
            'cosine_similarity': pair.cosine_similarity,
            'files': [asdict(f) for f in pair.files]
        }
        for pair in filepairs
    ]
    
    with open(cache_path, 'wb') as f:
        pickle.dump(serializable_pairs, f)

def load_filepairs_from_cache(cache_path: str) -> List[FilePair]:
    """Load FilePairs from cache, returns empty list if cache doesn't exist."""
    try:
        with open(cache_path, 'rb') as f:
            data = pickle.load(f)
        
        # Reconstruct FilePairs from serialized data
        return [
            FilePair(
                cosine_similarity=pair['cosine_similarity'],
                files=[EmbeddedFile(**f) for f in pair['files']]
            )
            for pair in data
        ]
    except (FileNotFoundError, EOFError):
        return []
    
def get_all_filepairs(
    local_repo: Path, 
    logger: Logger,
    heuristics: IngestionHeuristics = SAMPLE_INGESTION_HEURISTICS,
    refresh: bool = False,
) -> List[FilePair]:
    cache_path = f".cache/{local_repo}"

    filepairs_from_cache = load_filepairs_from_cache(cache_path=cache_path)

    if filepairs_from_cache and len(filepairs_from_cache) > 0 and refresh == False:
        logger.info(f"Generated filepairs from cache", extra=asdict(LogContext(
            log_type="lifecycle",
            event_type="openai_cost",
        )))
        return filepairs_from_cache
    
    logger.info(f"filepairs not found in cache", extra=asdict(LogContext(
            log_type="lifecycle",
            event_type="openai_cost",
        )))
    
    repo_structure = walk_repository(local_repo)

    file_pairs = []
    for dir_path, contents in repo_structure.items():
        full_path = os.path.join(local_repo, dir_path) if dir_path else local_repo
        if contents['files']:
            file_pairs.append(evaluate_for_context(full_path, contents, heuristics=heuristics, logger=logger))

    valid_pairs = [pair for pair in file_pairs if pair and isinstance(pair, FilePair)]
    if not valid_pairs:
        raise ValueError("No valid file pairs found in the repository. Ensure there are directories with 5+ Python files.")
    
    # Save the filepairs to cache
    save_filepairs_to_cache(
        filepairs=valid_pairs,
        cache_path=cache_path
    )

    return valid_pairs

if __name__ == "__main__":
    current_dir = Path(__file__).parent
    sample_repo = current_dir.parent / "sample-repo"

    logger = Logger("ingest")
    file_pairs = get_all_filepairs(
        sample_repo,
        logger=logger,
        refresh=True,
    )
    
    for pair in file_pairs:
        logger.info(f"{pair} {type(pair)}")
