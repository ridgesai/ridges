# Ridges AI

Many thanks to SN44 Score Vision, we've used their repo as inspiration for how to organize ours. Also thanks to the Rayon Labs team as our substrate interfaces are now built on Fiber

This repo will eventually be merged into the main ridgesai/ridges folder. In order to release the new changes such as new task types etc, we are refactoring the codebase to make it cleaner, support async tasks, and use fiber to allow us to more easily split emission between different types of tasks

btcli wallet faucet --wallet.name miner --subtensor.chain_endpoint ws://127.0.0.1:9945

btcli subnet register --wallet.name miner --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945

uvicorn miner.main:app --host 0.0.0.0 --port 7999

fiber-post-ip --netuid 1 --subtensor.chain_endpoint ws://127.0.0.1:9945 --external_ip 10.0.0.156 --external_port 7999 --wallet.name miner --wallet.hotkey default

ipconfig getifaddr en0