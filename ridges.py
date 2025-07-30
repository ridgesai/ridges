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
import requests
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

validator_tracing = False
if os.getenv("DD_API_KEY") and os.getenv("DD_APP_KEY") and os.getenv("DD_HOSTNAME") and os.getenv("DD_SITE") and os.getenv("DD_ENV") and os.getenv("DD_SERVICE"):
    validator_tracing = True

def run_cmd(cmd: str, capture: bool = True) -> tuple[int, str, str]:
    """Run command and return (code, stdout, stderr)"""
    try:
        if capture:
            result = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
            return result.returncode, result.stdout, result.stderr
        else:
            # For non-captured commands, use Popen for better KeyboardInterrupt handling
            process = subprocess.Popen(cmd, shell=True)
            try:
                return_code = process.wait()
                return return_code, "", ""
            except KeyboardInterrupt:
                # Properly terminate the subprocess
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
    except KeyboardInterrupt:
        # Forward KeyboardInterrupt to subprocess by killing it
        # This ensures proper cleanup when user presses Ctrl+C
        raise
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
    
    console.print(Panel(f"[bold cyan] Uploading Agent[/bold cyan]\n[yellow]File:[/yellow] {file}\n[yellow]API:[/yellow] {ridges.api_url}", title="🚀 Upload", border_style="cyan"))
    
    try:
        with open(file, 'rb') as f:
            files = {'agent_file': ('agent.py', f, 'text/plain')}
            content_hash = hashlib.sha256(f.read()).hexdigest()
            keypair = ridges.get_keypair()
            public_key = keypair.public_key.hex()
            
            name_and_prev_version = get_name_and_prev_version(ridges.api_url, keypair.ss58_address)
            if name_and_prev_version is None:
                name = Prompt.ask("Enter a name for your miner agent")
                version_num = -1
            else:
                name, prev_version_num = name_and_prev_version
                version_num = prev_version_num + 1

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

def get_name_and_prev_version(url: str, miner_hotkey: str) -> Optional[tuple[str, int]]:
    try:
        with httpx.Client() as client:
            response = client.get(f"{url}/retrieval/latest-agent?miner_hotkey={miner_hotkey}")
            if response.status_code == 404:
                return None
            if response.status_code == 200:
                latest_agent = response.json()
                if latest_agent:
                    return latest_agent.get("agent_name"), latest_agent.get("version_num")
    except Exception as e:
        console.print(f"💥 Error: {e}", style="bold red")
        exit(1)



@cli.group()
def validator():
    """Manage Ridges validators"""
    pass

@validator.command()
@click.option("--no-auto-update", is_flag=True, help="Run validator directly in foreground")
def run(no_auto_update: bool):
    """Run the Ridges validator."""

    if no_auto_update:
        console.print("🚀 Starting validator...", style="yellow")
        if validator_tracing:
            run_cmd("ddtrace-run uv run -m validator.main", capture=False)
        else:
            run_cmd("uv run -m validator.main", capture=False)
        return
    
    # Check if already running
    is_running, _ = check_pm2("ridges-validator-updater")
    if is_running:
        console.print("✅ Auto-updater already running!", style="green")
        console.print(" Showing validator logs...", style="cyan")
        run_cmd("pm2 logs ridges-validator ridges-validator-updater", capture=False)
        return
    
    # Start validator
    console.print("🚀 Starting validator...", style="yellow")
    run_cmd("uv pip install -e .", capture=False)
    run_cmd("pm2 start 'uv run -m validator.main' --name ridges-validator", capture=False)
    
    # Start auto-updater in background
    if run_cmd(f"pm2 start './ridges.py validator update --every 5' --name ridges-validator-updater", capture=False)[0] == 0:
        console.print(Panel(f"[bold green] Auto-updater started![/bold green]\n[cyan]Validator running with auto-updates every 5 minutes.[/cyan]", title="✨ Success", border_style="green"))
        console.print(" Showing validator logs...", style="cyan")
        run_cmd("pm2 logs ridges-validator ridges-validator-updater", capture=False)
    else:
        console.print("💥 Failed to start validator", style="red")

@validator.command()
def stop():
    """Stop the Ridges validator."""
    stopped = [p for p in ["ridges-validator", "ridges-validator-updater"] if run_cmd(f"pm2 delete {p}")[0] == 0]
    if stopped:
        console.print(Panel(f"[bold green] Stopped:[/bold green]\n[cyan]{', '.join(stopped)}[/cyan]", title="✨ Stop Complete", border_style="green"))
    else:
        console.print("  No validator processes running", style="cyan")

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
            console.print("Resetting with `git reset --hard HEAD`")
            if run_cmd("git reset --hard HEAD")[0] != 0:
                console.print("💥 Git reset failed", style="red")
                return
        
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
        run_cmd("pm2 restart ridges-validator" if is_running else "pm2 start 'uv run -m validator.main' --name ridges-validator")
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
    
    if no_auto_update:
        console.print(" Starting platform...", style="yellow")
        run_cmd("uv run -m api.src.main", capture=False)
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
    
    # Start platform
    if run_cmd(f"pm2 start '.venv/bin/ddtrace-run uv run -m api.src.main' --name ridges-api-platform", capture=False)[0] == 0:
        console.print(Panel(f"[bold green] Platform started![/bold green] Running on 0.0.0.0:8000", title="✨ Success", border_style="green"))
        console.print(" Showing platform logs...", style="cyan")
        run_cmd("pm2 logs ridges-api-platform", capture=False)
    else:
        console.print("💥 Failed to start platform", style="red")

@platform.command()
def stop():
    """Stop the Ridges API platform."""
    if run_cmd("pm2 delete ridges-api-platform")[0] == 0:
        console.print(Panel("[bold green] Platform stopped![/bold green]", title="✨ Stop Complete", border_style="green"))
    else:
        console.print("⚠️  Platform not running", style="yellow")

@platform.command()
def logs():
    """Show platform logs."""
    console.print(" Showing platform logs...", style="cyan")
    run_cmd("pm2 logs ridges-api-platform", capture=False)

@platform.command()
def update():
    """Update platform code and restart."""
    # Get current commit and pull updates
    code, current_commit, _ = run_cmd("git rev-parse HEAD")
    if code != 0:
        console.print(" Failed to get commit hash", style="red")
        return
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console, transient=True) as progress:
        progress.add_task("📥 Pulling changes...", total=None)
        if run_cmd("git pull")[0] != 0:
            console.print("💥 Git pull failed", style="red")
            return
    
    # Check if updates applied
    code, new_commit, _ = run_cmd("git rev-parse HEAD")
    if code != 0 or current_commit.strip() == new_commit.strip():
        console.print(Panel("[bold yellow] No updates available[/bold yellow]", title="📋 Status", border_style="yellow"))
        return 0
    
    # Update deps and restart
    console.print(Panel("[bold green]✨ Updates found![/bold green]\n[cyan]Updating deps and restarting...[/cyan]", title="🔄 Update", border_style="green"))
    run_cmd("uv pip install -e .")
    run_cmd("pm2 restart ridges-api-platform")
    console.print(Panel("[bold green]🎉 Platform updated![/bold green]", title="✨ Complete", border_style="green"))

@cli.group()
def proxy():
    """Manage Ridges proxy server"""
    pass

@proxy.command()
@click.option("--no-auto-update", is_flag=True, help="Run proxy directly in foreground")
def run(no_auto_update: bool):
    """Run the Ridges proxy server."""
    
    # Check if running
    is_running, _ = check_pm2("ridges-proxy")
    if is_running:
        console.print(Panel("[bold yellow]⚠️  Proxy already running![/bold yellow]", title="🔄 Status", border_style="yellow"))
        return
    
    if no_auto_update:
        console.print(" Starting proxy server...", style="yellow")
        run_cmd("uv run -m proxy.main", capture=False)
        return

    # Start proxy with PM2
    if run_cmd(f"pm2 start 'uv run -m proxy.main' --name ridges-proxy", capture=False)[0] == 0:
        console.print(Panel(f"[bold green]🎉 Proxy started![/bold green] Running on port 8001", title="✨ Success", border_style="green"))
        console.print(" Showing proxy logs...", style="cyan")
        run_cmd("pm2 logs ridges-proxy", capture=False)
    else:
        console.print(" Failed to start proxy", style="red")

@proxy.command()
def stop():
    """Stop the Ridges proxy server."""
    if run_cmd("pm2 delete ridges-proxy")[0] == 0:
        console.print(Panel("[bold green] Proxy stopped![/bold green]", title="✨ Stop Complete", border_style="green"))
    else:
        console.print("⚠️  Proxy not running", style="yellow")

@proxy.command()
def logs():
    """Show proxy logs."""
    console.print(" Showing proxy logs...", style="cyan")
    run_cmd("pm2 logs ridges-proxy", capture=False)

@cli.group()
def local():
    """Manage all Ridges services locally"""
    pass

@local.command()
def run():
    """Run all Ridges services (platform, proxy, validator) in the background with PM2."""
    console.print(Panel(f"[bold cyan] Starting All Services[/bold cyan]", title=" Local Environment", border_style="cyan"))
    
    services_started = []
    services_already_running = []
    services_failed = []
    
    # Start Platform
    console.print(" Starting platform...", style="yellow")
    is_running, _ = check_pm2("ridges-api-platform")
    if is_running:
        services_already_running.append("Platform")
        console.print("✅ Platform already running!", style="green")
    else:
        # Install dependencies and start platform
        run_cmd("uv pip install -e .", capture=False)
        if run_cmd(f"pm2 start 'uv run -m api.src.main' --name ridges-api-platform", capture=False)[0] == 0:
            services_started.append("Platform")
            console.print("✅ Platform started!", style="green")
        else:
            services_failed.append("Platform")
            console.print(" Failed to start platform", style="red")
    
    # Start Proxy
    console.print("🔗 Starting proxy...", style="yellow")
    is_running, _ = check_pm2("ridges-proxy")
    if is_running:
        services_already_running.append("Proxy")
        console.print("✅ Proxy already running!", style="green")
    else:
        if run_cmd(f"pm2 start 'uv run -m proxy.main' --name ridges-proxy", capture=False)[0] == 0:
            services_started.append("Proxy")
            console.print("✅ Proxy started!", style="green")
        else:
            services_failed.append("Proxy")
            console.print(" Failed to start proxy", style="red")
    
    # Start Validator
    console.print("🔍 Starting validator...", style="yellow")
    is_running, _ = check_pm2("ridges-validator")
    if is_running:
        services_already_running.append("Validator")
        console.print("✅ Validator already running!", style="green")
    else:
        if run_cmd("pm2 start 'uv run -m validator.main' --name ridges-validator", capture=False)[0] == 0:
            services_started.append("Validator")
            console.print("✅ Validator started!", style="green")
        else:
            services_failed.append("Validator")
            console.print(" Failed to start validator", style="red")
    
    # Summary
    console.print("\n" + "="*50)
    if services_started:
        console.print(f"[bold green]🎉 Started:[/bold green] {', '.join(services_started)}")
    if services_already_running:
        console.print(f"[bold yellow]⚠️  Already Running:[/bold yellow] {', '.join(services_already_running)}")
    if services_failed:
        console.print(f"[bold red] Failed:[/bold red] {', '.join(services_failed)}")
    
    # Show final status
    total_services = len(services_started) + len(services_already_running)
    if total_services == 3:
        console.print(Panel(f"[bold green] All services are running![/bold green]\n[cyan]Platform, Proxy, and Validator are active[/cyan]", title="✨ Success", border_style="green"))
        console.print(" Use 'pm2 logs' to view all logs or './ridges.py local logs' for combined logs", style="cyan")
    else:
        console.print(Panel(f"[bold yellow]⚠️  {total_services}/3 services running[/bold yellow]", title="🔄 Status", border_style="yellow"))

@local.command()
def stop():
    """Stop all Ridges services."""
    services = ["ridges-api-platform", "ridges-proxy", "ridges-validator"]
    stopped = []
    
    for service in services:
        if run_cmd(f"pm2 delete {service}")[0] == 0:
            stopped.append(service.replace("ridges-", "").replace("-", " ").title())
    
    if stopped:
        console.print(Panel(f"[bold green]🎉 Stopped:[/bold green]\n[cyan]{', '.join(stopped)}[/cyan]", title="✨ Stop Complete", border_style="green"))
    else:
        console.print(" No services running", style="cyan")

@local.command()
def logs():
    """Show logs for all services."""
    console.print("📋 Showing logs for all services...", style="cyan")
    run_cmd("pm2 logs ridges-api-platform ridges-proxy ridges-validator", capture=False)

@local.command()
def status():
    """Show status of all services."""
    console.print(Panel(f"[bold cyan] Service Status[/bold cyan]", title="🔍 Status Check", border_style="cyan"))
    
    services = [
        ("ridges-api-platform", "Platform"),
        ("ridges-proxy", "Proxy"),
        ("ridges-validator", "Validator")
    ]
    
    for pm2_name, display_name in services:
        is_running, status = check_pm2(pm2_name)
        if is_running:
            console.print(f"✅ {display_name}: [bold green]Running[/bold green]")
        else:
            console.print(f"❌ {display_name}: [bold red]Stopped[/bold red]")

@cli.command()
@click.option("--agent-file", default="miner/agent.py", help="Path to agent file to test")
@click.option("--num-problems", default=3, type=int, help="Number of problems to test")
@click.option("--timeout", default=1200, type=int, help="Timeout per problem in seconds")
@click.option("--problem-set", default="screener", type=click.Choice(['screener', 'easy', 'medium', 'hard']), help="Which problem set to use")
@click.option("--verbose", is_flag=True, help="Show detailed output")
@click.option("--cleanup", is_flag=True, default=True, help="Clean up containers after test")
@click.option("--start-proxy", is_flag=True, default=True, help="Automatically start proxy if needed")
def test_agent(agent_file: str, num_problems: int, timeout: int, problem_set: str, verbose: bool, cleanup: bool, start_proxy: bool):
    """Test your agent locally with full SWE-bench evaluation"""
    
    import tempfile
    import shutil
    import traceback
    from pathlib import Path
    
    # Check and setup .env file for proxy
    proxy_env_path = Path("proxy/.env")
    proxy_env_example_path = Path("proxy/.env.example")
    
    if not proxy_env_path.exists():
        if proxy_env_example_path.exists():
            console.print("📋 No .env file found, copying from .env.example...", style="yellow")
            import shutil
            shutil.copy(proxy_env_example_path, proxy_env_path)
            console.print("✅ Created proxy/.env from proxy/.env.example", style="green")
        else:
            console.print(" No proxy/.env.example file found! This is required for setup.", style="bold red")
            return
    
    # Check for required Chutes API key
    import os
    if os.path.exists("proxy/.env"):
        with open("proxy/.env", "r") as f:
            env_content = f.read()
        
        if "CHUTES_API_KEY=your_chutes_api_key_here" in env_content or "CHUTES_API_KEY=" in env_content and "CHUTES_API_KEY=your_chutes_api_key_here" not in env_content:
            # Check if it's just empty or still has placeholder
            import re
            api_key_match = re.search(r'CHUTES_API_KEY=(.*)$', env_content, re.MULTILINE)
            if not api_key_match or api_key_match.group(1).strip() in ['', 'your_chutes_api_key_here']:
                console.print(" CHUTES_API_KEY is required in proxy/.env", style="bold red")
                console.print("   Please get your API key from https://chutes.ai and update proxy/.env", style="yellow")
                return

    # Load environment variables FIRST before any other imports
    try:
        from validator.local_testing.setup import load_environment
        load_environment()
    except ImportError as e:
        console.print(f" Failed to load environment setup: {e}", style="bold red")
        return
    
    console.print(Panel(f"[bold cyan]🧪 Testing Agent Locally[/bold cyan]\n"
                        f"[yellow]Agent:[/yellow] {agent_file}\n"
                        f"[yellow]Problems:[/yellow] {num_problems} from {problem_set} set\n"
                        f"[yellow]Timeout:[/yellow] {timeout}s per problem", 
                        title=" Local Test", border_style="cyan"))
    
    # Validate agent file exists
    if not Path(agent_file).exists():
        console.print(f" Agent file not found: {agent_file}", style="bold red")
        return
    
    # Check if proxy is needed and start if required
    proxy_process = None
    if start_proxy:
        try:
            # Check if proxy is already running
            import requests
            proxy_url = os.getenv("RIDGES_PROXY_URL", "http://10.0.0.9:8001")
            try:
                response = requests.get(f"{proxy_url}/health", timeout=5)
                if response.status_code == 200:
                    console.print(f"✅ Proxy already running at {proxy_url}", style="green")
                else:
                    raise Exception("Proxy not responding")
            except:
                console.print("🚀 Starting proxy...", style="yellow")
                import os
                proxy_process = subprocess.Popen(
                    ["python", "ridges.py", "proxy", "run", "--no-auto-update"],
                    stdout=subprocess.DEVNULL if not verbose else None,
                    stderr=subprocess.DEVNULL if not verbose else None,
                    preexec_fn=os.setsid  # Create new process group for proper cleanup
                )
                # Give proxy time to start
                import time
                time.sleep(5)
                console.print("✅ Proxy started", style="green")
        except Exception as e:
            console.print(f"⚠️  Could not start proxy: {e}", style="yellow")
            console.print("You may need to run: ./ridges.py proxy run --no-auto-update", style="yellow")
    
    try:
        # Import our new local testing components (after environment is loaded)
        from validator.local_testing.setup import setup_local_testing_environment
        from validator.local_testing.local_manager import LocalSandboxManager
        from validator.local_testing.runner import run_local_evaluations
        
        # One-time setup (pulls images, etc.)
        console.print("🔧 Setting up local testing environment...", style="yellow")
        setup_local_testing_environment()
        
        # Create local sandbox manager
        local_manager = LocalSandboxManager(verbose=verbose)
        
        # Run evaluations
        import asyncio
        results = asyncio.run(run_local_evaluations(
            agent_file=agent_file,
            num_problems=num_problems,
            timeout=timeout,
            problem_set=problem_set,
            manager=local_manager
        ))
        
        # Display results
        display_test_results(results)
        
    except KeyboardInterrupt:
        console.print("\n🛑 Test interrupted by user", style="yellow")
    except Exception as e:
        console.print(f" Test failed: {e}", style="bold red")
        if verbose:
            console.print(traceback.format_exc(), style="dim")
    finally:
        # Cleanup
        if cleanup:
            console.print("🧹 Cleaning up...", style="dim")
            try:
                if 'local_manager' in locals():
                    local_manager.cleanup()
            except:
                pass
        
        # Stop proxy if we started it
        if proxy_process:
            try:
                # Kill the entire process group to ensure child processes are also terminated
                import os
                import signal
                if hasattr(proxy_process, 'pid') and proxy_process.pid:
                    try:
                        # Send SIGTERM to the entire process group
                        os.killpg(os.getpgid(proxy_process.pid), signal.SIGTERM)
                        proxy_process.wait(timeout=5)
                        console.print("🛑 Proxy stopped", style="dim")
                    except (ProcessLookupError, OSError):
                        # Process already terminated
                        console.print("🛑 Proxy stopped", style="dim")
                    except subprocess.TimeoutExpired:
                        # Force kill the process group
                        os.killpg(os.getpgid(proxy_process.pid), signal.SIGKILL)
                        console.print("🛑 Proxy force stopped", style="dim")
                else:
                    proxy_process.terminate()
                    proxy_process.wait(timeout=5)
                    console.print("🛑 Proxy stopped", style="dim")
            except Exception as e:
                try:
                    proxy_process.kill()
                    console.print("🛑 Proxy force stopped", style="dim")
                except:
                    console.print("⚠️  Could not stop proxy process", style="yellow")

def display_test_results(results: dict):
    """Display test results in a nice format"""
    
    summary = results['summary']
    individual_results = results['results']
    
    # Summary panel
    solved_count = summary['solved_count']
    total_count = summary['total_count']
    success_rate = summary['success_rate']
    avg_time = summary['avg_time']
    
    console.print(Panel(
        f"[bold green]✅ Solved:[/bold green] {solved_count}/{total_count} ({success_rate:.1f}%)\n"
        f"[yellow] Average time:[/yellow] {avg_time:.1f}s\n"
        f"[cyan]🔧 Patches generated:[/cyan] {summary['patches_generated']}/{total_count}",
        title="Test Summary", border_style="green" if success_rate > 0 else "red"
    ))
    
    # Individual results
    console.print("\n[bold cyan]Individual Results:[/bold cyan]")
    for i, result in enumerate(individual_results):
        status_icon = "✅" if result['solved'] else "❌" if result['error'] else "⚠️"
        console.print(f"{status_icon} {result['instance_id']}: {result['status']} ({result['duration']:.1f}s)")
        
        if result.get('error'):
            console.print(f"   [red]Error:[/red] {result['error'][:100]}...")
        
        # Display patch content if generated
        if result.get('patch_content'):
            console.print(f"   [green] Generated Patch:[/green]")
            # Display patch in a code block style
            patch_lines = result['patch_content'].split('\n')
            for line in patch_lines:  # Show all lines
                console.print(f"   [dim]{line}[/dim]")
            console.print("")  # Add spacing

if __name__ == "__main__":
    run_cmd(". .venv/bin/activate")
    cli() 