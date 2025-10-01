import random
import uvicorn
import requests
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from models import InferenceRequest, EmbeddingRequest, Settings



settings = Settings()



chutes_client = OpenAI(
    base_url=settings.CHUTES_BASE_URL,
    api_key=settings.CHUTES_API_KEY
)



app = FastAPI(title="Inference Gateway", description="Inference gateway server with embedding and inference endpoints, forwards whitelisted requests to Chutes and Targon")



@app.post("/api/inference")
async def inference(request: InferenceRequest):
    try:
        messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]
        response = chutes_client.chat.completions.create(
            model=request.model,
            messages=messages,
            temperature=request.temperature,
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/embedding")
async def embedding(request: EmbeddingRequest):
    try:
        response = requests.post(
            settings.CHUTES_EMBEDDING_URL,
            headers={
                "Authorization": f"Bearer {settings.CHUTES_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "inputs": request.input,
                "seed": random.randint(0, 2 ** 32 - 1)
            }
        )
        response.raise_for_status()
        return response.json()[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)