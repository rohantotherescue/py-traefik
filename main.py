import docker
from pydantic import BaseModel
from typing import Optional
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import Response
import httpx
import asyncio
import uvicorn
from docker_events import db, listen_to_docker_events

client = docker.from_env()
# db = {}

# def listen_to_docker_events():
#     try:
#         print("Listening to Docker events...")
#         event_stream = client.events(decode=True)
#         for event in event_stream:
#             if event.get("Type") == "container" and event.get("Action") == "start":
#                 container_id = event["id"]
#                 container = client.containers.get(container_id)
#                 container_info = container.attrs

#                 container_name = container_info["Name"].lstrip("/")
#                 ip_address = container_info["NetworkSettings"]["IPAddress"]
#                 exposed_ports = container_info["Config"].get("ExposedPorts", {})

#                 default_port = None
#                 if exposed_ports:
#                     for port in exposed_ports.keys():
#                         if port.endswith("/tcp"):
#                             default_port = port.split("/")[0]
#                             break

#                 print(f"Registering {container_name}.localhost --> http://{ip_address}:{default_port}")
#                 db[container_name] = {"container_name": container_name, "ip_address": ip_address, "default_port": default_port}

#     except Exception as e:
#         print("Error in getting events:", e)

app = FastAPI()

@app.middleware("http")
async def reverse_proxy(request: Request, call_next):
    print("Reverse proxy up..") 
    hostname = request.url.hostname
    subdomain = hostname.split(".")[0]

    if subdomain not in db:
        return Response("Not Found", status_code=404)

    ip_address = db[subdomain]["ip_address"]
    default_port = db[subdomain]["default_port"]
    target_url = f"http://{ip_address}:{default_port}"

    async with httpx.AsyncClient() as client:
        proxied_request = client.build_request(
            request.method, target_url + request.url.path, headers=dict(request.headers), data=await request.body()
        )
        response = await client.send(proxied_request, follow_redirects=True)

    return Response(content=response.content, status_code=response.status_code, headers=dict(response.headers))

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

    async with httpx.AsyncClient() as client:
        async with client.websocket_connect(target_url) as target_ws:
            await websocket.accept()

            async def forward_to_client():
                async for message in target_ws.iter_text():
                    await websocket.send_text(message)

            async def forward_to_target():
                async for message in websocket.iter_text():
                    await target_ws.send_text(message)

            await asyncio.gather(forward_to_client(), forward_to_target())

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

    image_already_exists = any(image_tag in sys_image.tags for sys_image in client.images.list())

    if not image_already_exists:
        print(f"Pulling image {image_tag}")
        client.images.pull(image, tag=tag)

    container = client.containers.create(
        image=image_tag,
        tty=False,
        host_config={'AutoRemove': True}
    )
    container.start()

    container_name = container.name
    return {"status": "success", "container": f"{container_name}.localhost"}

async def start_services():
    await asyncio.gather(
        asyncio.to_thread(listen_to_docker_events),
        asyncio.to_thread(uvicorn.run, app, host="0.0.0.0", port=80),
        asyncio.to_thread(uvicorn.run, managementAPI, host="0.0.0.0", port=8080)
    )

if __name__ == "__main__":
    asyncio.run(start_services())
