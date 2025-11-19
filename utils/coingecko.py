import httpx

async def get_tao_price() -> float:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "bittensor", "vs_currencies": "usd"}

    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()

    return data["bittensor"]["usd"]
