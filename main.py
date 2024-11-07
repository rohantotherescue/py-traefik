import asyncio
import ssl
import docker
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from starlette.responses import Response
import httpx
import uvicorn
from pydantic import BaseModel
from typing import Optional
import websockets
from docker_events import db, listen_to_docker_events

# Docker client setup
client = docker.from_env()
timeout = httpx.Timeout(10.0, connect=10.0, read=20.0)

# FastAPI app for proxying requests
app = FastAPI()

@app.middleware("http")
async def reverse_proxy(request: Request, call_next):
    print("Reverse proxy middleware triggered")
    hostname = request.url.hostname
    subdomain = hostname.split(".")[0]

    if subdomain not in db:
        return Response("Not Found", status_code=404)

    ip_address = db[subdomain]["ip_address"]
    default_port = db[subdomain]["default_port"]
    target_url = f"http://{ip_address}:{default_port}{request.url.path}{'?' + request.url.query if request.url.query else ''}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            # Send request to the container
            proxied_response = await client.request(
                method=request.method,
                url=target_url,
                headers=request.headers,
                content=await request.body()
            )

            # Prepare headers, removing ones that could cause issues
            headers = {key: value for key, value in proxied_response.headers.items() if key.lower() != 'content-encoding'}

            # Respond with the proxied response
            return Response(
                content=proxied_response.content,
                status_code=proxied_response.status_code,
                headers=headers,
                media_type=proxied_response.headers.get("content-type")
            )
        except httpx.HTTPStatusError as e:
            print(f"HTTP Status Error: {e}")
            return Response("Request failed", status_code=e.response.status_code)
        except Exception as e:
            print(f"General request error: {e}")
            return Response("An error occurred while processing the request", status_code=500)

@app.websocket("/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str):
    await websocket.accept()
    hostname = websocket.url.hostname
    subdomain = hostname.split(".")[0]

    if subdomain not in db:
        await websocket.close(code=1000)
        return

    ip_address = db[subdomain]["ip_address"]
    default_port = db[subdomain]["default_port"]
    target_url = f"ws://{ip_address}:{default_port}/{path}"

    # Collect headers from the original WebSocket request
    headers = {key: value for key, value in websocket.headers.items()}

    try:
        async with websockets.connect(target_url, extra_headers=headers) as target_ws:
            async def forward(ws_from, ws_to):
                try:
                    async for message in ws_from:
                        if isinstance(message, bytes):
                            await ws_to.send_bytes(message)
                        else:
                            await ws_to.send_text(message)
                except WebSocketDisconnect:
                    pass

            await asyncio.gather(
                forward(websocket, target_ws),
                forward(target_ws, websocket)
            )
    except websockets.InvalidStatusCode as e:
        print(f"Invalid status code: {e.status_code}")
        await websocket.close(code=1000)
    except Exception as e:
        print(f"Error during WebSocket proxying: {e}")
        await websocket.close(code=1000)



# Management API for container handling
managementAPI = FastAPI()

class ContainerRequest(BaseModel):
    image: str
    tag: Optional[str] = "latest"

@managementAPI.get("/healthCheck")
async def health_check():
    return "I'm good bro."

@managementAPI.post("/containers")
async def create_container(request: ContainerRequest):
    image = request.image
    tag = request.tag
    image_tag = f"{image}:{tag}"

    # Check if the image is already present
    image_already_exists = any(image_tag in sys_image.tags for sys_image in client.images.list())

    if not image_already_exists:
        print(f"Pulling image {image_tag}")
        client.images.pull(image, tag=tag)

    # Create and start the container
    container = client.containers.create(
        image=image_tag,
        tty=False,
        host_config={'AutoRemove': True}
    )
    container.start()

    container_name = container.name
    return {"status": "success", "container": f"{container_name}.localhost"}

# Start services
async def start_services():
    await asyncio.gather(
        asyncio.to_thread(listen_to_docker_events),
        asyncio.to_thread(uvicorn.run, app, host="0.0.0.0", port=80),
        asyncio.to_thread(uvicorn.run, managementAPI, host="0.0.0.0", port=8080)
    )

if __name__ == "__main__":
    asyncio.run(start_services())
