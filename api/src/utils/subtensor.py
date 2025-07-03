from substrateinterface import SubstrateInterface
import requests
import os
import dotenv
import time

from api.src.utils.logging_utils import get_logger
from api.src.db.operations import DatabaseManager

dotenv.load_dotenv()

logger = get_logger(__name__)

db_manager = DatabaseManager()

# Simple cache for single subnet price
_cached_price = None
_cached_time = 0
_cached_tao_usd_price = None
_cached_tao_usd_time = 0

def get_current_weights(netuid: int = 62) -> dict:
    """
    Method using substrate-interface library to get weights in bittensor format. Returns None if there is an error.
    """
    try:
        substrate = SubstrateInterface (
            url="wss://entrypoint-finney.opentensor.ai:443",
            ss58_format=42, 
            type_registry_preset="substrate-node-template"
        )
        
        result = substrate.query_map(
            module="SubtensorModule",
            storage_function="Weights",
            params=[netuid],
        )
        
        weights = [(uid, w.value or []) for uid, w in result]

        return weights
        
    except Exception as e:
        logger.error(f"Error getting weights from substrate: {e}")
        return None
    
def get_uid_for_hotkey_on_subnet(hotkey: str, netuid: int = 62) -> int:
        """
        Get the UID for a hotkey on a subnet. Returns None if there is an error.
        """
        try:
            substrate = SubstrateInterface (
                url="wss://entrypoint-finney.opentensor.ai:443",
                ss58_format=42, 
                type_registry_preset="substrate-node-template"
            )
            
            result = substrate.query(
                module="SubtensorModule",
                storage_function="Uids",
                params=[netuid, hotkey],
            )
            
            return getattr(result, "value", result)
        
        except Exception as e:
            logger.error(f"Error getting UID for hotkey {hotkey} on subnet {netuid}: {e}")
            return None
        
def get_subnet_token_price_in_tao(netuid: int):
    """
    Get the price of the subnet token in TAO. Returns None if there is an error.
    Caches results for 30 seconds to avoid excessive API calls.
    """
    global _cached_price, _cached_time
    
    if _cached_price is not None and time.time() - _cached_time < 30:
        return _cached_price
    
    try:
        url = "https://api.taostats.io/api/dtao/pool/latest/v1"

        headers = {
            "accept": "application/json",
            "Authorization": os.getenv("TAOSTATS_API_KEY")
        }

        response = requests.get(url, headers=headers, params={"netuid": netuid})
        price_in_tao = response.json()['data'][0]['price']
        price_float = float(price_in_tao)
        
        _cached_price = price_float
        _cached_time = time.time()
        
        return price_float
    except Exception as e:
        logger.error(f"Error getting subnet token price in TAO: {e}")
        return None

def get_tao_usd_price():
    """
    Get TAO's current price in USD using TaoStats API. Returns None if there is an error.
    Caches results for 30 seconds to avoid excessive API calls.
    """
    global _cached_tao_usd_price, _cached_tao_usd_time
    
    if _cached_tao_usd_price is not None and time.time() - _cached_tao_usd_time < 30:
        return _cached_tao_usd_price
    
    try:
        url = "https://api.taostats.io/api/price/latest/v1?asset=tao"

        headers = {
            "accept": "application/json",
            "Authorization": os.getenv("TAOSTATS_API_KEY")
        }
        response = requests.get(url, headers=headers)

        tao_usd_price = response.json()['data'][0]['price']
        _cached_tao_usd_price = float(tao_usd_price)
        _cached_tao_usd_time = time.time()
        
        return _cached_tao_usd_price
    except Exception as e:
        print(f"Error getting TAO USD price: {e}")
        return None

async def get_daily_earnings_by_hotkey(hotkey: str, netuid: int = 62) -> float:
    """
    Get the daily earnings for a hotkey on a subnet. Returns None if there is an error.
    """
    try:
        miner_uid = get_uid_for_hotkey_on_subnet(hotkey, netuid)
        
        if not miner_uid:
            logger.error(f"Tried to calculate daily earnings for hotkey {hotkey} on subnet {netuid}, but no UID found")
            return None

        subnet_token_price_in_tao = get_subnet_token_price_in_tao(netuid)
        tao_usd_price = get_tao_usd_price()
        subnet_token_price_in_usd = subnet_token_price_in_tao * tao_usd_price
        
        top_miner_daily_rewards = subnet_token_price_in_usd * 7200 * 0.41

        weights_data = await db_manager.get_weights_history_last_24h_with_prior()
        
        total_seconds = 0
        uid_top_seconds = 0

        for i in range(1, len(weights_data)):
            prev = weights_data[i - 1]
            curr = weights_data[i]

            dt = (curr.timestamp - prev.timestamp).total_seconds()
            total_seconds += dt

            prev_weights = prev.miner_weights
            if not prev_weights:
                continue
            top_uid = max(prev_weights.items(), key=lambda x: float(x[1]))[0]

            if int(top_uid) == int(miner_uid):
                uid_top_seconds += dt

        if total_seconds == 0:
            percentage_of_top_miner_fraction = 0.0
        else:
            percentage_of_top_miner_fraction = uid_top_seconds / total_seconds

        daily_earnings = top_miner_daily_rewards * percentage_of_top_miner_fraction

        logger.info(f"Daily earnings for {hotkey} on subnet {netuid} with UID {miner_uid} and top miner fraction {percentage_of_top_miner_fraction}: {daily_earnings}")

        return daily_earnings
    
    except Exception as e:
        logger.error(f"Error getting daily earnings for hotkey {hotkey} on subnet {netuid}: {e}")
        return None
