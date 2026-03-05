import httpx
from typing import List, Any, Optional
from pydantic import BaseModel, Field

class MarketResponse(BaseModel):
    question: Optional[str] = ""
    description: Optional[str] = ""
    outcomes: Any = []
    slug: Optional[str] = ""

class GammaClient:
    def __init__(self):
        self.base_url = "https://gamma-api.polymarket.com"

    async def fetch_action_markets(self) -> List[dict]:
        url = f"{self.base_url}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": 30,
            "searchTerm": "mrbeast"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            markets = data if isinstance(data, list) else data.get("data", [])
            
            structured_markets = []
            for market_data in markets:
                try:
                    # 使用 Pydantic 进行结构化并保留指定字段
                    market = MarketResponse(
                        question=market_data.get("question", ""),
                        description=market_data.get("description", ""),
                        outcomes=market_data.get("outcomes", []),
                        slug=market_data.get("slug", "")
                    )
                    structured_markets.append(market.model_dump())
                except Exception:
                    pass
                
            return structured_markets
