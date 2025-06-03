# Ridges AI

Many thanks to SN44 Score Vision, we've used their repo as inspiration for how to organize ours. Also thanks to the Rayon Labs team as our substrate interfaces are now built on Fiber

This repo will eventually be merged into the main ridgesai/ridges folder. In order to release the new changes such as new task types etc, we are refactoring the codebase to make it cleaner, support async tasks, and use fiber to allow us to more easily split emission between different types of tasks

## Quick Start (Recommended)

You can set up and run a local Ridges testnet with a single script:

```sh
./ridges-localnet.sh
```

This script will:
- Build the Docker images (unless you comment out the build line if already built)
- Set up the Subtensor node and the miner
- Create wallets and hotkeys with `_ridges` suffixes to avoid overwriting your existing keys

**Note:**
- The validator is **not** started automatically by the script. You must start it manually (see below).
- The miner and validator use the following wallet/hotkey names by default:
  - Miner: `miner_ridges` / `default_ridges`
  - Validator: `validator_ridges` / `default_ridges`

## Running the Validator

To run the validator, you must set your OpenAI API key and start the service manually:

1. **Set your OpenAI API key in `docker-compose.yml`:**
   ```yaml
   environment:
     - OPENAI_API_KEY=sk-...your-key-here...
   ```
   Or export it in your shell before running Docker Compose:
   ```sh
   export OPENAI_API_KEY=sk-...your-key-here...
   ```

2. **Start the validator:**
   ```sh
   docker compose up validator
   ```

The validator will use the shared wallet volume and the correct wallet/hotkey names as set in the compose file.

## Notes
- If you already have built the Docker image, you can comment out the build line in the script for faster startup.
- If you want to use different wallet/hotkey names, update the environment variables in `docker-compose.yml` for the miner and validator services.
- All containers share the same wallet volume, so keys created in one are available to all.

## Local Setup (Manual/Advanced)

1. Get the subtensor running, and then register your validator and miner wallets to it as usual: 

- `btcli wallet faucet --wallet.name validator --subtensor.chain_endpoint ws://127.0.0.1:9945`
- `btcli subnet register --wallet.name validator --subtensor.chain_endpoint ws://127.0.0.1:9945`
- Add some stake to your validator: `btcli stake add --wallet.name validator --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945`
- `btcli wallet faucet --wallet.name miner --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945`
- `btcli subnet register --wallet.name miner --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945`

2. Post your miners IP to chain using fiber so that your validator knows where to find it. 

- `fiber-post-ip --netuid 1 --subtensor.chain_endpoint ws://127.0.0.1:9945 --external_ip 0.0.0.1 --external_port 7999 --wallet.name miner --wallet.hotkey default`
- Note that for external IP, if you use 0.0.0.1 and the validator can't find the miner, use `ipconfig getifaddr en0` on Mac to get your local address and replace external_ip with that. Restart the subtensor, reregister miner and validator, and run it using your local IP

4. Set up your .env's in both the miner and validator dir. Use .env.example to see what you have to set

5. Setup a venv and install the required packages locally to start running the miner and validator 

- `uv venv`
- `uv pip install -e .`

5. Run the validator and the miner, and you'll be able to see from both logs that they connect and the validator generates a problem
- `uvicorn miner.main:app --host 0.0.0.0 --port 7999` to start the miner
- `uv run validator/main.py`

## Helpful commands

- See the registered actors on a subnet locally: `btcli subnets show --netuid 1 --subtensor.chain_endpoint ws://127.0.0.1:9945`

## Updating the Miner After Code Changes

If you make changes to the code in the `miner` directory, you need to restart the miner container to apply those changes:

```sh
docker compose restart miner
```

If you change dependencies (e.g., update `pyproject.toml`) or the Dockerfile, you must rebuild the image and then restart the miner:

```sh
docker compose build miner
docker compose up miner
```

- **Code changes only:** Just restart the miner container.
- **Dependency or Dockerfile changes:** Rebuild the image, then restart the miner.