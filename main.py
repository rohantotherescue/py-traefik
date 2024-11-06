import docker
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import Response
import httpx
import asyncio
import uvicorn
from pydantic import BaseModel
from typing import Optional

# Docker client setup
client = docker.from_env()
timeout = httpx.Timeout(10.0, connect=10.0, read=10.0)
db = {}

# Listening to Docker events
def listen_to_docker_events():
    try:
        print("Listening to Docker events...")
        event_stream = client.events(decode=True)
        for event in event_stream:
            if event.get("Type") == "container" and event.get("Action") == "start":
                container_id = event["id"]
                container = client.containers.get(container_id)
                container_info = container.attrs

                container_name = container_info["Name"].lstrip("/")
                ip_address = container_info["NetworkSettings"]["IPAddress"]
                exposed_ports = container_info["Config"].get("ExposedPorts", {})

                # Find a default port if available
                default_port = None
                if exposed_ports:
                    for port in exposed_ports.keys():
                        if port.endswith("/tcp"):
                            default_port = port.split("/")[0]
                            break

                if default_port:
                    print(f"Registering {container_name}.localhost --> http://{ip_address}:{default_port}")
                    db[container_name] = {"container_name": container_name, "ip_address": ip_address, "default_port": default_port}

    except Exception as e:
        print("Error in getting events:", e)

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
    target_url = f"http://{ip_address}:{default_port}{request.url.path}"

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

# WebSocket proxy route
@app.websocket_route("/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str):
    hostname = websocket.url.hostname
    subdomain = hostname.split(".")[0]

    if subdomain not in db:
        await websocket.close(code=404)
        return

    ip_address = db[subdomain]["ip_address"]
    default_port = db[subdomain]["default_port"]
    target_url = f"ws://{ip_address}:{default_port}/{path}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.websocket_connect(target_url) as target_ws:
                await websocket.accept()

                async def forward_to_client():
                    async for message in target_ws.iter_text():
                        await websocket.send_text(message)

                async def forward_to_target():
                    async for message in websocket.iter_text():
                        await target_ws.send_text(message)

                await asyncio.gather(forward_to_client(), forward_to_target())
        except httpx.RequestError as e:
            print("WebSocket connection failed:", e)
            await websocket.close(code=500)

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
