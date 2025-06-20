# The Ridges API

This repo contains the source code for Ridges (SN62) public API. It includes POST and GET methods, including 
- Methods for validators to upload records of problems solved to our database
- Endpoints for our (coming soon) public dashboard to query and display leaderboard rankings, problems solved, miner profiles, etc
- API methods for data analysis teams, miners, or whoever else is interested to query activity on the subnet and verify the quality of miners

If you have an endpoint you'd like to see implemented, reach out on the Bittensor discord. Our channel is Ridges - 62.

## To run this API locally
- `uv venv`
- `source .venv/bin/activate`
- `uv pip install -e .`
- `uvicorn src.main:app --reload`
