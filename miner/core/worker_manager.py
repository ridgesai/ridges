import asyncio
import threading
import tempfile
from pathlib import Path
import time
import os
import yaml
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional

from shared.logging_utils import get_logger
from miner.core.queue import ChallengeQueue, Challenge

logger = get_logger(__name__)

class WorkerManager:
    def __init__(self, num_workers: int = 3, max_queue_size: int = 10):
        """Initialize the worker manager with a specified number of workers"""
        self.num_workers = num_workers
        self.challenge_queue = ChallengeQueue(max_size=max_queue_size)
        self.worker_task = None
        self.running = False
        self.worker_processes = {}  # Maps challenge_id to process info
        
    async def start(self):
        """Start the worker manager"""
        if self.running:
            return
            
        self.running = True
        # Start a single task to manage all workers
        self.worker_task = asyncio.create_task(self._worker_loop())
        logger.info(f"Started worker manager with {self.num_workers} workers")
        
    async def stop(self):
        """Stop the worker manager"""
        if not self.running:
            return
            
        self.running = False
        
        if self.worker_task:
            self.worker_task.cancel()
            
        # Terminate any running processes
        for challenge_id, process_info in list(self.worker_processes.items()):
            process = process_info.get("process")
            if process and process.poll() is None:
                logger.info(f"Terminating process for challenge {challenge_id}")
                process.terminate()
                
        self.worker_processes = {}
        logger.info("Worker manager stopped")
        
    async def _worker_loop(self):
        """Main loop for worker management"""
        logger.info("Worker loop started")
        
        # Use a thread pool to manage worker processes
        with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            while self.running:
                try:
                    # Check if we can start a new worker
                    active_workers = len([p for p in self.worker_processes.values() 
                                         if p.get("process") and p.get("process").poll() is None])
                    
                    if active_workers >= self.num_workers:
                        # Maximum workers running, wait a bit
                        await asyncio.sleep(1)
                        continue
                        
                    # Try to get a challenge
                    challenge = await self.challenge_queue.get_next_challenge()
                    
                    if not challenge:
                        await asyncio.sleep(0.1)
                        continue
                        
                    logger.info(f"Starting worker for challenge {challenge.challenge_id}")
                    
                    # Start a worker process for this challenge
                    executor.submit(
                        self._process_challenge,
                        challenge
                    )
                    
                except asyncio.CancelledError:
                    logger.info("Worker loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in worker loop: {str(e)}", exc_info=True)
                    await asyncio.sleep(1)
                    
        logger.info("Worker loop stopped")
        
    def _process_challenge(self, challenge: Challenge):
        """Process a challenge using SWE-agent's batch mode"""
        try:
            challenge_id = challenge.challenge_id
            data = challenge.data
            
            logger.info(f"Processing challenge {challenge_id}")
            
            # Create a temporary directory for batch config
            work_dir = tempfile.mkdtemp(prefix=f"challenge_{challenge_id}_")
            instance_file = Path(work_dir) / "instance.yaml"
            
            # Extract challenge data
            problem_statement = data.get("problem_statement", "")
            repository_url = data.get("repository_url", "")
            commit_hash = data.get("commit_hash", "")
            
            # Create instance config for SWE-agent batch
            instance = {
                "problem_statement": {
                    "type": "text",
                    "text": problem_statement,
                    "id": challenge_id
                },
                "env": {
                    "deployment": {
                        "type": "docker",
                        "image": "python:3.11",
                        "python_standalone_dir": "/root"
                    },
                    "repo": {
                        "type": "github",
                        "github_url": repository_url,
                        "commit": commit_hash
                    }
                }
            }
            
            # Write instance config to file
            with open(instance_file, "w") as f:
                yaml.dump([instance], f)
                
            # Create output directory
            output_dir = Path(work_dir) / "output"
            output_dir.mkdir(exist_ok=True)
            
            # Get SWE-agent config path
            config_path = os.environ.get("SWEAGENT_CONFIG_PATH", "config/default.yaml")
            
            # Prepare SWE-agent batch command
            cmd = [
                "sweagent", "run-batch",
                "--config", config_path,
                "--agent.model.name=claude-3-7-sonnet-latest",
                "--agent.model.per_instance_cost_limit=3.00",
                "--instances.type=file",
                f"--instances.path={instance_file}",
                "--num_workers=1",  # We're managing concurrency ourselves
                f"--output_dir={output_dir}"
            ]
            
            logger.info(f"Running SWE-agent batch for challenge {challenge_id} with command: {' '.join(cmd)}")
            
            # Start the process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Store process info
            self.worker_processes[challenge_id] = {
                "process": process,
                "start_time": time.time(),
                "output_dir": output_dir,
                "challenge": challenge
            }
            
            # Function to stream output
            def stream_output(pipe, prefix):
                for line in pipe:
                    logger.info(f"{prefix} [{challenge_id}]: {line.strip()}")
                    
            # Start threads to monitor output
            threading.Thread(target=stream_output, args=(process.stdout, "SWE-agent stdout"), daemon=True).start()
            threading.Thread(target=stream_output, args=(process.stderr, "SWE-agent stderr"), daemon=True).start()
            
            # Wait for process to complete
            process.wait()
            
            # Process completed
            logger.info(f"Challenge {challenge_id} processing completed with return code {process.returncode}")
            
            # Get the patch file
            patch_file = output_dir / challenge_id / f"{challenge_id}.patch"
            diff = ""
            
            if patch_file.exists():
                with open(patch_file, "r") as f:
                    diff = f.read()
                    
                logger.info(f"Found patch file for challenge {challenge_id}, length: {len(diff)}")
            else:
                logger.warning(f"No patch file found for challenge {challenge_id}")
                
            # Complete the challenge
            asyncio.run(self.challenge_queue.complete_challenge(challenge_id))
            
            # Clean up
            if challenge_id in self.worker_processes:
                del self.worker_processes[challenge_id]
                
            # Return the diff for potential future use
            return diff
                
        except Exception as e:
            logger.error(f"Error processing challenge {challenge.challenge_id}: {str(e)}", exc_info=True)
            try:
                asyncio.run(self.challenge_queue.complete_challenge(challenge.challenge_id))
                if challenge.challenge_id in self.worker_processes:
                    del self.worker_processes[challenge.challenge_id]
            except Exception as cleanup_error:
                logger.error(f"Error cleaning up challenge: {str(cleanup_error)}")
            return None 