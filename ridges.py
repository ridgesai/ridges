#!.venv/bin/python3

"""
Ridges CLI - Command-line interface for managing Ridges miners
"""

import hashlib
import click
from fiber.chain.chain_utils import load_hotkey_keypair
import httpx
import subprocess
import os
import sys
import time
from typing import Optional
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.align import Align
from rich.live import Live
from rich.layout import Layout
from rich import box

# Initialize Rich console
console = Console()

# Configuration
API_BASE_URL = "http://54.226.52.51:8000"  # Update this with the actual API URL
CONFIG_FILE = "miner/.env"

# Load environment variables from CONFIG_FILE
load_dotenv(CONFIG_FILE)

def print_success(message: str):
    """Print a success message."""
    console.print(f"✨ {message}", style="bold green")

def print_error(message: str):
    """Print an error message."""
    console.print(f"💥 {message}", style="bold red")

def print_info(message: str):
    """Print an info message."""
    console.print(f"ℹ️  {message}", style="bold blue")

def print_warning(message: str):
    """Print a warning message."""
    console.print(f"⚠️  {message}", style="bold yellow")

def animate_loading(message: str, duration: float = 2.0):
    """Show a loading animation."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task(message, total=None)
        time.sleep(duration)

class RidgesConfig:
    """Configuration management for Ridges CLI."""
    
    def __init__(self):
        self.config_file = CONFIG_FILE
        self.load_config()
    
    def load_config(self):
        """Load configuration from .env file."""
        if os.path.exists(self.config_file):
            load_dotenv(self.config_file)
    
    def save_config(self, key: str, value: str):
        """Save a configuration value to .env file."""
        # Read existing config
        config_lines = []
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                config_lines = f.readlines()
        
        # Update or add the key-value pair
        key_found = False
        for i, line in enumerate(config_lines):
            if line.strip().startswith(f"{key}="):
                config_lines[i] = f"{key}={value}\n"
                key_found = True
                break
        
        if not key_found:
            config_lines.append(f"{key}={value}\n")
        
        # Write back to file
        with open(self.config_file, 'w') as f:
            f.writelines(config_lines)
        
        # Update environment variable
        os.environ[key] = value
    
    def get_or_prompt(self, key: str, prompt_text: str, default: Optional[str] = None) -> str:
        """Get a configuration value or prompt for it if not found."""
        value = os.getenv(key)
        
        if not value:
            if default:
                value = Prompt.ask(f"🎯 {prompt_text}", default=default)
            else:
                value = Prompt.ask(f"🎯 {prompt_text}")
            
            # Save to config file
            self.save_config(key, value)
            print_success(f"Configuration saved: {key} → {self.config_file}")
        
        return value

class RidgesCLI:
    def __init__(self):
        self.api_url = API_BASE_URL
        self.config = RidgesConfig()
        
    def get_coldkey_name(self) -> str:
        """Get coldkey name from environment, config, or prompt user."""
        return self.config.get_or_prompt(
            "RIDGES_COLDKEY_NAME",
            "Enter your coldkey name",
            default="miner"
        )
    
    def get_hotkey_name(self) -> str:
        """Get hotkey name from environment, config, or prompt user."""
        return self.config.get_or_prompt(
            "RIDGES_HOTKEY_NAME",
            "Enter your hotkey name",
            default="default"
        )
    
    def get_keypair(self):
        """Get the keypair for signing operations."""
        coldkey_name = self.get_coldkey_name()
        hotkey_name = self.get_hotkey_name()
        return load_hotkey_keypair(coldkey_name, hotkey_name)
    
    def get_agent_file_path(self) -> str:
        """Get agent file path from environment, config, or prompt user."""
        return self.config.get_or_prompt(
            "RIDGES_AGENT_FILE",
            "Enter the path to your agent.py file",
            default="miner/agent.py"
        )
    
    def get_agent_name(self) -> str:
        """Get agent name from environment, config, or prompt user."""
        return self.config.get_or_prompt(
            "RIDGES_AGENT_NAME",
            "Enter a name for your miner agent"
        )

def create_status_table(agent_data: dict, evaluations: list = None) -> Table:
    """Create a status table."""
    table = Table(
        title="🎯 Agent Status Dashboard",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta"
    )
    
    table.add_column("Property", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")
    
    table.add_row("Agent ID", str(agent_data.get('agent_id', 'N/A')))
    table.add_row("Latest Version", str(agent_data.get('latest_version', 'N/A')))
    table.add_row("Created", str(agent_data.get('created_at', 'N/A')))
    table.add_row("Last Updated", str(agent_data.get('last_updated', 'N/A')))
    
    if evaluations:
        table.add_row("Recent Evaluations", f"{len(evaluations)} found")
    
    return table

def create_evaluations_table(evaluations: list) -> Table:
    """Create an evaluations table."""
    table = Table(
        title="📊 Recent Evaluations",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta"
    )
    
    table.add_column("Status", style="cyan", no_wrap=True)
    table.add_column("Created", style="green")
    table.add_column("Details", style="yellow")
    
    status_emojis = {
        'waiting': '⏳',
        'running': '🔄',
        'completed': '✅',
        'error': '❌',
        'timedout': '⏰',
        'disconnected': '🔌'
    }
    
    for eval_data in evaluations[:5]:  # Show last 5
        status = eval_data.get('status', 'unknown')
        emoji = status_emojis.get(status, '❓')
        table.add_row(
            f"{emoji} {status.title()}",
            str(eval_data.get('created_at', 'N/A')),
            str(eval_data.get('details', 'N/A'))
        )
    
    return table

@click.group()
@click.version_option(version="1.0.0")
def cli():
    """
    Ridges CLI - Manage your Ridges miners
    
    Provides commands to upload miners and check their status.
    """
    pass

@cli.command()
@click.option("--file", help="Path to agent.py file (can also be set via RIDGES_AGENT_FILE env var)")
@click.option("--coldkey-name", help="Coldkey name (can also be set via RIDGES_COLDKEY_NAME env var)")
@click.option("--hotkey-name", help="Hotkey name (can also be set via RIDGES_HOTKEY_NAME env var)")
@click.option("--name", help="Name for the miner agent")
def upload(hotkey_name: Optional[str], file: Optional[str], coldkey_name: Optional[str], name: Optional[str]):
    """Upload a miner agent to the Ridges API."""
    ridges = RidgesCLI()
    
    # Get configuration values, prompting if needed
    if not file:
        file = ridges.get_agent_file_path()
    
    if not coldkey_name:
        coldkey_name = ridges.get_coldkey_name()
    
    if not hotkey_name:
        hotkey_name = ridges.get_hotkey_name()
    
    if not name:
        name = ridges.get_agent_name()
    
    # Check if file exists
    if not os.path.exists(file):
        print_error(f"File not found: {file}")
        sys.exit(1)
    
    # Check if file is named agent.py
    if os.path.basename(file) != "agent.py":
        print_error("File must be named 'agent.py'")
        sys.exit(1)
    
    # Get the actual hotkey address for the request
    console.print(Panel(
        f"[bold cyan]📤 Uploading Agent[/bold cyan]\n"
        f"[yellow]File:[/yellow] {file}\n"
        f"[yellow]Name:[/yellow] {name}\n"
        f"[yellow]Coldkey:[/yellow] {coldkey_name}\n"
        f"[yellow]Hotkey:[/yellow] {hotkey_name}",
        title="🚀 Upload Configuration",
        border_style="cyan"
    ))
    
    try:
        with open(file, 'rb') as f:
            files = {'agent_file': ('agent.py', f, 'text/plain')}
            content_hash = hashlib.sha256(f.read()).hexdigest()
            keypair = ridges.get_keypair()
            public_key = keypair.public_key.hex()
            file_info = f"{keypair.ss58_address}:{content_hash}"
            signature = keypair.sign(file_info).hex()
            payload = { 
                'miner_hotkey': keypair.ss58_address, 
                'public_key': public_key, 
                'file_info': file_info, 
                'signature': signature,
                'name': name
            }

            animate_loading("🔐 Signing and preparing upload...")
            
            with httpx.Client() as client:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                    transient=True
                ) as progress:
                    task = progress.add_task("📡 Uploading to Ridges API...", total=None)
                    response = client.post(f"{ridges.api_url}/upload/agent", files=files, data=payload, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    print_success(result.get('message', 'Upload successful'))
                    
                    # Show success panel
                    console.print(Panel(
                        f"[bold green]🎉 Upload Complete[/bold green]\n"
                        f"[cyan]Your miner '{name}' has been uploaded to the Ridges network![/cyan]\n"
                        f"[cyan]It has been queued for evaluation.[/cyan]",
                        title="✨ Success",
                        border_style="green"
                    ))
                else:
                    error_detail = response.json().get('detail', 'Unknown error') if response.headers.get('content-type', '').startswith('application/json') else response.text
                    print_error(f"Upload failed: {error_detail}")
                    sys.exit(1)
                    
    except httpx.RequestError as e:
        print_error(f"Network error: {e}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        sys.exit(1)

@cli.command()
@click.option("--hotkey-name", help="Hotkey name (can also be set via RIDGES_HOTKEY_NAME env var)")
def status(hotkey_name: Optional[str]):
    """Check the status of your miner."""
    ridges = RidgesCLI()
    
    if not hotkey_name:
        hotkey_name = ridges.get_hotkey_name()
    
    # Get the actual hotkey address for the API request
    actual_hotkey = ridges.get_keypair().ss58_address
    
    console.print(Panel(
        f"[bold cyan]🔍 Checking Status[/bold cyan]\n"
        f"[yellow]Hotkey Name:[/yellow] {hotkey_name}\n"
        f"[yellow]Address:[/yellow] {actual_hotkey}",
        title="📊 Status Check",
        border_style="cyan"
    ))
    
    try:
        with httpx.Client() as client:
            # First, try to get agent info
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("🔍 Fetching agent information...", total=None)
                response = client.get(
                    f"{ridges.api_url}/agent/{actual_hotkey}",
                    timeout=10.0
                )
            
            if response.status_code == 200:
                agent_data = response.json()
                
                # Get evaluations if available
                evaluations_response = client.get(
                    f"{ridges.api_url}/agent/{actual_hotkey}/evaluations",
                    timeout=10.0
                )
                
                evaluations = []
                if evaluations_response.status_code == 200:
                    evaluations = evaluations_response.json()
                
                # Display status table
                status_table = create_status_table(agent_data, evaluations)
                console.print(status_table)
                
                # Display evaluations if available
                if evaluations:
                    evaluations_table = create_evaluations_table(evaluations)
                    console.print(evaluations_table)
                else:
                    console.print(Panel(
                        "[yellow]No evaluations found yet[/yellow]",
                        title="📊 Evaluations",
                        border_style="yellow"
                    ))
                    
            elif response.status_code == 404:
                console.print(Panel(
                    f"[red]❌ No agent found for hotkey: {actual_hotkey}[/red]\n"
                    f"[yellow]Try uploading your agent first with:[/yellow] [cyan]ridges upload[/cyan]",
                    title="🔍 Agent Not Found",
                    border_style="red"
                ))
            else:
                print_error(f"Error checking status: {response.status_code}")
                sys.exit(1)
                
    except httpx.RequestError as e:
        print_error(f"Network error: {e}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        sys.exit(1)

@cli.command()
def config():
    """Show current configuration."""
    ridges = RidgesCLI()
    
    config_table = Table(
        title="⚙️ Ridges CLI Configuration",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta"
    )
    
    config_table.add_column("Setting", style="cyan", no_wrap=True)
    config_table.add_column("Value", style="green")
    
    config_table.add_row("Agent File", ridges.get_agent_file_path())
    config_table.add_row("Agent Name", ridges.get_agent_name())
    config_table.add_row("Coldkey Name", ridges.get_coldkey_name())
    config_table.add_row("Hotkey Name", ridges.get_hotkey_name())
    config_table.add_row("Hotkey Address", ridges.get_keypair().ss58_address)
    config_table.add_row("Config File", ridges.config.config_file)
    
    console.print(config_table)

@cli.command()
def version():
    """Show version information."""
    console.print(Panel(
        "[bold yellow]🌟 Ridges CLI v1.0.0[/bold yellow]\n"
        "[cyan]Command-line interface for managing Ridges miners and validators[/cyan]\n\n"
        "[green]Features:[/green]\n"
        "• 🚀 Upload miners to the network\n"
        "• 🔍 Check miner status and evaluations\n"
        "• ⚙️ Manage configuration settings\n",
        title="📋 Version Information",
        border_style="yellow"
    ))

if __name__ == "__main__":
    cli() 