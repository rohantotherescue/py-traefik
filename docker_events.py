import docker

# Initialize Docker client and database dictionary
client = docker.from_env()
db = {}

def listen_to_docker_events():
    try:
    # Connect to Docker events stream
        print("Listening to Docker events...")
        event_stream = client.events(decode=True)
        for event in event_stream:
                # Process each event
            # print("Received Docker event:", event.get("Action"))
            if event.get("Type") == "container" and event.get("Action") == "start":
                container_id = event["id"]
                container = client.containers.get(container_id)
                
                container_info = container.attrs
                # print(container_info["NetworkSettings"])

                    # Extract container details
                container_name = container_info["Name"].lstrip("/")
                ip_address = container_info["NetworkSettings"]["IPAddress"]
                exposed_ports = container_info["Config"].get("ExposedPorts", {})

                    # Determine default port if any exposed
                default_port = None
                if exposed_ports:
                    for port in exposed_ports.keys():
                        if port.endswith("/tcp"):
                            default_port = port.split("/")[0]
                            break

                    # Register the container information in the db
                print(f"Registering {container_name}.localhost --> http://{ip_address}:{default_port}")
                db[container_name] = {"container_name": container_name, "ip_address": ip_address, "default_port": default_port}

    except Exception as e:
        print("Error in getting events:", e)

if __name__ == "__main__":
    __all__ = "db"
    listen_to_docker_events()