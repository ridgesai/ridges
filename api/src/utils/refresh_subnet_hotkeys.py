#!/usr/bin/env python3
import os
import json
import time
import dotenv
from fiber.chain.interface import get_substrate
from fiber.chain.fetch_nodes import get_nodes_for_netuid

dotenv.load_dotenv("api/.env")

substrate = get_substrate(
    subtensor_network=os.getenv("SUBTENSOR_NETWORK"), 
    subtensor_address=os.getenv("SUBTENSOR_ADDRESS")
)
nodes = get_nodes_for_netuid(substrate, int(os.getenv("NETUID")))
hotkeys = [node.hotkey for node in nodes]

with open("subnet_hotkeys_cache.json", 'w') as f:
    json.dump({"hotkeys": hotkeys, "timestamp": time.time()}, f)

print(f"Updated cache with {len(hotkeys)} hotkeys")
