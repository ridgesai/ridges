#!.venv/bin/python3
"""
Ridges CLI - A command-line interface for managing Ridges miners and validators.
"""

import hashlib
import click
from fiber.chain.chain_utils import load_hotkey_keypair
import httpx
import subprocess
import os
import sys
from typing import Optional
from dotenv import load_dotenv

# Configuration
API_BASE_URL = "http://localhost:8000"  # Update this with the actual API URL
CONFIG_FILE = "miner/.env"

# Load environment variables from CONFIG_FILE
load_dotenv(CONFIG_FILE)

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
                value = click.prompt(prompt_text, default=default, type=str)
            else:
                value = click.prompt(prompt_text, type=str)
            
            # Save to config file
            self.save_config(key, value)
            click.echo(f"💾 Saved {key} to {self.config_file}")
        
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
        """Get the keypair for signing operations, prompting for coldkey and hotkey names if needed."""
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
    
    def check_pm2_installed(self):
        """Check if PM2 is installed."""
        try:
            subprocess.run(["pm2", "--version"], check=True, capture_output=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    def run_pm2_command(self, command: str, capture_output: bool = True):
        """Run a PM2 command."""
        if not self.check_pm2_installed():
            click.echo("❌ PM2 is not installed. Please install it first: npm install -g pm2", err=True)
            sys.exit(1)
        
        try:
            result = subprocess.run(
                ["pm2"] + command.split(),
                check=True,
                capture_output=capture_output,
                text=True
            )
            return result
        except subprocess.CalledProcessError as e:
            click.echo(f"❌ PM2 command failed: {e}", err=True)
            return None

@click.group()
@click.version_option(version="1.0.0")
def cli():
    """
    Ridges CLI - Manage your Ridges miners and validators.
    
    This CLI provides commands to upload miners, check their status,
    and manage validator processes with PM2.
    """
    pass

@cli.command()
@click.option("--file", help="Path to agent.py file (can also be set via RIDGES_AGENT_FILE env var)")
@click.option("--coldkey-name", help="Coldkey name (can also be set via RIDGES_COLDKEY_NAME env var)")
@click.option("--hotkey-name", help="Hotkey name (can also be set via RIDGES_HOTKEY_NAME env var)")
def upload(hotkey_name: Optional[str], file: Optional[str], coldkey_name: Optional[str]):
    """Upload a miner agent to the Ridges API."""
    ridges = RidgesCLI()
    
    # Get configuration values, prompting if needed
    if not file:
        file = ridges.get_agent_file_path()
    
    if not coldkey_name:
        coldkey_name = ridges.get_coldkey_name()
    
    if not hotkey_name:
        hotkey_name = ridges.get_hotkey_name()
    
    # Check if file exists
    if not os.path.exists(file):
        click.echo(f"❌ File not found: {file}", err=True)
        sys.exit(1)
    
    # Check if file is named agent.py
    if os.path.basename(file) != "agent.py":
        click.echo("❌ File must be named 'agent.py'", err=True)
        sys.exit(1)
    
    # Get the actual hotkey address for the request
    click.echo(f"📤 Uploading {file} with coldkey name: {coldkey_name}, hotkey name: {hotkey_name}")
    
    try:
        with open(file, 'rb') as f:
            files = {'agent_file': ('agent.py', f, 'text/plain')}
            content_hash = hashlib.sha256(f.read()).hexdigest()
            keypair = ridges.get_keypair()
            public_key = keypair.public_key.hex()
            file_info = f"{keypair.ss58_address}:{content_hash}"
            signature = keypair.sign(file_info).hex()
            payload = { 'miner_hotkey': keypair.ss58_address, 'public_key': public_key, 'file_info': file_info, 'signature': signature }

            
            with httpx.Client() as client:
                response = client.post(f"{ridges.api_url}/upload/agent", files=files, data=payload, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    click.echo(f"✅ {result.get('message', 'Upload successful')}")
                else:
                    error_detail = response.json().get('detail', 'Unknown error') if response.headers.get('content-type', '').startswith('application/json') else response.text
                    click.echo(f"❌ Upload failed: {error_detail}", err=True)
                    sys.exit(1)
                    
    except httpx.RequestError as e:
        click.echo(f"❌ Network error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Unexpected error: {e}", err=True)
        sys.exit(1)

@cli.command()
@click.option("--hotkey-name", help="Hotkey name (can also be set via RIDGES_HOTKEY_NAME env var)")
def status(hotkey_name: Optional[str]):
    """Check the status of your miner on the Ridges API."""
    ridges = RidgesCLI()
    
    if not hotkey_name:
        hotkey_name = ridges.get_hotkey_name()
    
    # Get the actual hotkey address for the API request
    actual_hotkey = ridges.get_keypair().ss58_address
    click.echo(f"🔍 Checking status for hotkey name: {hotkey_name} (address: {actual_hotkey})")
    
    try:
        with httpx.Client() as client:
            # First, try to get agent info
            response = client.get(
                f"{ridges.api_url}/agent/{actual_hotkey}",
                timeout=10.0
            )
            
            if response.status_code == 200:
                agent_data = response.json()
                click.echo(f"✅ Agent found!")
                click.echo(f"   Agent ID: {agent_data.get('agent_id', 'N/A')}")
                click.echo(f"   Latest Version: {agent_data.get('latest_version', 'N/A')}")
                click.echo(f"   Created: {agent_data.get('created_at', 'N/A')}")
                click.echo(f"   Last Updated: {agent_data.get('last_updated', 'N/A')}")
                
                # Get evaluations if available
                evaluations_response = client.get(
                    f"{ridges.api_url}/agent/{actual_hotkey}/evaluations",
                    timeout=10.0
                )
                
                if evaluations_response.status_code == 200:
                    evaluations = evaluations_response.json()
                    if evaluations:
                        click.echo(f"   Recent Evaluations:")
                        for eval_data in evaluations[:5]:  # Show last 5
                            status = eval_data.get('status', 'unknown')
                            status_emoji = {
                                'waiting': '⏳',
                                'running': '🔄',
                                'completed': '✅',
                                'error': '❌',
                                'timedout': '⏰',
                                'disconnected': '🔌'
                            }.get(status, '❓')
                            click.echo(f"     {status_emoji} {status} - {eval_data.get('created_at', 'N/A')}")
                    else:
                        click.echo("   No evaluations found")
                        
            elif response.status_code == 404:
                click.echo(f"❌ No agent found for hotkey: {actual_hotkey}")
            else:
                click.echo(f"❌ Error checking status: {response.status_code}", err=True)
                sys.exit(1)
                
    except httpx.RequestError as e:
        click.echo(f"❌ Network error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Unexpected error: {e}", err=True)
        sys.exit(1)

@cli.group()
def validator():
    """Manage validator processes with PM2."""
    pass

@validator.command()
@click.option("--name", default="ridges-validator", help="PM2 process name (default: ridges-validator)")
@click.option("--skip-confirm", is_flag=True, help="Skip environment confirmation")
def start(name: str, skip_confirm: bool):
    """Start the validator with PM2."""
    ridges = RidgesCLI()
    
    click.echo(f"🚀 Starting validator with PM2 process name: {name}")
    
    # Check if already running
    result = ridges.run_pm2_command(f"list | grep {name}")
    if result and name in result.stdout:
        click.echo(f"✅ Validator '{name}' is already running")
        return
    
    # Check if validator/.env exists
    if not os.path.exists("validator/.env"):
        click.echo("❌ validator/.env does not exist", err=True)
        if os.path.exists("validator/.env.example"):
            if click.confirm("Would you like to copy validator/.env.example to validator/.env?"):
                subprocess.run(["cp", "validator/.env.example", "validator/.env"])
                click.echo("✅ Copied validator/.env.example to validator/.env")
            else:
                sys.exit(1)
        else:
            click.echo("Please create validator/.env file", err=True)
            sys.exit(1)
    
    # Confirm environment variables if not skipped
    if not skip_confirm:
        click.echo("Please confirm the following values in validator/.env:")
        try:
            with open("validator/.env", "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        key, value = line.split("=", 1)
                        click.echo(f"  {key}: {value}")
        except Exception as e:
            click.echo(f"❌ Error reading validator/.env: {e}", err=True)
            sys.exit(1)
        
        if not click.confirm("Are these values correct?"):
            click.echo("Please update validator/.env and run this command again")
            sys.exit(1)
    
    # Install dependencies
    click.echo("📦 Installing dependencies...")
    try:
        subprocess.run(["uv", "pip", "install", "-e", "."], check=True)
    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Failed to install dependencies: {e}", err=True)
        sys.exit(1)
    
    # Start validator with PM2
    click.echo("🔄 Starting validator...")
    result = ridges.run_pm2_command(f"start 'uv run --no-sync validator/main.py' --name {name}")
    
    if result:
        click.echo(f"✅ Validator '{name}' started successfully")
        click.echo("💡 Use 'ridges validator status' to check the process")
    else:
        click.echo("❌ Failed to start validator", err=True)
        sys.exit(1)

@validator.command()
@click.option("--name", default="ridges-validator", help="PM2 process name (default: ridges-validator)")
def stop(name: str):
    """Stop the validator PM2 process."""
    ridges = RidgesCLI()
    
    click.echo(f"🛑 Stopping validator '{name}'...")
    
    result = ridges.run_pm2_command(f"stop {name}")
    if result:
        click.echo(f"✅ Validator '{name}' stopped successfully")
    else:
        click.echo(f"❌ Failed to stop validator '{name}'", err=True)
        sys.exit(1)

@validator.command()
@click.option("--name", default="ridges-validator", help="PM2 process name (default: ridges-validator)")
def restart(name: str):
    """Restart the validator PM2 process."""
    ridges = RidgesCLI()
    
    click.echo(f"🔄 Restarting validator '{name}'...")
    
    result = ridges.run_pm2_command(f"restart {name}")
    if result:
        click.echo(f"✅ Validator '{name}' restarted successfully")
    else:
        click.echo(f"❌ Failed to restart validator '{name}'", err=True)
        sys.exit(1)

@validator.command()
@click.option("--name", default="ridges-validator", help="PM2 process name (default: ridges-validator)")
def status(name: str):
    """Check the status of the validator PM2 process."""
    ridges = RidgesCLI()
    
    click.echo(f"📊 Status for validator '{name}':")
    
    result = ridges.run_pm2_command(f"list | grep {name}")
    if result and name in result.stdout:
        click.echo("✅ Validator is running")
        # Get detailed status
        detailed_result = ridges.run_pm2_command(f"show {name}")
        if detailed_result:
            lines = detailed_result.stdout.split('\n')
            for line in lines:
                if any(keyword in line.lower() for keyword in ['status', 'uptime', 'memory', 'cpu']):
                    click.echo(f"   {line.strip()}")
    else:
        click.echo("❌ Validator is not running")

@validator.command()
@click.option("--name", default="ridges-validator", help="PM2 process name (default: ridges-validator)")
def logs(name: str):
    """Show logs for the validator PM2 process."""
    ridges = RidgesCLI()
    
    click.echo(f"📋 Logs for validator '{name}':")
    
    result = ridges.run_pm2_command(f"logs {name} --lines 50", capture_output=False)
    if not result:
        click.echo(f"❌ Failed to get logs for '{name}'", err=True)
        sys.exit(1)

@validator.command()
@click.option("--name", default="ridges-validator", help="PM2 process name (default: ridges-validator)")
def auto_update(name: str):
    """Start auto-update process for the validator."""
    ridges = RidgesCLI()
    
    click.echo(f"🔄 Starting auto-update process for validator '{name}'...")
    
    # Check if auto-update script exists
    if not os.path.exists("validator_auto_update.sh"):
        click.echo("❌ validator_auto_update.sh not found", err=True)
        sys.exit(1)
    
    # Make script executable
    os.chmod("validator_auto_update.sh", 0o755)
    
    # Start auto-update process with PM2
    result = ridges.run_pm2_command(f"start validator_auto_update.sh --name {name}-auto-update --interpreter bash")
    
    if result:
        click.echo(f"✅ Auto-update process started successfully")
        click.echo("💡 This process will automatically update the validator when new code is pushed to git")
    else:
        click.echo("❌ Failed to start auto-update process", err=True)
        sys.exit(1)

@cli.command()
def config():
    """Show current configuration."""
    ridges = RidgesCLI()
    
    click.echo("📋 Current Ridges CLI Configuration:")
    click.echo(f"   Agent File: {ridges.get_agent_file_path()}")
    click.echo(f"   Coldkey Name: {ridges.get_coldkey_name()}")
    click.echo(f"   Hotkey Name: {ridges.get_hotkey_name()}")
    click.echo(f"   Hotkey Address: {ridges.get_keypair().ss58_address}")
    click.echo(f"   Config File: {ridges.config.config_file}")

@cli.command()
def version():
    """Show version information."""
    click.echo("Ridges CLI v1.0.0")
    click.echo("A command-line interface for managing Ridges miners and validators")

if __name__ == "__main__":
    cli() 