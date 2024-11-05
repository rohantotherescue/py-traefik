import docker
from pydantic import BaseModel
from typing import Optional
from fastapi import FastAPI

client =  docker.from_env()

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

    # Check if the image already exists
    image_already_exists = False
    images = client.images.list()
    for system_image in images:
        if image_tag in system_image.tags:
            image_already_exists = True
            break

    # Pull the image if it does not exist
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

    # Return response
    container_name = container.name
    return {
        "status": "success",
        "container": f"{container_name}.localhost"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(managementAPI, host="0.0.0.0", port=8080)