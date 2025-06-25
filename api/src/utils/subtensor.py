from substrateinterface import SubstrateInterface

def get_current_weights(netuid: int):
    """
    Method using substrate-interface library to get weights in bittensor format
    """
    try:
        
        # Connect to Bittensor
        substrate = SubstrateInterface(
            url="wss://entrypoint-finney.opentensor.ai:443",
            ss58_format=42,  # Bittensor uses format 42
            type_registry_preset="substrate-node-template"
        )
        
        # Query weights storage map
        result = substrate.query_map(
            module="SubtensorModule",
            storage_function="Weights",
            params=[netuid],
        )
        
        # Process results to match bittensor module format
        weights = [(uid, w.value or []) for uid, w in result]
        return weights
        
    except Exception as e:
        return {"error": f"Error getting weights from substrate: {e}"}
