import os
import json
import requests



RUN_ID = os.getenv("RUN_ID")
if not RUN_ID:
    print("[AGENT] WARNING: RUN_ID is not set")

SANDBOX_PROXY_URL = os.getenv("SANDBOX_PROXY_URL")
if not SANDBOX_PROXY_URL:
    print("[AGENT] WARNING: SANDBOX_PROXY_URL is not set")



def inference(model, temperature, messages):
    try:
        payload = {
            "run_id": RUN_ID,
            "model": model,
            "temperature": temperature,
            "messages": messages
        }
        
        print(f"[AGENT] inference(): Sending inference request for model {model} (temperature {temperature}) with {len(messages)} messages")
        response = requests.post(
            f"{SANDBOX_PROXY_URL}/api/inference",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload)
        )
        
        if response.status_code == 200:
            result = response.text.strip('"')
            print(f"[AGENT] inference(): Inference response: {len(result)} characters")
            return result
        else:
            print(f"[AGENT] inference(): Inference failed with status {response.status_code}: {response.text}")
            return None
            
    except Exception as e:
        print(f"[AGENT] inference(): Inference request failed: {e}")
        return None



def embedding(input):
    try:
        payload = {
            "run_id": RUN_ID,
            "input": input
        }
        
        print(f"[AGENT] embedding(): Sending embedding request...")
        response = requests.post(
            f"{SANDBOX_PROXY_URL}/api/embedding",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload)
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"[AGENT] embedding(): Embedding response: {len(result)} dimensions")
            return result
        else:
            print(f"[AGENT] embedding(): Embedding failed with status {response.status_code}: {response.text}")
            return None
            
    except Exception as e:
        print(f"[AGENT] embedding(): Embedding request failed: {e}")
        return None



def agent_main(input): 
    print("[AGENT] Entered agent_main()")



    # Test inference function
    message = "What is 2+2?"
    print(f"[AGENT] <-- '{message}'")
    messages = [
        {"role": "user", "content": message}
    ]
    inference_result = inference("moonshotai/Kimi-K2-Instruct", 0.5, messages)
    if inference_result:
        print(f"[AGENT] --> '{inference_result}'")



    print("[AGENT] Reading solution from /sandbox/solution.diff")
    with open("/sandbox/solution.diff", "r") as f:
        diff = f.read()



    print("[AGENT] Exiting agent_main()")
    
    return diff