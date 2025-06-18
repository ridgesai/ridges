#!/usr/bin/env python3
"""Test script for GitHub utils functionality."""

from validator.utils.github_utils import get_commits_behind_main

def test_github_utils():
    """Test the GitHub utils functionality."""
    print("Testing GitHub utils...")
    
    try:
        commits_behind = get_commits_behind_main()
        
        if commits_behind == 0:
            print("✅ Up to date with main branch!")
        elif commits_behind > 0:
            print(f"⚠️  {commits_behind} commits behind main branch")
        else:
            print("❌ Could not determine commits behind main")
            
    except Exception as e:
        print(f"❌ Error testing GitHub utils: {e}")

if __name__ == "__main__":
    test_github_utils() 