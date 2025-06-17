#!.venv/bin/python
"""
🏭  Mineshaft – Help your miners get to work
"""

import asyncio
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv, set_key
from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live
from rich.table import Table

import bittensor as bt

console = Console()

MINESHAFT_BANNER = """ [bold cyan]
███╗   ███╗██╗███╗   ██╗███████╗███████╗██╗  ██╗ █████╗ ███████╗████████╗
████╗ ████║██║████╗  ██║██╔════╝██╔════╝██║  ██║██╔══██╗██╔════╝╚══██╔══╝
██╔████╔██║██║██╔██╗ ██║███████╗███████╗███████║███████║█████╗     ██║   
██║╚██╔╝██║██║██║╚██╗██║██╔════╝╚════██║██╔══██║██╔══██║██╔══╝     ██║   
██║ ╚═╝ ██║██║██║ ╚████║███████║███████║██║  ██║██║  ██║██║        ██║   
╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝        ╚═╝   
[/bold cyan] [dim]⚡️  Help your miners get to work  ⚡️[/dim]
"""


class MineshaftConfig:
    """Configuration manager for Mineshaft."""

    def __init__(self, env_file: str = "mineshaft.env"):
        self.env_file = Path(env_file)
        self.config = {}
        self._init_config()
        self._validate_wallets()

    def _init_config(self) -> None:
        """Initialize configuration by loading or prompting for values."""
        console.print(Panel(MINESHAFT_BANNER, border_style="cyan"))

        defaults = {
            "SUBTENSOR_ADDRESS": f"ws://127.0.0.1:9945",
            "NETUID": "1",
            "NUM_MINERS": "3",
            "DATABASE_PATH": "validator.db",
            "MINER_COLDKEY_NAME": "miner",
            "VALIDATOR_WALLET_NAME": "validator",
            "VALIDATOR_HOTKEY_NAME": "default",
            "API_KEYS": "",
            "BASE_MINER_PORT": "7999",
            "EXTERNAL_IP": self._get_external_ip(),
            "LOG_LEVEL": "INFO",
        }

        prompts = {
            "SUBTENSOR_ADDRESS": "🌐 Subtensor WebSocket address",
            "NETUID": "🔢 Subnet ID (netuid)",
            "NUM_MINERS": "⛏️  Number of miners to run",
            "DATABASE_PATH": "💾 Path to validator database",
            "MINER_COLDKEY_NAME": "🔑 Miner coldkey wallet name",
            "VALIDATOR_WALLET_NAME": "🔐 Validator wallet name",
            "VALIDATOR_HOTKEY_NAME": "🔑 Validator hotkey name",
            "API_KEYS": "🔑 API keys (comma-separated)",
            "BASE_MINER_PORT": "🔌 Starting port for miners (decrementing)",
            "EXTERNAL_IP": "🌍 External IP address",
            "LOG_LEVEL": "📝 Logging level (DEBUG, INFO, WARNING, ERROR)",
        }

        if not self.env_file.exists():
            self.env_file.touch()

        load_dotenv(self.env_file)

        for key, prompt in prompts.items():
            current_value = os.getenv(key, "").strip()
            if not current_value:
                new_value = Prompt.ask(
                    f"[cyan]{prompt}[/cyan]", default=defaults[key]
                )
                set_key(self.env_file, key, new_value)
                os.environ[key] = new_value
            self.config[key] = os.getenv(key, defaults[key])

    def _get_external_ip(self) -> str:
        """Get the external IP address of the machine."""
        try:
            return (
                subprocess.run(
                    ["ipconfig", "getifaddr", "en0"], capture_output=True, text=True
                ).stdout.strip()
                or "0.0.0.0"
            )
        except Exception:
            return "0.0.0.0"

    def _get_free_ports(self, num_ports: int) -> List[int]:
        """Find a list of free ports starting from BASE_MINER_PORT."""
        ports = []
        base_port = int(self.config["BASE_MINER_PORT"])
        port = base_port

        while len(ports) < num_ports and port > base_port - 1000:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", port))
                    ports.append(port)
            except OSError:
                pass
            port -= 1

        if len(ports) < num_ports:
            raise RuntimeError(f"Could not find {num_ports} free ports")

        return ports

    def _validate_wallets(self) -> None:
        """Validate and setup required wallets."""
        console.print("\n[bold cyan]🔍 Validating wallets...[/bold cyan]")

        # Check coldkey exists
        coldkey_name = self.config["MINER_COLDKEY_NAME"]
        if not self._coldkey_exists(coldkey_name):
            console.print(f"[red]❌ Coldkey '{coldkey_name}' does not exist.[/red]")
            raise RuntimeError(
                f"Coldkey '{coldkey_name}' does not exist. Please create it first using btcli wallet new_coldkey."
            )

        console.print(f"[green]✅ Coldkey '{coldkey_name}' found[/green]")

        # Setup hotkeys
        num_miners = int(self.config["NUM_MINERS"])
        self.miners = []

        # Get all required ports upfront
        with console.status("[bold blue]🔌 Finding free ports...[/bold blue]"):
            self.ports = self._get_free_ports(num_miners)

        for i in range(num_miners):
            hotkey_name = f"mineshaft-{i+1}"
            status = "[yellow]⏳ Checking...[/yellow]"

            if not self._hotkey_exists(coldkey_name, hotkey_name):
                if (
                    Prompt.ask(
                        f"[yellow]⚠️  Hotkey '{hotkey_name}' does not exist. Create it?[/yellow]",
                        choices=["y", "n"],
                    )
                    == "y"
                ):
                    console.print(f"[blue]🔧 Creating hotkey {hotkey_name}[/blue]")
                    command = [
                        "btcli",
                        "wallet",
                        "new-hotkey",
                        "--wallet.name",
                        coldkey_name,
                        "--wallet.hotkey",
                        hotkey_name,
                        "--n_words",
                        "12",
                    ]
                    subprocess.run(command, check=True)
                    status = "[green]✅ Created[/green]"
                else:
                    status = "[red]❌ Skipped[/red]"
                    continue

            if not self._is_registered(coldkey_name, hotkey_name):
                if (
                    Prompt.ask(
                        f"[yellow]⚠️  Hotkey '{hotkey_name}' is not registered. Register it?[/yellow]",
                        choices=["y", "n"],
                    )
                    == "y"
                ):
                    console.print(f"[blue]🔧 Registering {hotkey_name}[/blue]")
                    self._register_hotkey(coldkey_name, hotkey_name)
                    status = "[green]✅ Registered[/green]"
                else:
                    status = "[red]❌ Skipped[/red]"
                    continue
            else:
                status = "[green]✅ Ready[/green]"

            self.miners.append(
                {
                    "coldkey_name": coldkey_name,
                    "hotkey_name": hotkey_name,
                    "port": self.ports[i],
                }
            )

        if not self.miners:
            raise RuntimeError(
                "No miners were configured. Please create and register at least one hotkey."
            )

    def _coldkey_exists(self, coldkey_name: str) -> bool:
        """Check if a coldkey exists."""
        try:
            bt.wallet(name=coldkey_name)
            return True
        except Exception:
            return False

    def _hotkey_exists(self, coldkey_name: str, hotkey_name: str) -> bool:
        """Check if a hotkey exists in the wallet."""
        try:
            wallet = bt.wallet(name=coldkey_name, hotkey=hotkey_name)
            return wallet.hotkey is not None
        except Exception:
            return False

    def _is_registered(self, coldkey_name: str, hotkey_name: str) -> bool:
        """Check if a hotkey is registered on the subnet."""
        try:
            wallet = bt.wallet(name=coldkey_name, hotkey=hotkey_name)
            subtensor = bt.subtensor(network=self.config["SUBTENSOR_ADDRESS"])
            return subtensor.is_hotkey_registered(
                wallet.hotkey.ss58_address, int(self.config["NETUID"])
            )
        except Exception:
            return False

    def _register_hotkey(self, coldkey_name: str, hotkey_name: str) -> None:
        """Register a hotkey and post its IP."""
        # Register hotkey
        command = [
            "btcli",
            "subnet",
            "register",
            "--wallet.name",
            coldkey_name,
            "--wallet.hotkey",
            hotkey_name,
            "--subtensor.chain_endpoint",
            self.config["SUBTENSOR_ADDRESS"],
            "--netuid",
            self.config["NETUID"],
        ]
        console.print(
            f"\n[blue]Running registration command: {' '.join(command)}[/blue]"
        )

        # Run registration with interactive output
        process = subprocess.Popen(
            command,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            universal_newlines=True,
        )

        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, command)

        # Post IP
        ip_command = [
            "fiber-post-ip",
            "--wallet.name",
            coldkey_name,
            "--wallet.hotkey",
            hotkey_name,
            "--subtensor.chain_endpoint",
            self.config["SUBTENSOR_ADDRESS"],
            "--netuid",
            self.config["NETUID"],
            "--external_ip",
            self.config["EXTERNAL_IP"],
            "--external_port",
            str(self._get_free_port()),
        ]
        console.print(
            f"\n[blue]Running fiber post IP command: {' '.join(ip_command)}[/blue]"
        )
        subprocess.run(ip_command, check=True)

    def _get_free_port(self) -> int:
        """Find a free port starting from 7999."""
        port = 7999
        while port > 7000:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", port))
                    return port
            except OSError:
                port -= 1
        raise RuntimeError("No free ports found")


class Mineshaft:
    def __init__(self, env_file: str = "mineshaft.env"):
        self.config = MineshaftConfig(env_file)
        self.processes: Dict[str, subprocess.Popen] = {}
        self.shutdown_requested = False

        self.logs_dir = Path("logs_mineshaft") / datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Redirect stderr to devnull to suppress asyncio cancellation errors
        self.original_stderr = sys.stderr
        sys.stderr = open(os.devnull, "w")

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        # Redirect stderr immediately to suppress all error output
        sys.stderr = open(os.devnull, "w")
        sys.stdout = open(os.devnull, "w")  # Also redirect stdout to be safe

        # Cancel all asyncio tasks in the validator
        if "validator" in self.processes:
            try:
                loop = asyncio.get_event_loop()
                for task in asyncio.all_tasks(loop):
                    task.cancel()
            except Exception:
                pass

        # Restore stdout for our shutdown messages
        sys.stdout = sys.__stdout__
        console.print(f"\n[yellow]⚠️   Received signal {signum}, shutting down...[/yellow]")
        self._cleanup_processes()
        
        # Restore stderr for final message
        sys.stderr = self.original_stderr
        console.print("[green]✨ Mineshaft stopped successfully[/green]")
        sys.exit(0)

    def _cleanup_processes(self) -> None:
        """Clean up all running processes."""
        if not self.processes:
            return

        console.print("\n[yellow]🧹  Cleaning up processes...[/yellow]")

        for name, obj in list(self.processes.items()):
            try:
                if name.endswith("_thread") or name == "validator_stderr":
                    continue
                pid = obj.pid
                console.print(f"[yellow]Stopping {name} (PID: {pid})...[/yellow]")
                obj.terminate()
                try:
                    obj.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    obj.kill()
                    obj.wait()
            except Exception as e:
                # Only show errors that aren't related to process termination or asyncio cancellation
                if not isinstance(e, (asyncio.CancelledError, subprocess.TimeoutExpired, httpx.ConnectTimeout)):
                    console.print(f"[red]Error cleaning up {name}: {e}[/red]")

        # Close any open file handles
        for name, obj in list(self.processes.items()):
            if name == "validator_stderr":
                obj.close()

        self.processes.clear()

    async def _start_miner(self, hotkey_name: str, port: int) -> None:
        """Start a miner process with output logging."""
        log_file = self.logs_dir / f'miner_{hotkey_name.split("-")[1]}.log'

        process = subprocess.Popen(
            ["uvicorn", "miner.main:app", "--host", "0.0.0.0", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            env={**os.environ, "MINER_HOTKEY": hotkey_name},
        )

        self.processes[f"miner_{hotkey_name}"] = process

        def stream_output():
            ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])")
            with open(log_file, "w") as f:
                for line in process.stdout:
                    clean_line = ansi_escape.sub("", line)
                    f.write(clean_line)
                    f.flush()

        thread = threading.Thread(target=stream_output, daemon=True)
        thread.start()
        self.processes[f"miner_{hotkey_name}_thread"] = thread

    def _start_validator(self) -> None:
        """Start the validator with output logging."""
        log_file = self.logs_dir / "validator.log"

        # Set environment variables to handle graceful shutdown
        env = {
            **os.environ,
            "VALIDATOR_WALLET": self.config.config["VALIDATOR_WALLET_NAME"],
            "VALIDATOR_HOTKEY": self.config.config["VALIDATOR_HOTKEY_NAME"],
            "PYTHONUNBUFFERED": "1",  # Ensure output is not buffered
        }

        # Create a null device for stderr
        null_device = open(os.devnull, "w")

        process = subprocess.Popen(
            ["uv", "run", "--no-sync", "validator/main.py"],
            stdout=subprocess.PIPE,
            stderr=null_device,  # Redirect stderr to null
            bufsize=1,
            universal_newlines=True,
            env=env,
        )

        self.processes["validator"] = process
        self.processes["validator_stderr"] = null_device  # Keep reference to close later

        def stream_output():
            ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])")
            with open(log_file, "w") as f:
                for line in process.stdout:
                    clean_line = ansi_escape.sub("", line)
                    f.write(clean_line)
                    f.flush()
                    print(line, end="")

        thread = threading.Thread(target=stream_output, daemon=True)
        thread.start()
        self.processes["validator_thread"] = thread

    async def run(self) -> None:
        """Main run method."""
        try:
            console.print("\n[bold green]🚀 Starting Mineshaft...[/bold green]")

            console.print("[bold blue]⛏️ Starting miners...[/bold blue]")
            for miner in self.config.miners:
                await self._start_miner(miner["hotkey_name"], miner["port"])

            table = Table(
                show_header=True, header_style="bold magenta", title="Mineshaft Status"
            )
            table.add_column("Component", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("PID", style="yellow")

            for name, process in self.processes.items():
                if "miner" in name and not name.endswith("_thread"):
                    table.add_row(
                        f"⛏️  {name.replace('miner_', '')}",
                        "[green]✅ Started[/green]",
                        str(process.pid),
                    )

            console.print("\n")
            console.print(table)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Waiting for miners to initialize...", total=5)
                for i in range(5):
                    await asyncio.sleep(1)
                    progress.update(task, advance=1)

            console.print("[bold blue]🔧 Starting validator...[/bold blue]")
            self._start_validator()

            console.print(
                "\n[bold green]✨ Mineshaft is running! Press Ctrl+C to stop.[/bold green]"
            )

            while True:
                await asyncio.sleep(1)

        except Exception as e:
            console.print(f"[red]❌ Error: {e}[/red]")
        finally:
            self._cleanup_processes()


def main():
    """Main entry point."""
    asyncio.run(Mineshaft().run())


if __name__ == "__main__":
    main()
