"""
Setup utilities for local testing environment.

This module handles:
- Docker image pulling
- Environment validation
- One-time setup operations
"""

import subprocess
import os
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console

console = Console()

# Load environment variables FIRST before any other imports
def load_environment():
    """Load environment variables early to prevent config validation errors"""
    validator_env = Path("validator/.env")
    if validator_env.exists():
        load_dotenv(validator_env)
        console.print("✅ Loaded configuration from validator/.env", style="green")
        return True
    else:
        console.print("⚠️  No validator/.env found, using defaults", style="yellow")
        return False

def setup_local_testing_environment():
    """One-time setup for local testing environment"""
    
    # Load environment first
    load_environment()
    
    # Check Docker is available and running
    try:
        import docker
    except ImportError:
        console.print("❌ Docker Python library not found", style="bold red")
        console.print("Install with: pip install docker", style="yellow")
        raise SystemExit("Docker Python library is required for local testing")
    
    try:
        client = docker.from_env()
        client.ping()
        console.print("✅ Docker is running", style="green")
    except docker.errors.DockerException as e:
        console.print("❌ Docker is not running or accessible", style="bold red")
        console.print("Please ensure Docker is:", style="yellow")
        console.print("  • Installed (https://docs.docker.com/get-docker/)", style="yellow")
        console.print("  • Running (start Docker Desktop or docker daemon)", style="yellow")
        console.print("  • Accessible by your user (check permissions)", style="yellow")
        console.print(f"Error details: {e}", style="dim")
        raise SystemExit("Docker is required for local testing")
    except Exception as e:
        console.print("❌ Docker connection failed", style="bold red")
        console.print(f"Error: {e}", style="red")
        console.print("Please check your Docker installation and permissions", style="yellow")
        raise SystemExit("Docker is required for local testing")
    
    # Pull required images
    images_to_pull = [
        "ghcr.io/ridgesai/ridges/sandbox:latest",
        "ghcr.io/ridgesai/ridges/proxy:latest"
    ]
    
    for image in images_to_pull:
        try:
            client.images.get(image)
            console.print(f"✅ Image {image} already exists", style="green")
        except docker.errors.ImageNotFound:
            console.print(f"📥 Pulling {image}...", style="yellow")
            try:
                client.images.pull(image)
                console.print(f"✅ Successfully pulled {image}", style="green")
            except docker.errors.APIError as e:
                console.print(f"❌ Failed to pull {image}", style="red")
                console.print("This might be due to:", style="yellow")
                console.print("  • Network connectivity issues", style="yellow")
                console.print("  • Docker registry authentication", style="yellow")
                console.print("  • Insufficient disk space", style="yellow")
                console.print(f"Error details: {e}", style="dim")
                raise SystemExit(f"Failed to pull required Docker image: {image}")
            except Exception as e:
                console.print(f"❌ Failed to pull {image}: {e}", style="red")
                raise SystemExit(f"Failed to pull required Docker image: {image}")
    
    # Check if swebench is available
    try:
        import swebench
        console.print("✅ SWE-bench available", style="green")
    except ImportError:
        console.print("❌ SWE-bench not found. Install with: pip install swebench", style="red")
        raise SystemExit("SWE-bench is required for local testing")
    
    # Show configuration being used
    api_url = os.getenv("RIDGES_API_URL", "http://localhost:8000")
    proxy_url = os.getenv("RIDGES_PROXY_URL", "http://localhost:8001")
    console.print(f"📡 Using API URL: {api_url}", style="cyan")
    console.print(f"🔗 Using Proxy URL: {proxy_url}", style="cyan")
    
    console.print("🎉 Local testing environment ready!", style="bold green") 