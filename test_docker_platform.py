#!/usr/bin/env python3
"""
Test script to verify Docker platform configuration for SWE-Bench on Apple Silicon.
"""

import os
import platform

print(f"System architecture: {platform.machine()}")
print(f"System platform: {platform.system()}")

# Check if DOCKER_DEFAULT_PLATFORM is set
docker_platform = os.getenv('DOCKER_DEFAULT_PLATFORM')
print(f"DOCKER_DEFAULT_PLATFORM: {docker_platform}")

# Simulate the fix
if platform.machine() == 'arm64' and not docker_platform:
    os.environ['DOCKER_DEFAULT_PLATFORM'] = 'linux/arm64'
    print("✅ Set DOCKER_DEFAULT_PLATFORM to linux/arm64")
else:
    print("✅ Platform configuration already set or not needed")

print(f"Current DOCKER_DEFAULT_PLATFORM: {os.getenv('DOCKER_DEFAULT_PLATFORM')}")

# Test Docker info
import subprocess
try:
    result = subprocess.run(['docker', 'info'], capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        lines = result.stdout.split('\n')
        for line in lines:
            if 'Architecture' in line:
                print(f"Docker Architecture: {line.strip()}")
                break
    else:
        print("⚠️  Could not get Docker info")
except Exception as e:
    print(f"⚠️  Error checking Docker: {e}")
