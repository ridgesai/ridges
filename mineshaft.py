#!.venv/bin/python3
"""
🏭  Mineshaft - One-Click Multi-miner Management for Ridges AI
Helps your miners get to work by managing multiple miners and a validator
"""

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import logging
import pexpect
import re
import threading

import bittensor as bt
from dotenv import load_dotenv, set_key
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

# Import validator evaluation components
try:
    from validator.evaluation.evaluation_loop import run_evaluation_loop
    from validator.db.operations import DatabaseManager
    VALIDATOR_AVAILABLE = True
except ImportError as e:
    console = Console()
    console.print(f"[yellow]⚠️   Validator modules not available: {e}[/yellow]")
    console.print("[yellow]⚠️   Evaluation features will use simplified simulation[/yellow]")
    VALIDATOR_AVAILABLE = False

console = Console()


class Mineshaft:
    def __init__(self, env_file: str = "mineshaft.env"):
        self.env_file = Path(env_file)
        self.processes: Dict[str, subprocess.Popen] = {}
        self.shutdown_requested = False
        self.validator_port = 8091  # Default validator port
        
        # Set up logs directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.logs_dir = Path('logs_mineshaft') / timestamp
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.miner_logs = [self.logs_dir / f'miner_{i+1}.log' for i in range(3)]
        self.validator_log = self.logs_dir / 'validator.log'
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Initialize or load configuration
        self._init_config()

        self.logger = logging.getLogger(__name__)
        self._setup_logging()

    def _init_config(self) -> None:
        """Initialize configuration by loading or prompting for values."""
        # Load existing config if available
        if self.env_file.exists():
            load_dotenv(self.env_file)
        else:
            console.print(f"[yellow]Creating new configuration file: {self.env_file}[/yellow]")
            self._create_default_env()
        
        # Check and prompt for required values
        self._ensure_required_config()

    def _create_default_env(self) -> None:
        """Create a default .env file."""
        default_content = """# 🏭  Mineshaft Configuration
# Auto-generated configuration file - modify as needed

# Basic Settings
SUBTENSOR_ADDRESS=ws://127.0.0.1:9945
NETUID=1
NUM_MINERS=3
DATABASE_PATH=validator.db

# Wallet Settings
MINER_COLDKEY_NAME=miner
VALIDATOR_WALLET_NAME=validator
VALIDATOR_HOTKEY_NAME=default

# API Keys (comma-separated, distributed evenly among miners)
API_KEYS=

# Advanced Settings (usually don't need to change)
BASE_MINER_PORT=7999
EXTERNAL_IP=0.0.0.1
CREATE_NEW_DATABASE=false
LOG_LEVEL=INFO
"""
        with open(self.env_file, 'w') as f:
            f.write(default_content)
        load_dotenv(self.env_file)

    def _ensure_required_config(self) -> None:
        """Ensure all required configuration values are present, prompting if needed."""
        config_prompts = {
            'SUBTENSOR_ADDRESS': {
                'prompt': 'Subtensor WebSocket address',
                'default': 'ws://127.0.0.1:9945',
                'help': 'The WebSocket address of your subtensor node'
            },
            'NETUID': {
                'prompt': 'Subnet ID (netuid)',
                'default': '1',
                'help': 'The subnet ID to register miners on'
            },
            'NUM_MINERS': {
                'prompt': 'Number of miners to run',
                'default': '3',
                'help': 'How many miner instances to start'
            },
            'DATABASE_PATH': {
                'prompt': 'Database file path',
                'default': 'validator.db',
                'help': 'Path to the validator database file'
            },
            'MINER_COLDKEY_NAME': {
                'prompt': 'Miner coldkey wallet name',
                'default': 'miner',
                'help': 'Name of the coldkey wallet for all miners (will create hotkeys mineshaft-1, mineshaft-2, etc.)',
                'required': True
            },
            'API_KEYS': {
                'prompt': 'API keys (comma-separated)',
                'default': '',
                'help': 'API keys for AI services, distributed among miners',
                'required': True
            }
        }
        
        updated = False
        for key, config in config_prompts.items():
            current_value = os.getenv(key, '').strip()
            
            # Skip prompting if the variable already has a non-empty value
            # This avoids redundant prompts when the environment or .env file
            # already defines the setting.
            if current_value:
                continue
                
            # Prompt for value
            console.print(f"\n[bold blue]⚙️ Configuration:[/bold blue] {config['help']}")
            
            if current_value:
                prompt_text = f"{config['prompt']} [dim](current: {current_value})[/dim]"
            else:
                prompt_text = config['prompt']
                
            new_value = Prompt.ask(
                prompt_text,
                default=current_value or config['default']
            ).strip()
            
            if new_value != current_value:
                set_key(self.env_file, key, new_value)
                os.environ[key] = new_value
                updated = True
        
        if updated:
            console.print("[green]✅  Configuration updated![/green]")
            load_dotenv(self.env_file, override=True)

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals and exit immediately."""
        console.print(f"\n[yellow]⚠️   Received signal {signum}, exiting...[/yellow]")
        sys.exit(0)

    def _run_command(self, command: List[str], description: str, check: bool = True, ask_confirmation: bool = False, show_output: bool = False, is_background: bool = False) -> Tuple[bool, str]:
        """Run a command and return success status and output."""
        command_str = " ".join(command)
        
        if ask_confirmation:
            if not Confirm.ask(f"Run command: {command_str}?"):
                return False, "Skipped by user"
        
        try:
            console.print(f"[blue]⚡  Running: {description}[/blue]")
            console.print(f"[dim]Command: {command_str}[/dim]")
            
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            if is_background:
                return True, "Started in background"
            
            # Read output
            output = []
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    line = line.strip()
                    output.append(line)
                    console.print(f"[dim]{line}[/dim]")
            
            # Check for errors
            stderr = process.stderr.read()
            if stderr:
                console.print(f"[red]Error: {stderr}[/red]")
            
            success = process.returncode == 0
            if check and not success:
                console.print(f"[red]❌  Command failed with exit code {process.returncode}[/red]")
            return success, "\n".join(output)
            
        except Exception as e:
            console.print(f"[red]❌  Error running command: {str(e)}[/red]")
            return False, str(e)

    def _generate_miners(self) -> List[Dict[str, str]]:
        """Generate miner configurations automatically."""
        num_miners = int(os.getenv('NUM_MINERS', '3'))
        base_port = int(os.getenv('BASE_MINER_PORT', '7999'))
        coldkey_name = os.getenv('MINER_COLDKEY_NAME', 'miner')
        
        miners = []
        for i in range(num_miners):
            miners.append({
                'coldkey_name': coldkey_name,
                'hotkey_name': f'mineshaft-{i+1}',
                'port': base_port - i
            })
        
        return miners

    def _get_api_keys(self) -> List[str]:
        """Get API keys and distribute them among miners."""
        api_keys_str = os.getenv('API_KEYS', '').strip()
        if not api_keys_str:
            return []
        
        return [key.strip() for key in api_keys_str.split(',') if key.strip()]

    def _distribute_api_keys(self, miners: List[Dict], api_keys: List[str]) -> None:
        """Distribute API keys evenly among miners."""
        if not api_keys:
            console.print("[yellow]⚠️   No API keys provided - miners will use default configuration[/yellow]")
            return
        
        for i, miner in enumerate(miners):
            # Distribute keys round-robin style
            api_key = api_keys[i % len(api_keys)]
            miner['api_key'] = api_key

    def _coldkey_exists(self, coldkey_name: str) -> bool:
        """Check if a coldkey exists using bittensor-cli."""
        try:
            wallet = bt.wallet(name=coldkey_name)
            return True
        except Exception:
            return False

    def _hotkey_exists(self, coldkey_name: str, hotkey_name: str) -> bool:
        """Check if a hotkey exists for the given coldkey using bittensor-cli."""
        try:
            wallet = bt.wallet(name=coldkey_name, hotkey=hotkey_name)
            return True
        except Exception:
            return False

    def _create_coldkey(self, coldkey_name: str) -> bool:
        """Create a new coldkey using bittensor-cli."""
        console.print(f"[blue]💳  Creating coldkey {coldkey_name}[/blue]")
        try:
            wallet = bt.wallet(name=coldkey_name, n_words=12)
            console.print(f"[green]✅  Created coldkey {coldkey_name}[/green]")
            return True
        except Exception as e:
            console.print(f"[red]❌  Failed to create coldkey: {e}[/red]")
            return False

    def _create_hotkey(self, coldkey_name: str, hotkey_name: str) -> bool:
        """Create a new hotkey for the given coldkey using bittensor-cli."""
        console.print(f"[blue]🔑  Creating hotkey {hotkey_name} for {coldkey_name}[/blue]")
        try:
            wallet = bt.wallet(name=coldkey_name, hotkey=hotkey_name, n_words=12)
            console.print(f"[green]✅  Created hotkey {coldkey_name}:{hotkey_name}[/green]")
            return True
        except Exception as e:
            console.print(f"[red]❌  Failed to create hotkey: {e}[/red]")
            return False

    def _get_coldkey_balance(self, coldkey_name: str) -> float:
        """Get the balance of a coldkey using bittensor-cli."""
        try:
            wallet = bt.wallet(name=coldkey_name)
            subtensor = bt.subtensor(network=os.getenv('SUBTENSOR_ADDRESS'))
            balance = subtensor.get_balance(wallet.coldkeypub.ss58_address)
            return float(balance)
        except Exception as e:
            console.print(f"[red]❌  Error getting balance: {e}[/red]")
            return 0.0

    def _is_registered(self, coldkey_name: str, hotkey_name: str) -> bool:
        """Check if a hotkey is registered on the subnet using bittensor-cli."""
        try:
            wallet = bt.wallet(name=coldkey_name, hotkey=hotkey_name)
            subtensor = bt.subtensor(network=os.getenv('SUBTENSOR_ADDRESS'))
            netuid = int(os.getenv('NETUID', '1'))
            return subtensor.is_hotkey_registered(wallet.hotkey.ss58_address, netuid)
        except Exception as e:
            console.print(f"[red]❌  Error checking registration: {e}[/red]")
            return False

    def _register_hotkey(self, coldkey_name: str, hotkey_name: str) -> Tuple[bool, str]:
        """Register a hotkey on the subnet."""
        command = [
            "btcli", "subnet", "register",
            "--wallet.name", coldkey_name,
            "--wallet.hotkey", hotkey_name,
            "--subtensor.chain_endpoint", os.getenv('SUBTENSOR_ADDRESS'),
            "--netuid", os.getenv('NETUID'),
            "--no-prompt"  # Disable interactive prompts
        ]
        success, output = self._run_command(command, f"Registering {coldkey_name}:{hotkey_name}", show_output=True)
        
        # If registration was successful, post IP
        if success:
            port = self._get_next_port()
            ip_command = [
                "fiber-post-ip",
                "--wallet.name", coldkey_name,
                "--wallet.hotkey", hotkey_name,
                "--subtensor.chain_endpoint", os.getenv('SUBTENSOR_ADDRESS'),
                "--netuid", os.getenv('NETUID'),
                "--external_ip", "127.0.0.1",
                "--external_port", str(port)
            ]
            ip_success, ip_output = self._run_command(ip_command, f"Posting IP for {coldkey_name}:{hotkey_name}", show_output=True)
            if not ip_success:
                console.print(f"[red]❌  Failed to post IP after registration: {ip_output}[/red]")
        
        return success, output

    def _setup_wallet(self, coldkey_name: str) -> bool:
        """Set up the wallet, ensuring it exists and is registered."""
        console.print(f"[blue]⚙️  Setting up wallet: {coldkey_name}[/blue]")
        
        # Create coldkey if it doesn't exist
        if not self._coldkey_exists(coldkey_name):
            console.print(f"[blue]📝  Creating coldkey: {coldkey_name}[/blue]")
            if not self._create_coldkey(coldkey_name):
                return False
        else:
            console.print(f"[green]✅  Coldkey already exists[/green]")
        
        # Create hotkey if it doesn't exist
        if not self._hotkey_exists(coldkey_name, os.getenv('VALIDATOR_HOTKEY_NAME')):
            console.print(f"[blue]📝  Creating hotkey: {coldkey_name}:{os.getenv('VALIDATOR_HOTKEY_NAME')}[/blue]")
            if not self._create_hotkey(coldkey_name, os.getenv('VALIDATOR_HOTKEY_NAME')):
                return False
        else:
            console.print(f"[green]✅  Hotkey already exists, reusing[/green]")
        
        # Check if registered
        if not self._is_registered(coldkey_name, os.getenv('VALIDATOR_HOTKEY_NAME')):
            console.print(f"[blue]📝  Hotkey not registered, registering...[/blue]")
            success, _ = self._register_hotkey(coldkey_name, os.getenv('VALIDATOR_HOTKEY_NAME'))
            if not success:
                return False
        else:
            console.print(f"[green]✅  Hotkey already registered[/green]")
        
        return True

    def _setup_miner_coldkey(self, coldkey_name: str) -> bool:
        """Set up the miner coldkey, checking balance and fauceting if needed."""
        console.print(f"\n[bold blue]⚙️  Setting up miner coldkey: {coldkey_name}[/bold blue]")
        
        # Check if coldkey exists, create if needed
        if not self._coldkey_exists(coldkey_name):
            console.print(f"[yellow]💳  Coldkey doesn't exist, creating...[/yellow]")
            if not self._create_coldkey(coldkey_name):
                return False
        else:
            console.print(f"[green]✅  Coldkey already exists[/green]")
        
        # Check balance and faucet if needed
        balance = self._get_coldkey_balance(coldkey_name)
        console.print(f"[blue]💰 Coldkey balance: {balance:.2f} TAO[/blue]")
        
        # Estimate needed balance (rough estimate: 2 TAO per registration)
        num_miners = int(os.getenv('NUM_MINERS', '3'))
        estimated_needed = num_miners * 2.0
        
        if balance < estimated_needed:
            console.print(f"[yellow]💧  Balance may be insufficient for {num_miners} registrations, attempting faucet...[/yellow]")
            self._faucet_coldkey(coldkey_name)
        
        return True

    async def _start_miners(self, miner_configs: List[dict]) -> None:
        """Start all miners in parallel."""
        for config in miner_configs:
            await self._start_miner(config['hotkey_name'], config['port'])

    async def _start_miner(self, hotkey_name: str, port: int) -> None:
        """Start a miner process with the given hotkey and port."""
        process_name = f"miner-{hotkey_name}"
        console.print(f"[bold blue]Starting {process_name} on port {port}...[/bold blue]")
        
        # Get the appropriate log file for this miner
        miner_index = int(hotkey_name.split('-')[1]) - 1
        log_file = self.miner_logs[miner_index]
        
        # Start the miner process using uvicorn with output capture
        process = subprocess.Popen(
            ["uvicorn", "miner.main:app", "--host", "0.0.0.0", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            env={**os.environ, 'MINER_HOTKEY': hotkey_name}
        )
        
        self.processes[f"miner_{hotkey_name}_{port}"] = process

        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])')
        
        def stream_and_strip():
            with open(log_file, 'w') as f:
                for line in process.stdout:
                    clean_line = ansi_escape.sub('', line)
                    f.write(clean_line)
                    f.flush()
                    print(clean_line, end='')
        
        # Run the blocking I/O in a background thread
        thread = threading.Thread(target=stream_and_strip, daemon=True)
        thread.start()
        self.processes[f"miner_{hotkey_name}_{port}_thread"] = thread

    def _handle_database(self) -> None:
        """Handle database creation/cleanup."""
        db_path = Path(os.getenv('DATABASE_PATH', 'validator.db'))
        create_new = os.getenv('CREATE_NEW_DATABASE', 'false').lower() == 'true'
        
        if create_new and db_path.exists():
            if Confirm.ask(f"Delete existing database {db_path}?"):
                db_path.unlink()
                console.print(f"[yellow]🗑️   Deleted existing database {db_path}[/yellow]")

    def _setup_logging(self) -> None:
        """Set up logging configuration."""
        log_dir = Path('logs_mineshaft')
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Set up root logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter('%(asctime)s - %(message)s')
        
        # Create handlers for each miner
        self.miner_handlers = []
        for i in range(3):  # For 3 miners
            log_file = self.miner_logs[i]
            handler = logging.FileHandler(log_file, mode='w')  # 'w' mode to start fresh
            handler.setFormatter(formatter)
            handler.setLevel(logging.INFO)
            self.miner_handlers.append(handler)

    def _run_validator(self, wallet: str, hotkey: str, port: int):
        """Run the validator with the given wallet and hotkey."""
        os.environ['VALIDATOR_WALLET'] = wallet
        os.environ['VALIDATOR_HOTKEY'] = hotkey
        os.environ['VALIDATOR_PORT'] = str(port)
        console.print("[bold green]Starting validator...[/bold green]")
        command = f"uv run --no-sync validator/main.py"
        child = pexpect.spawn(command, encoding='utf-8')
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        while True:
            try:
                index = child.expect(['\n', pexpect.EOF, pexpect.TIMEOUT], timeout=1)
                if index == 0:
                    line = ansi_escape.sub('', child.before.strip())
                    console.print(line)
                elif index == 1:
                    break
            except pexpect.EOF:
                break
            except pexpect.TIMEOUT:
                console.print("[yellow]Validator is still running...[/yellow]")

    async def _stream_validator_output(self, process: subprocess.Popen, log_file: Path, error_log_file: Path) -> None:
        """Stream validator output to log file."""
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        with open(self.validator_log, 'w') as log:
            while True:
                stdout_line = process.stdout.readline()
                stderr_line = process.stderr.readline()
                if stdout_line:
                    line = ansi_escape.sub('', stdout_line.strip())
                    log.write(line + '\n')
                if stderr_line:
                    line = ansi_escape.sub('', stderr_line.strip())
                    log.write(line + '\n')
                if not stdout_line and not stderr_line and process.poll() is not None:
                    break

    async def _monitor_processes(self) -> None:
        """Monitor all processes and report status."""
        console.print("[blue]👀  Starting process monitoring...[/blue]")
        
        while not self.shutdown_requested:
            for name, process in list(self.processes.items()):
                if process.poll() is not None:
                    console.print(f"[red]💀  Process {name} (PID {process.pid}) has stopped[/red]")
                    del self.processes[name]
            
            await asyncio.sleep(10)  # Check every 10 seconds

    def _cleanup_processes(self) -> None:
        """Clean up all running processes."""
        if not self.processes:
            return
            
        console.print("\n[yellow]🧹  Cleaning up all processes...[/yellow]")
        
        for name, obj in list(self.processes.items()):
            try:
                if name.endswith('_thread'):
                    # Join the background thread
                    console.print(f"[yellow]🧵  Waiting for thread {name} to finish[/yellow]")
                    obj.join(timeout=2)
                else:
                    console.print(f"[yellow]🛑  Terminating {name} (PID {obj.pid})[/yellow]")
                    obj.terminate()
                    try:
                        obj.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        console.print(f"[red]💥  Force killing {name} (PID {obj.pid})[/red]")
                        obj.kill()
                        obj.wait()
            except Exception as e:
                console.print(f"[red]❌  Error cleaning up {name}: {str(e)}[/red]")
        
        self.processes.clear()
        console.print("[green]✅  Process cleanup complete[/green]")

    def _copy_database(self) -> None:
        """Copy the database to the logs directory."""
        try:
            db_path = Path(os.getenv('DATABASE_PATH', 'validator.db'))
            if db_path.exists():
                dest_path = self.logs_dir / db_path.name
                shutil.copy2(db_path, dest_path)
                console.print(f"[green]📁  Database copied to {dest_path}[/green]")
            else:
                console.print(f"[yellow]⚠️   Database not found at {db_path}[/yellow]")
        except Exception as e:
            console.print(f"[red]❌  Error copying database: {e}[/red]")

    def _check_setup(self, miners: List[Dict]) -> bool:
        """Verify that all required coldkeys and hotkeys exist before starting miners."""
        console.print("\n[bold blue]🔍  Checking setup...[/bold blue]")

        # Check validator coldkey and hotkey
        validator_coldkey = os.getenv('VALIDATOR_WALLET_NAME')
        validator_hotkey = os.getenv('VALIDATOR_HOTKEY_NAME')
        if not validator_coldkey or not validator_hotkey:
            console.print("[red]❌  Validator coldkey or hotkey not set in environment.[/red]")
            return False

        if not self._coldkey_exists(validator_coldkey):
            console.print(f"[red]❌  Validator coldkey '{validator_coldkey}' not found.[/red]")
            if Confirm.ask(f"Create validator coldkey '{validator_coldkey}'?"):
                if not self._create_coldkey(validator_coldkey):
                    return False
            else:
                return False

        if not self._hotkey_exists(validator_coldkey, validator_hotkey):
            console.print(f"[red]❌  Validator hotkey '{validator_hotkey}' not found for coldkey '{validator_coldkey}'.[/red]")
            if Confirm.ask(f"Create validator hotkey '{validator_hotkey}' for coldkey '{validator_coldkey}'?"):
                if not self._create_hotkey(validator_coldkey, validator_hotkey):
                    return False
            else:
                return False

        # Check miner coldkey and hotkeys
        miner_coldkey = os.getenv('MINER_COLDKEY_NAME')
        if not miner_coldkey:
            console.print("[red]❌  Miner coldkey not set in environment.[/red]")
            return False

        if not self._coldkey_exists(miner_coldkey):
            console.print(f"[red]❌  Miner coldkey '{miner_coldkey}' not found.[/red]")
            if Confirm.ask(f"Create miner coldkey '{miner_coldkey}'?"):
                if not self._create_coldkey(miner_coldkey):
                    return False
            else:
                return False

        for miner in miners:
            hotkey_name = miner['hotkey_name']
            if not self._hotkey_exists(miner_coldkey, hotkey_name):
                console.print(f"[red]❌  Miner hotkey '{hotkey_name}' not found for coldkey '{miner_coldkey}'.[/red]")
                if Confirm.ask(f"Create miner hotkey '{hotkey_name}' for coldkey '{miner_coldkey}'?"):
                    if not self._create_hotkey(miner_coldkey, hotkey_name):
                        return False
                else:
                    return False

        console.print("[green]✅  Setup check passed.[/green]")
        return True

    def _display_summary(self, miners: List[Dict], successful_miners: int) -> None:
        """Display a nice summary table."""
        table = Table(title="🏭  Mineshaft Summary")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Details", style="white")
        
        # Validator info
        table.add_row(
            "🛡️  Validator",
            "Running" if "validator" in self.processes else "Not Started",
            f"Wallet: {os.getenv('VALIDATOR_WALLET_NAME')}:{os.getenv('VALIDATOR_HOTKEY_NAME')}"
        )
        
        # Miners info
        for i, miner in enumerate(miners[:successful_miners]):
            process_name = f"miner_{miner['hotkey_name']}_{miner['port']}"
            status = "Running" if process_name in self.processes else "Stopped"
            api_key_info = " (with API key)" if 'api_key' in miner else " (no API key)"
            table.add_row(
                f"⛏️  Miner {i+1}",
                status,
                f"Port {miner['port']} - {miner['coldkey_name']}:{miner['hotkey_name']}{api_key_info}"
            )
        
        console.print("\n")
        console.print(table)
        console.print(f"\n[green]🎉  Mineshaft is operational with {successful_miners} miners![/green]")
        console.print(f"[blue]💳  All miners use coldkey: {os.getenv('MINER_COLDKEY_NAME')}[/blue]")
        console.print("[dim]Press Ctrl+C to stop all processes[/dim]")

    async def run(self) -> None:
        """Main run method - get those miners to work!"""
        try:
            # Show welcome banner
            console.print(Panel.fit(
                "[bold blue]🏭  Mineshaft - One-Click Multi-miner Management[/bold blue]\n"
                f"[dim]📁  Logs directory: {self.logs_dir}[/dim]",
                border_style="blue"
            ))
            
            # Generate miner configurations
            miners = self._generate_miners()
            api_keys = self._get_api_keys()
            self._distribute_api_keys(miners, api_keys)
            
            console.print(f"\n[bold]📋  Ready to start {len(miners)} miners + validator[/bold]")
            console.print(f"   • Coldkey: {os.getenv('MINER_COLDKEY_NAME')} • API keys: {len(api_keys)} • Subnet: {os.getenv('NETUID')}")
            
            # Check that all required coldkeys and hotkeys exist before proceeding
            if not self._check_setup(miners):
                console.print("[red]❌  Setup check failed. Please fix the issues above and try again.[/red]")
                return
            
            # Setup validator wallet
            validator_wallet = os.getenv('VALIDATOR_WALLET_NAME')
            validator_hotkey = os.getenv('VALIDATOR_HOTKEY_NAME')
            
            console.print(f"\n[bold yellow]🛡️   Setting up validator[/bold yellow]")
            if not self._setup_validator_wallet(validator_wallet, validator_hotkey):
                console.print("[red]❌  Validator setup failed[/red]")
                return
            
            # Setup miner coldkey first
            miner_coldkey = os.getenv('MINER_COLDKEY_NAME')
            if not self._setup_miner_coldkey(miner_coldkey):
                console.print("[red]❌  Failed to setup miner coldkey[/red]")
                return
            
            # Setup and start miners
            console.print(f"\n[bold yellow]⛏️   Setting up {len(miners)} miners[/bold yellow]")
            
            # Start all miners with delay between each
            await self._start_miners(miners)
            
            # Start validator
            console.print(f"\n[bold yellow]🛡️   Starting validator[/bold yellow]")
            if not self._run_validator(validator_wallet, validator_hotkey, self.validator_port):
                console.print("[red]❌  Validator failed to start[/red]")
                return
            
            # Display summary
            self._display_summary(miners, len(miners))
            
            # Monitor processes
            await self._monitor_processes()
            
        except Exception as e:
            console.print(f"[red]💥  Error in main run: {str(e)}[/red]")
        finally:
            self._cleanup_processes()

    def _get_database_manager(self, db_path: str) -> DatabaseManager:
        """Get a DatabaseManager instance for the validator database."""
        if not Path(db_path).exists():
            console.print(f"[red]❌  Database not found: {db_path}[/red]")
            sys.exit(1)
        
        if not VALIDATOR_AVAILABLE:
            console.print(f"[red]❌  Validator modules not available[/red]")
            sys.exit(1)
        
        try:
            return DatabaseManager(db_path)
        except Exception as e:
            console.print(f"[red]❌  Failed to connect to database: {e}[/red]")
            sys.exit(1)

    async def eval_database(self, db_path: str) -> None:
        """Run the actual validator evaluation loop on the database."""
        if not VALIDATOR_AVAILABLE:
            console.print("[red]❌  Validator modules not available[/red]")
            return

        from pathlib import Path
        from openai import OpenAI

        console.print(Panel.fit(
            f"[bold blue]🔍  Running validator evaluation loop[/bold blue]\n"
            f"[dim]Database: {db_path}[/dim]",
            border_style="blue"
        ))

        # You may want to load the OpenAI key from env or config
        openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        validator_hotkey = os.getenv("VALIDATOR_HOTKEY_NAME", "default")

        # Run the evaluation loop ONCE (set sleep_interval=0 to avoid waiting)
        await run_evaluation_loop(
            db_path=Path(db_path),
            openai_client=openai_client,
            validator_hotkey=validator_hotkey,
            sleep_interval=0  # Run once, then exit
        )
        console.print("[green]✅  Evaluation loop completed[/green]")

    def _setup_validator_wallet(self, wallet_name: str, hotkey_name: str) -> bool:
        """Set up the validator wallet, ensuring it exists and is registered."""
        console.print(f"[blue]⚙️  Setting up validator wallet: {wallet_name}:{hotkey_name}[/blue]")
        
        # Create coldkey if it doesn't exist
        if not self._coldkey_exists(wallet_name):
            console.print(f"[blue]📝  Creating coldkey: {wallet_name}[/blue]")
            if not self._create_coldkey(wallet_name):
                return False
        else:
            console.print(f"[green]✅  Coldkey already exists[/green]")
        
        # Create hotkey if it doesn't exist
        if not self._hotkey_exists(wallet_name, hotkey_name):
            console.print(f"[blue]📝  Creating hotkey: {wallet_name}:{hotkey_name}[/blue]")
            if not self._create_hotkey(wallet_name, hotkey_name):
                return False
        else:
            console.print(f"[green]✅  Hotkey already exists, reusing[/green]")
        
        # Check if registered
        if not self._is_registered(wallet_name, hotkey_name):
            console.print(f"[blue]📝  Hotkey not registered, registering...[/blue]")
            success, _ = self._register_hotkey(wallet_name, hotkey_name)
            if not success:
                return False
        else:
            console.print(f"[green]✅  Hotkey already registered[/green]")
        
        return True


def main():
    """Main entry point with command-line interface."""
    parser = argparse.ArgumentParser(
        description="🏭  Mineshaft - One-Click Multi-miner Management for Ridges AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  run                    Start miners and validator (default if no command given)
  eval <database>        Run evaluation loop on database and show results
  weight <database>      Show weight calculations for miners based on database

Examples:
  python mineshaft.py                    # Start mining operation
  python mineshaft.py run                # Same as above
  python mineshaft.py eval validator.db  # Evaluate responses in database
  python mineshaft.py weight validator.db # Show weight calculations
        """
    )
    
    parser.add_argument(
        'command',
        nargs='?',
        default='run',
        choices=['run', 'eval', 'weight'],
        help='Command to execute (default: run)'
    )
    
    parser.add_argument(
        'database',
        nargs='?',
        help='Database file path (required for eval and weight commands)'
    )
    
    args = parser.parse_args()
    
    async def run_async():
        try:
            mineshaft = Mineshaft("mineshaft.env")
            
            if args.command == 'run':
                await mineshaft.run()
            elif args.command == 'eval':
                if not args.database:
                    console.print("[red]❌  Database path required for eval command[/red]")
                    console.print("Usage: python mineshaft.py eval <database>")
                    sys.exit(1)
                await mineshaft.eval_database(args.database)
            elif args.command == 'weight':
                if not args.database:
                    console.print("[red]❌  Database path required for weight command[/red]")
                    console.print("Usage: python mineshaft.py weight <database>")
                    sys.exit(1)
                await mineshaft.show_weights(args.database)
            
        except KeyboardInterrupt:
            console.print("\n[yellow]🛑  Shutdown requested - stopping all processes...[/yellow]")
        except Exception as e:
            console.print(f"[red]💥  Error: {str(e)}[/red]")
            import sys
            sys.exit(1)
    
    asyncio.run(run_async())

if __name__ == "__main__":
    main() 