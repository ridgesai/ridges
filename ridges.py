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

console = Console()
DEFAULT_API_BASE_URL = "https://platform-v2.ridges.ai"
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
def upload(ctx, file: Optional[str], coldkey_name: Optional[str], hotkey_name: Optional[str]):
    """Upload a miner agent to the Ridges API."""
    ridges = RidgesCLI(ctx.obj.get('url'))
    
    coldkey = coldkey_name or ridges.config.get_or_prompt("RIDGES_COLDKEY_NAME", "Enter your coldkey name", "miner")
    hotkey = hotkey_name or ridges.config.get_or_prompt("RIDGES_HOTKEY_NAME", "Enter your hotkey name", "default")
    keypair = load_hotkey_keypair(coldkey, hotkey)
    
    file = file or ridges.config.get_or_prompt("RIDGES_AGENT_FILE", "Enter the path to your agent.py file", "agent.py")
    if not os.path.exists(file) or os.path.basename(file) != "agent.py":
        console.print("File must be named 'agent.py' and exist", style="bold red")
        return
    
    console.print(Panel(f"[bold cyan]Uploading Agent[/bold cyan]\n[yellow]Hotkey:[/yellow] {keypair.ss58_address}\n[yellow]File:[/yellow] {file}\n[yellow]API:[/yellow] {ridges.api_url}", title="Upload", border_style="cyan"))
    
    try:
        with open(file, 'rb') as f:
            file_content = f.read()
        
        content_hash = hashlib.sha256(file_content).hexdigest()
        public_key = keypair.public_key.hex()
        
        with httpx.Client() as client:
            response = client.get(f"{ridges.api_url}/retrieval/agent-by-hotkey?miner_hotkey={keypair.ss58_address}")
            
            if response.status_code == 200 and response.json():
                latest_agent = response.json()
                name = latest_agent.get("name")
                version_num = latest_agent.get("version_num", -1) + 1
            else:
                name = Prompt.ask("Enter a name for your miner agent")
                version_num = 0

            file_info = f"{keypair.ss58_address}:{content_hash}:{version_num}"
            signature = keypair.sign(file_info).hex()
            payload = {'public_key': public_key, 'file_info': file_info, 'signature': signature, 'name': name}
            files = {'agent_file': ('agent.py', file_content, 'text/plain')}

            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console, transient=True) as progress:
                progress.add_task("Signing and uploading...", total=None)
                response = client.post(f"{ridges.api_url}/upload/agent", files=files, data=payload, timeout=120)
            
            if response.status_code == 200:
                console.print(Panel(f"[bold green]Upload Complete[/bold green]\n[cyan]Miner '{name}' uploaded successfully![/cyan]", title="Success", border_style="green"))
            else:
                error = response.json().get('detail', 'Unknown error') if response.headers.get('content-type', '').startswith('application/json') else response.text
                console.print(f"Upload failed: {error}", style="bold red")
                    
    except Exception as e:
        console.print(f"Error: {e}", style="bold red")


if __name__ == "__main__":
    run_cmd(". .venv/bin/activate")
    cli() 