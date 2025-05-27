# Ridges AI

Many thanks to SN44 Score Vision, we've used their repo as inspiration for how to organize ours. Also thanks to the Rayon Labs team as our substrate interfaces are now built on Fiber

This repo will eventually be merged into the main ridgesai/ridges folder. In order to release the new changes such as new task types etc, we are refactoring the codebase to make it cleaner, support async tasks, and use fiber to allow us to more easily split emission between different types of tasks

## Local Setup

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