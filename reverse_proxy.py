import httpx
import asyncio
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect
from docker_events import db  # Importing the db from docker_events

app = FastAPI()

# Proxy HTTP requests
@app.middleware("http")
async def reverse_proxy(request: Request, call_next):
    print("Reverse proxy up..")
    hostname = request.url.hostname
    subdomain = hostname.split(".")[0]

    # Check if the subdomain exists in the db
    if subdomain not in db:
        return Response("Not Found", status_code=404)

    # Retrieve target IP and port
    # print("IP ADDRESS: ",db[subdomain]["ip_address"])  logging
    # print("PORT NUMBER: ",db[subdomain]["default_port"])
    ip_address = db[subdomain]["ip_address"]
    default_port = db[subdomain]["default_port"]
    target_url = f"http://{ip_address}:{default_port}"

    # Forward the request to the target
    async with httpx.AsyncClient() as client:
        proxied_request = client.build_request(
            request.method, target_url + request.url.path, headers=dict(request.headers), data=await request.body()
        )
        print("Proxied request: ", proxied_request)
        response = await client.send(proxied_request, follow_redirects=True)

    # Return the proxied response
    return Response(content=response.content, status_code=response.status_code, headers=dict(response.headers))

# WebSocket forwarding
@app.websocket_route("/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str):
    hostname = websocket.url.hostname
    subdomain = hostname.split(".")[0]

    # Check if the subdomain exists in the db
    if subdomain not in db:
        await websocket.close(code=404)
        return

    # Retrieve target IP and port
    ip_address = db[subdomain]["ip_address"]
    default_port = db[subdomain]["default_port"]
    target_url = f"ws://{ip_address}:{default_port}/{path}"

    # Connect to target WebSocket
    async with httpx.AsyncClient() as client:
        async with client.websocket_connect(target_url) as target_ws:
            await websocket.accept()

            async def forward_to_client():
                async for message in target_ws.iter_text():
                    await websocket.send_text(message)

            async def forward_to_target():
                async for message in websocket.iter_text():
                    await target_ws.send_text(message)

            # Start bidirectional communication
            await asyncio.gather(forward_to_client(), forward_to_target())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80)
