#!.venv/bin/python3

"""
Ridges CLI - Elegant command-line interface for managing Ridges miners and validators
"""

import hashlib
import time
from fiber.chain.chain_utils import load_hotkey_keypair
import httpx
import os
import subprocess
from typing import Optional
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from dotenv import load_dotenv

console = Console()
DEFAULT_API_BASE_URL = "https://platform.ridges.ai"
CONFIG_FILE = "miner/.env"

load_dotenv(CONFIG_FILE)
load_dotenv(".env")

def run_cmd(cmd: str, capture: bool = True) -> tuple[int, str, str]:
    """Run command and return (code, stdout, stderr)"""
    result = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
    return result.returncode, result.stdout, result.stderr
run_cmd("uv add click")
import click

def check_docker(image: str) -> bool:
    """Check if Docker image exists"""
    code, output, _ = run_cmd(f"docker images -q {image}")
    return code == 0 and output.strip() != ""

def build_docker(path: str, tag: str) -> bool:
    """Build Docker image"""
    console.print(f"🔨 Building {tag}...", style="yellow")
    return run_cmd(f"docker build -t {tag} {path}", capture=False)[0] == 0

def check_pm2(process: str = "ridges-validator") -> tuple[bool, str]:
    """Check if PM2 process is running"""
    code, output, _ = run_cmd("pm2 list")
    if code != 0:
        return False, ""
    
    # Parse PM2 list output to find exact process name
    lines = output.strip().split('\n')
    for line in lines:
        if '│' in line and process in line:
            # Split by │ and get the name column (index 2, after id and │)
            parts = line.split('│')
            if len(parts) >= 3:
                process_name = parts[2].strip()
                if process_name == process:
                    return True, "running"
    return False, ""

def get_logs(process: str = "ridges-validator", lines: int = 15) -> str:
    """Get PM2 logs"""
    return run_cmd(f"pm2 logs {process} --lines {lines} --nostream")[1]

class Config:
    def __init__(self):
        self.file = CONFIG_FILE
        if os.path.exists(self.file):
            load_dotenv(self.file)
    
    def save(self, key: str, value: str):
        lines = []
        if os.path.exists(self.file):
            with open(self.file, 'r') as f:
                lines = f.readlines()
        
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                found = True
                break
        
        if not found:
            lines.append(f"{key}={value}\n")
        
        with open(self.file, 'w') as f:
            f.writelines(lines)
        os.environ[key] = value
    
    def get_or_prompt(self, key: str, prompt: str, default: Optional[str] = None) -> str:
        value = os.getenv(key)
        if not value:
            value = Prompt.ask(f"🎯 {prompt}", default=default) if default else Prompt.ask(f"🎯 {prompt}")
            self.save(key, value)
        return value

class RidgesCLI:
    def __init__(self, api_url: Optional[str] = None):
        self.api_url = api_url or DEFAULT_API_BASE_URL
        self.config = Config()
    
    def get_keypair(self):
        coldkey = self.config.get_or_prompt("RIDGES_COLDKEY_NAME", "Enter your coldkey name", "miner")
        hotkey = self.config.get_or_prompt("RIDGES_HOTKEY_NAME", "Enter your hotkey name", "default")
        return load_hotkey_keypair(coldkey, hotkey)
    
    def get_agent_path(self) -> str:
        return self.config.get_or_prompt("RIDGES_AGENT_FILE", "Enter the path to your agent.py file", "miner/agent.py")

@click.group()
@click.version_option(version="1.0.0")
@click.option("--url", help=f"Custom API URL (default: {DEFAULT_API_BASE_URL})")
@click.pass_context
def cli(ctx, url):
    """Ridges CLI - Manage your Ridges miners and validators"""
    ctx.ensure_object(dict)
    ctx.obj['url'] = url

@cli.command()
@click.option("--file", help="Path to agent.py file")
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.pass_context
def upload(ctx, hotkey_name: Optional[str], file: Optional[str], coldkey_name: Optional[str]):
    """Upload a miner agent to the Ridges API."""
    ridges = RidgesCLI(ctx.obj.get('url'))
    
    file = file or ridges.get_agent_path()
    if not os.path.exists(file) or os.path.basename(file) != "agent.py":
        console.print("💥 File must be named 'agent.py' and exist", style="bold red")
        return
    
    console.print(Panel(f"[bold cyan]📤 Uploading Agent[/bold cyan]\n[yellow]File:[/yellow] {file}\n[yellow]API:[/yellow] {ridges.api_url}", title="🚀 Upload", border_style="cyan"))
    
    try:
        with open(file, 'rb') as f:
            files = {'agent_file': ('agent.py', f, 'text/plain')}
            content_hash = hashlib.sha256(f.read()).hexdigest()
            keypair = ridges.get_keypair()
            public_key = keypair.public_key.hex()
            
            # Get name and version
            name_and_version = get_name_and_version(ridges.api_url, keypair.ss58_address)
            if name_and_version is None:
                name = Prompt.ask("Enter a name for your miner agent")
                version_num = -1
            else:
                name, version_num = name_and_version

            file_info = f"{keypair.ss58_address}:{content_hash}:{version_num}"
            signature = keypair.sign(file_info).hex()
            payload = {'public_key': public_key, 'file_info': file_info, 'signature': signature, 'name': name}

            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console, transient=True) as progress:
                progress.add_task("🔐 Signing and uploading...", total=None)
                
                with httpx.Client() as client:
                    response = client.post(f"{ridges.api_url}/upload/agent", files=files, data=payload, timeout=120)
                
                if response.status_code == 200:
                    console.print(Panel(f"[bold green]🎉 Upload Complete[/bold green]\n[cyan]Miner '{name}' uploaded successfully![/cyan]", title="✨ Success", border_style="green"))
                else:
                    error = response.json().get('detail', 'Unknown error') if response.headers.get('content-type', '').startswith('application/json') else response.text
                    console.print(f"💥 Upload failed: {error}", style="bold red")
                    
    except Exception as e:
        console.print(f"💥 Error: {e}", style="bold red")

def get_name_and_version(url: str, miner_hotkey: str) -> Optional[tuple[str, int]]:
    try:
        with httpx.Client() as client:
            response = client.get(f"{url}/retrieval/agent?miner_hotkey={miner_hotkey}&include_code=false")
            if response.status_code == 200:
                latest_agent = response.json().get("latest_agent")
                if latest_agent:
                    latest_version = latest_agent.get("latest_version")
                    return latest_agent.get("name"), latest_version.get("version_num")
    except Exception:
        pass
    return None



@cli.group()
def validator():
    """Manage Ridges validators"""
    pass

@validator.command()
@click.option("--no-auto-update", is_flag=True, help="Run validator directly in foreground")
@click.option("--rebuild-containers", is_flag=True, help="Rebuild Docker containers")
def run(no_auto_update: bool, rebuild_containers: bool):
    """Run the Ridges validator."""

    if rebuild_containers:
        console.print("🔨 Rebuilding Docker containers...", style="yellow")
        if not build_docker("validator/sandbox", "sandbox-runner"):
            return
        if not build_docker("validator/sandbox/proxy", "sandbox-nginx-proxy"):
            return
    
    # Check Docker containers
    missing = [img for img in ["sandbox-runner", "sandbox-nginx-proxy"] if not check_docker(img)]
    if missing:
        console.print(Panel(f"[bold yellow]🐳 Missing containers:[/bold yellow]\n[red]{', '.join(missing)}[/red]", title="Docker Status", border_style="yellow"))
        if Prompt.ask("Build missing containers?", choices=["y", "n"], default="y") == "y":
            for img in missing:
                path = "validator/sandbox" if img == "sandbox-runner" else "validator/sandbox/proxy/"
                if not build_docker(path, img):
                    return
        else:
            return
    
    if no_auto_update:
        console.print("🚀 Starting validator...", style="yellow")
        run_cmd("uv run validator/main.py", capture=False)
        return
    
    # Check if already running
    is_running, _ = check_pm2("ridges-validator-updater")
    if is_running:
        console.print("✅ Auto-updater already running!", style="green")
        console.print("📋 Showing validator logs...", style="cyan")
        run_cmd("pm2 logs ridges-validator ridges-validator-updater", capture=False)
        return
    
    # Start validator
    console.print("🚀 Starting validator...", style="yellow")
    run_cmd("uv pip install -e .", capture=False)
    run_cmd("pm2 start 'uv run validator/main.py' --name ridges-validator", capture=False)
    
    # Start auto-updater in background
    if run_cmd(f"pm2 start './ridges.py validator update --every 5' --name ridges-validator-updater", capture=False)[0] == 0:
        console.print(Panel(f"[bold green]🎉 Auto-updater started![/bold green]\n[cyan]Validator running with auto-updates every 5 minutes.[/cyan]", title="✨ Success", border_style="green"))
        console.print("📋 Showing validator logs...", style="cyan")
        run_cmd("pm2 logs ridges-validator ridges-validator-updater", capture=False)
    else:
        console.print("💥 Failed to start validator", style="red")

@validator.command()
def stop():
    """Stop the Ridges validator."""
    stopped = [p for p in ["ridges-validator", "ridges-validator-updater"] if run_cmd(f"pm2 delete {p}")[0] == 0]
    if stopped:
        console.print(Panel(f"[bold green]🎉 Stopped:[/bold green]\n[cyan]{', '.join(stopped)}[/cyan]", title="✨ Stop Complete", border_style="green"))
    else:
        console.print("ℹ️  No validator processes running", style="cyan")

@validator.command()
def logs():
    """Show validator logs."""
    console.print("📋 Showing validator logs...", style="cyan")
    run_cmd("pm2 logs ridges-validator ridges-validator-updater", capture=False)

@validator.command()
@click.option("--every", default=None, type=int, help="Run in loop every N minutes (default: update once and exit)")
def update(every: Optional[int]):
    """Update validator code and restart."""
    while True:
        # Get current commit and pull updates
        code, current_commit, _ = run_cmd("git rev-parse HEAD")
        if code != 0 or run_cmd("git pull")[0] != 0:
            console.print("💥 Git operation failed", style="red")
            break
        
        # Check if updates applied
        code, new_commit, _ = run_cmd("git rev-parse HEAD")
        if code != 0 or current_commit.strip() == new_commit.strip():
            console.print("No updates available")
            if not every:
                break
            console.print(f"Sleeping for {every} minutes...")
            time.sleep(every * 60)
            continue
        
        # Update deps and restart validator
        console.print("✨ Updates found! Restarting validator...", style="green")
        run_cmd("uv pip install -e .")
        is_running, _ = check_pm2("ridges-validator")
        run_cmd("pm2 restart ridges-validator" if is_running else "pm2 start 'uv run validator/main.py' --name ridges-validator")
        console.print("Validator updated!")
        
        if not every:
            break
            
        console.print(f"Sleeping for {every} minutes...")
        time.sleep(every * 60)

@cli.group()
def platform():
    """Manage Ridges API platform"""
    pass

@platform.command()
@click.option("--no-auto-update", is_flag=True, help="Run platform directly in foreground")
def run(no_auto_update: bool):
    """Run the Ridges API platform."""
    console.print(Panel(f"[bold cyan]🚀 Starting Platform[/bold cyan]", title="🌐 Platform", border_style="cyan"))
    
    # Check if running
    is_running, _ = check_pm2("ridges-api-platform")
    if is_running:
        console.print(Panel("[bold yellow]⚠️  Platform already running![/bold yellow]", title="🔄 Status", border_style="yellow"))
        return
    
    # Remove old venv, create new venv, activate new venv, download dependencies
    if run_cmd("rm -rf .venv")[0] == 0:
        console.print("🔄 Removed old venv", style="yellow")
    else:
        console.print("💥 Failed to remove old venv", style="red")
        return
    if run_cmd("uv venv .venv")[0] == 0:
        console.print("🔄 Created new venv", style="yellow")
    else:
        console.print("💥 Failed to create new venv", style="red")
        return
    if run_cmd(". .venv/bin/activate")[0] == 0:
        console.print("🔄 Activated new venv", style="yellow")
    else:
        console.print("💥 Failed to activate new venv", style="red")
        return
    if run_cmd("uv pip install -e .")[0] == 0:
        console.print("🔄 Downloaded dependencies", style="yellow")
    else:
        console.print("💥 Failed to download dependencies", style="red")
        return
    
    if no_auto_update:
        console.print("🚀 Starting platform...", style="yellow")
        run_cmd("uv run -m api.src.main", capture=False)
        return
    
    # Start platform
    if run_cmd(f"pm2 start '.venv/bin/ddtrace-run uv run -m api.src.main' --name ridges-api-platform", capture=False)[0] == 0:
        console.print(Panel(f"[bold green]🎉 Platform started![/bold green] Running on 0.0.0.0:8000", title="✨ Success", border_style="green"))
        console.print("📋 Showing platform logs...", style="cyan")
        run_cmd("pm2 logs ridges-api-platform", capture=False)
    else:
        console.print("💥 Failed to start platform", style="red")

@platform.command()
def stop():
    """Stop the Ridges API platform."""
    if run_cmd("pm2 delete ridges-api-platform")[0] == 0:
        console.print(Panel("[bold green]🎉 Platform stopped![/bold green]", title="✨ Stop Complete", border_style="green"))
    else:
        console.print("⚠️  Platform not running", style="yellow")

@platform.command()
def logs():
    """Show platform logs."""
    console.print("📋 Showing platform logs...", style="cyan")
    run_cmd("pm2 logs ridges-api-platform", capture=False)

@platform.command()
def update():
    """Update platform code and restart."""
    # Get current commit and pull updates
    code, current_commit, _ = run_cmd("git rev-parse HEAD")
    if code != 0:
        console.print("💥 Failed to get commit hash", style="red")
        return
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console, transient=True) as progress:
        progress.add_task("📥 Pulling changes...", total=None)
        if run_cmd("git pull")[0] != 0:
            console.print("💥 Git pull failed", style="red")
            return
    
    # Check if updates applied
    code, new_commit, _ = run_cmd("git rev-parse HEAD")
    if code != 0 or current_commit.strip() == new_commit.strip():
        console.print(Panel("[bold yellow]ℹ️  No updates available[/bold yellow]", title="📋 Status", border_style="yellow"))
        return 0
    
    # Update deps and restart
    console.print(Panel("[bold green]✨ Updates found![/bold green]\n[cyan]Updating deps and restarting...[/cyan]", title="🔄 Update", border_style="green"))
    run_cmd("uv pip install -e .")
    run_cmd("pm2 restart ridges-api-platform")
    console.print(Panel("[bold green]🎉 Platform updated![/bold green]", title="✨ Complete", border_style="green"))

if __name__ == "__main__":
    run_cmd(". .venv/bin/activate")
    cli() 