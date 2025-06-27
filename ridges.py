#!.venv/bin/python3

"""
Ridges CLI - Command-line interface for managing Ridges miners
"""

import hashlib
import click
from fiber.chain.chain_utils import load_hotkey_keypair
import httpx
import os
import sys
import time
import subprocess
from typing import Optional
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt

console = Console()
DEFAULT_API_BASE_URL = "https://testnet.ridges.ai"
CONFIG_FILE = "miner/.env"

load_dotenv(CONFIG_FILE)

def run_command(cmd: str, capture_output: bool = True) -> tuple[int, str, str]:
    """Run a shell command and return (return_code, stdout, stderr)"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=capture_output, text=True)
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)

def check_docker_image(image_name: str) -> bool:
    """Check if a Docker image exists"""
    returncode, stdout, _ = run_command(f"docker images -q {image_name}")
    return returncode == 0 and stdout.strip() != ""

def build_docker_image(path: str, tag: str) -> bool:
    """Build a Docker image"""
    console.print(f"🔨 Building {tag} from {path}...", style="yellow")
    returncode, stdout, stderr = run_command(f"docker build -t {tag} {path}", capture_output=False)
    if returncode == 0:
        console.print(f"✅ {tag} built successfully", style="green")
        return True
    else:
        console.print(f"💥 Failed to build {tag}", style="red")
        return False

def check_pm2_process(process_name: str = "ridges-validator") -> tuple[bool, str]:
    """Check if PM2 process is running and return (is_running, last_check_time)"""
    returncode, stdout, _ = run_command(f"pm2 list | grep {process_name}")
    if returncode == 0 and process_name in stdout:
        # Try to get last check time from PM2 logs
        if process_name == "ridges-validator-updater":
            # For updater, look for "Checking for updates" lines
            log_returncode, log_output, _ = run_command(f"pm2 logs {process_name} --lines 20 --nostream")
            if log_returncode == 0 and log_output:
                # Look for the most recent "Checking for updates" line
                import re
                from datetime import datetime
                for line in log_output.strip().split('\n'):
                    if "Checking for updates" in line:
                        timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                        if timestamp_match:
                            timestamp_str = timestamp_match.group(1)
                            try:
                                timestamp_dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                                diff = datetime.now() - timestamp_dt
                                relative = f"{diff.seconds // 60}m {diff.seconds % 60}s ago"
                                return True, f"{timestamp_str} ({relative})"
                            except ValueError:
                                return True, timestamp_str
        else:
            # For other processes, use the original logic
            log_returncode, log_output, _ = run_command(f"pm2 logs {process_name} --lines 2 --nostream")
            if log_returncode == 0 and log_output:
                # Extract timestamp from log line
                lines = log_output.strip().split('\n')
                if lines:
                    first_line = lines[0]
                    # Look for timestamp pattern
                    import re
                    timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', first_line)
                    if timestamp_match:
                        return True, timestamp_match.group(1)
        return True, "unknown"
    return False, ""

def get_pm2_logs(process_name: str = "ridges-validator", lines: int = 15) -> str:
    """Get the last N lines of PM2 logs"""
    returncode, stdout, _ = run_command(f"pm2 logs {process_name} --lines {lines} --nostream")
    return stdout if returncode == 0 else "No logs available"

class RidgesConfig:
    def __init__(self):
        self.config_file = CONFIG_FILE
        if os.path.exists(self.config_file):
            load_dotenv(self.config_file)
    
    def save_config(self, key: str, value: str):
        config_lines = []
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                config_lines = f.readlines()
        
        key_found = False
        for i, line in enumerate(config_lines):
            if line.strip().startswith(f"{key}="):
                config_lines[i] = f"{key}={value}\n"
                key_found = True
                break
        
        if not key_found:
            config_lines.append(f"{key}={value}\n")
        
        with open(self.config_file, 'w') as f:
            f.writelines(config_lines)
        
        os.environ[key] = value
    
    def get_or_prompt(self, key: str, prompt_text: str, default: Optional[str] = None) -> str:
        value = os.getenv(key)
        
        if not value:
            value = Prompt.ask(f"🎯 {prompt_text}", default=default) if default else Prompt.ask(f"🎯 {prompt_text}")
            self.save_config(key, value)
            console.print(f"✨ Configuration saved: {key} → {self.config_file}", style="bold green")
        
        return value

class RidgesCLI:
    def __init__(self, api_url: Optional[str] = None):
        self.api_url = api_url or DEFAULT_API_BASE_URL
        self.config = RidgesConfig()
        
    def get_coldkey_name(self) -> str:
        return self.config.get_or_prompt("RIDGES_COLDKEY_NAME", "Enter your coldkey name", default="miner")
    
    def get_hotkey_name(self) -> str:
        return self.config.get_or_prompt("RIDGES_HOTKEY_NAME", "Enter your hotkey name", default="default")
    
    def get_keypair(self):
        return load_hotkey_keypair(self.get_coldkey_name(), self.get_hotkey_name())
    
    def get_agent_file_path(self) -> str:
        return self.config.get_or_prompt("RIDGES_AGENT_FILE", "Enter the path to your agent.py file", default="miner/agent.py")

@click.group()
@click.version_option(version="1.0.0")
@click.option("--url", help=f"Custom API URL (default: {DEFAULT_API_BASE_URL})")
@click.pass_context
def cli(ctx, url):
    """Ridges CLI - Manage your Ridges miners"""
    ctx.ensure_object(dict)
    ctx.obj['url'] = url

@cli.command()
@click.option("--file", help="Path to agent.py file")
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.pass_context
def upload(ctx, hotkey_name: Optional[str], file: Optional[str], coldkey_name: Optional[str]):
    """Upload a miner agent to the Ridges API."""
    api_url = ctx.obj.get('url')
    ridges = RidgesCLI(api_url)
    
    file = file or ridges.get_agent_file_path()
    coldkey_name = coldkey_name or ridges.get_coldkey_name()
    hotkey_name = hotkey_name or ridges.get_hotkey_name()
    
    if not os.path.exists(file):
        console.print(f"💥 File not found: {file}", style="bold red")
        sys.exit(1)
    
    if os.path.basename(file) != "agent.py":
        console.print("💥 File must be named 'agent.py'", style="bold red")
        sys.exit(1)
    
    console.print(Panel(
        f"[bold cyan]📤 Uploading Agent[/bold cyan]\n"
        f"[yellow]File:[/yellow] {file}\n"
        f"[yellow]Coldkey:[/yellow] {coldkey_name}\n"
        f"[yellow]Hotkey:[/yellow] {hotkey_name}\n"
        f"[yellow]API URL:[/yellow] {ridges.api_url}",
        title="🚀 Upload Configuration",
        border_style="cyan"
    ))

    
    try:
        with open(file, 'rb') as f:
            files = {'agent_file': ('agent.py', f, 'text/plain')}
            content_hash = hashlib.sha256(f.read()).hexdigest()
            keypair = ridges.get_keypair()
            public_key = keypair.public_key.hex()
            name_and_version = get_name_and_version_number(ridges.api_url, keypair.ss58_address)
            if name_and_version is None:
                name = Prompt.ask("Enter a name for your miner agent")
                version_num = -1
            else:
                name, version_num = name_and_version

            file_info = f"{keypair.ss58_address}:{content_hash}:{version_num}"
            signature = keypair.sign(file_info).hex()
            payload = {'miner_hotkey': keypair.ss58_address, 'public_key': public_key, 'file_info': file_info, 'signature': signature, 'name': name}

            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console, transient=True) as progress:
                progress.add_task("🔐 Signing and preparing upload...", total=None)
                time.sleep(1)
            
            with httpx.Client() as client:
                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console, transient=True) as progress:
                    progress.add_task(f"📡 Uploading {name} to Ridges API...", total=None)
                    response = client.post(f"{ridges.api_url}/upload/agent", files=files, data=payload, timeout=120)
                
                if response.status_code == 200:
                    result = response.json()
                    console.print(f"✨ {result.get('message', 'Upload successful')}", style="bold green")
                    console.print(Panel(
                        f"[bold green]🎉 Upload Complete[/bold green]\n"
                        f"[cyan]Your miner '{name}' has been uploaded to {ridges.api_url}[/cyan]\n"
                        f"[cyan]It has been queued for evaluation.[/cyan]",
                        title="✨ Success",
                        border_style="green"
                    ))
                else:
                    error_detail = response.json().get('detail', 'Unknown error') if response.headers.get('content-type', '').startswith('application/json') else response.text
                    console.print(f"💥 Upload failed: {error_detail}", style="bold red")
                    sys.exit(1)
                    
    except httpx.RequestError as e:
        console.print(f"💥 Network error: {e}", style="bold red")
        sys.exit(1)
    except Exception as e:
        console.print(f"💥 Unexpected error: {e}", style="bold red")
        sys.exit(1)

def get_name_and_version_number(url: str, miner_hotkey: str) -> Optional[tuple[str, int]]:
    try:
        with httpx.Client() as client:
            response = client.get(f"{url}/retrieval/agent?miner_hotkey={miner_hotkey}&include_code=false")
            if response.status_code == 200:
                latest_agent = response.json().get("latest_agent")
                if not latest_agent:
                    return None
                latest_version = latest_agent.get("latest_version")
                return latest_agent.get("name"), latest_version.get("version_num")
            else:
                return None
    except Exception as e:
        return None

@cli.command()
def version():
    """Show version information."""
    console.print(Panel(
        "[bold yellow]🌟 Ridges CLI v1.0.0[/bold yellow]\n"
        "[cyan]Command-line interface for managing Ridges miners and validators[/cyan]\n\n"
        "[green]Features:[/green]\n"
        "• 🚀 Upload miners to the network\n"
        "• ⚙️ Manage configuration settings\n",
        title="📋 Version Information",
        border_style="yellow"
    ))

@cli.group()
def validator():
    """Manage Ridges validators"""
    pass

@validator.command()
@click.option("--no-auto-update", is_flag=True, help="Run validator directly in foreground without auto-updater")
def run(no_auto_update: bool):
    """Run the Ridges validator with auto-updater."""
    
    # Check Docker containers
    console.print("🔍 Checking Docker containers...", style="cyan")
    
    sandbox_missing = not check_docker_image("sandbox-runner")
    proxy_missing = not check_docker_image("sandbox-nginx-proxy")
    
    if sandbox_missing or proxy_missing:
        console.print(Panel(
            "[bold yellow]🐳 Docker containers missing![/bold yellow]\n"
            f"[red]sandbox-runner:[/red] {'❌ Missing' if sandbox_missing else '✅ Found'}\n"
            f"[red]sandbox-nginx-proxy:[/red] {'❌ Missing' if proxy_missing else '✅ Found'}",
            title="Docker Status",
            border_style="yellow"
        ))
        
        if not Prompt.ask("Would you like to build the missing containers?", choices=["Y", "n"], default="y") == "y":
            console.print("❌ Aborting - containers required", style="red")
            sys.exit(1)
        
        if sandbox_missing and not build_docker_image("validator/sandbox", "sandbox-runner"):
            sys.exit(1)
        if proxy_missing and not build_docker_image("validator/sandbox/proxy/", "sandbox-nginx-proxy"):
            sys.exit(1)
    
    if no_auto_update:
        # Run validator directly in foreground
        console.print("🚀 Starting validator...", style="yellow")
        console.print("Press Ctrl+C to stop", style="cyan")
        
        # Run the validator directly using uv
        returncode, stdout, stderr = run_command("uv run validator/main.py", capture_output=False)
        
        if returncode != 0:
            console.print(f"💥 Validator exited with error code {returncode}", style="red")
            sys.exit(1)
        return
    
    # Check if auto-updater is running
    console.print("🔍 Checking auto-updater status...", style="cyan")
    is_running, last_check = check_pm2_process("ridges-validator-updater")
    
    if is_running:
        console.print(Panel(
            f"[bold green]✅ Auto-updater is already running![/bold green]\n"
            f"[cyan]Last checked:[/cyan] {last_check}\n"
            f"[cyan]Status:[/cyan] Monitoring for updates every 5 minutes",
            title="🚀 Validator Status",
            border_style="green"
        ))
        return
    
    console.print("🚀 Starting validator...", style="yellow")
    
    # Run the auto-updater script under PM2 using the specified process name
    returncode, stdout, stderr = run_command("pm2 start ./validator_auto_update.sh --name ridges-validator-updater", capture_output=False)
    
    if returncode != 0:
        console.print(f"💥 Failed to start auto-updater", style="red")
        sys.exit(1)
    
    # Verify it started and show logs
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task("Starting auto-updater...", total=None)
        time.sleep(3)  # Show loading indicator for 3 seconds
    is_running, _ = check_pm2_process("ridges-validator-updater")
    
    if is_running:
        console.print(Panel(
            "[bold green]🎉 Auto-updater started successfully![/bold green]\n"
            "[cyan]The validator is now running and will auto-update every 5 minutes.[/cyan]",
            title="✨ Success",
            border_style="green"
        ))
        
        # Show last 15 lines of logs
        logs = get_pm2_logs()
        if logs:
            console.print(Panel(
                f"[bold cyan]📋 Recent Logs:[/bold cyan]\n{logs}",
                title="📄 Log Output",
                border_style="cyan"
            ))
    else:
        console.print("💥 Auto-updater failed to start properly", style="red")
        sys.exit(1)

@validator.command()
def stop():
    """Stop the Ridges validator and auto-updater."""
    console.print("🛑 Stopping Ridges validator processes...", style="yellow")
    
    # Stop both processes
    processes_to_stop = ["ridges-validator", "ridges-validator-updater"]
    stopped_processes = []
    
    for process in processes_to_stop:
        returncode, stdout, stderr = run_command(f"pm2 delete {process}")
        if returncode == 0:
            stopped_processes.append(process)
            console.print(f"✅ Stopped {process}", style="green")
        else:
            console.print(f"⚠️  {process} was not running or could not be stopped", style="yellow")
    
    if stopped_processes:
        console.print(Panel(
            f"[bold green]🎉 Successfully stopped:[/bold green]\n"
            f"[cyan]{', '.join(stopped_processes)}[/cyan]",
            title="✨ Stop Complete",
            border_style="green"
        ))
    else:
        console.print("ℹ️  No validator processes were running", style="cyan")

@validator.command()
def logs():
    """Show real-time logs from the Ridges validator and auto-updater."""
    console.print("📋 Showing real-time logs from validator processes...", style="cyan")
    console.print("Press Ctrl+C to stop", style="yellow")
    
    # Show logs from both processes
    returncode, stdout, stderr = run_command("pm2 logs ridges-validator ridges-validator-updater", capture_output=False)
    
    if returncode != 0:
        console.print("💥 Failed to show logs", style="red")
        sys.exit(1)

@validator.command()
def restart():
    """Restart the Ridges validator and auto-updater."""
    console.print("🔄 Restarting Ridges validator processes...", style="yellow")
    
    # Stop both processes
    processes_to_restart = ["ridges-validator", "ridges-validator-updater"]
    stopped_processes = []
    
    for process in processes_to_restart:
        returncode, stdout, stderr = run_command(f"pm2 delete {process}")
        if returncode == 0:
            stopped_processes.append(process)
            console.print(f"✅ Stopped {process}", style="green")
        else:
            console.print(f"⚠️  {process} was not running or could not be stopped", style="yellow")
    
    if stopped_processes:
        console.print("⏳ Waiting 2 seconds before restarting...", style="cyan")
        time.sleep(2)
        
        # Restart the auto-updater
        console.print("🚀 Restarting auto-updater...", style="yellow")
        returncode, stdout, stderr = run_command("pm2 start ./validator_auto_update.sh --name ridges-validator-updater", capture_output=False)
        
        if returncode == 0:
            console.print(Panel(
                "[bold green]🎉 Validator restarted successfully![/bold green]\n"
                "[cyan]The validator is now running and will auto-update every 5 minutes.[/cyan]",
                title="✨ Restart Complete",
                border_style="green"
            ))
        else:
            console.print("💥 Failed to restart validator", style="red")
            sys.exit(1)
    else:
        console.print("ℹ️  No validator processes were running to restart", style="cyan")

@cli.group()
def platform():
    """Manage Ridges API platform"""
    pass

@platform.command()
@click.option("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
@click.option("--port", default=8000, help="Port to bind to (default: 8000)")
def run(host: str, port: int):
    """Run the Ridges API platform using PM2."""
    console.print(Panel(
        f"[bold cyan]🚀 Starting Ridges API Platform[/bold cyan]\n"
        f"[yellow]Host:[/yellow] {host}\n"
        f"[yellow]Port:[/yellow] {port}\n"
        f"[yellow]Process:[/yellow] ridges-api-platform",
        title="🌐 Platform Configuration",
        border_style="cyan"
    ))
    
    # Check if platform is already running
    is_running, _ = check_pm2_process("ridges-api-platform")
    if is_running:
        console.print(Panel(
            "[bold yellow]⚠️  API platform is already running![/bold yellow]\n"
            "[cyan]Use 'ridges.py platform logs' to view logs[/cyan]\n"
            "[cyan]Use 'ridges.py platform stop' to stop the platform[/cyan]",
            title="🔄 Platform Status",
            border_style="yellow"
        ))
        return
    
    # Start the platform with PM2
    returncode, stdout, stderr = run_command(f"pm2 start 'uvicorn api.src.main:app --host {host} --port {port}' --name ridges-api-platform", capture_output=False)
    
    if returncode != 0:
        console.print(f"💥 Failed to start API platform", style="red")
        sys.exit(1)
    
    # Verify it started
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task("Starting API platform...", total=None)
        time.sleep(3)  # Show loading indicator for 3 seconds
    
    is_running, _ = check_pm2_process("ridges-api-platform")
    
    if is_running:
        console.print(Panel(
            "[bold green]🎉 API platform started successfully![/bold green]\n"
            f"[cyan]Platform is running on {host}:{port}[/cyan]\n"
            "[cyan]Use 'ridges.py platform logs' to view real-time logs[/cyan]",
            title="✨ Success",
            border_style="green"
        ))
    else:
        console.print("💥 API platform failed to start properly", style="red")
        sys.exit(1)

@platform.command()
def stop():
    """Stop the Ridges API platform."""
    console.print("🛑 Stopping Ridges API platform...", style="yellow")
    
    returncode, stdout, stderr = run_command("pm2 delete ridges-api-platform")
    if returncode == 0:
        console.print(Panel(
            "[bold green]🎉 API platform stopped successfully![/bold green]",
            title="✨ Stop Complete",
            border_style="green"
        ))
    else:
        console.print("⚠️  API platform was not running or could not be stopped", style="yellow")

@platform.command()
def logs():
    """Show real-time logs from the Ridges API platform."""
    console.print("📋 Showing real-time logs from API platform...", style="cyan")
    console.print("Press Ctrl+C to stop", style="yellow")
    
    # Show logs from the API platform
    returncode, stdout, stderr = run_command("pm2 logs ridges-api-platform", capture_output=False)
    
    if returncode != 0:
        console.print("💥 Failed to show logs", style="red")
        sys.exit(1)

@platform.command()
def restart():
    """Restart the Ridges API platform."""
    console.print("🔄 Restarting Ridges API platform...", style="yellow")
    
    # Stop the platform
    returncode, stdout, stderr = run_command("pm2 delete ridges-api-platform")
    if returncode == 0:
        console.print("✅ Stopped API platform", style="green")
    else:
        console.print("⚠️  API platform was not running or could not be stopped", style="yellow")
    
    console.print("⏳ Waiting 2 seconds before restarting...", style="cyan")
    time.sleep(2)
    
    # Restart the platform with default settings
    console.print("🚀 Restarting API platform...", style="yellow")
    returncode, stdout, stderr = run_command("pm2 start 'uvicorn api.src.main:app --host 0.0.0.0 --port 8000' --name ridges-api-platform", capture_output=False)
    
    if returncode == 0:
        console.print(Panel(
            "[bold green]🎉 API platform restarted successfully![/bold green]\n"
            "[cyan]Platform is running on 0.0.0.0:8000[/cyan]\n"
            "[cyan]Use 'ridges.py platform logs' to view real-time logs[/cyan]",
            title="✨ Restart Complete",
            border_style="green"
        ))
    else:
        console.print("💥 Failed to restart API platform", style="red")
        sys.exit(1)

if __name__ == "__main__":
    cli() 