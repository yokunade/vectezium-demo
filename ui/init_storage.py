"""Creates the lake container in Azurite (waiting for Azurite to accept connections)."""

import os
import sys
import time

from azure.core.exceptions import ResourceExistsError, ServiceRequestError
from azure.storage.blob import BlobServiceClient

CONTAINER = os.environ.get("LAKE_CONTAINER", "raw")

service = BlobServiceClient.from_connection_string(os.environ["AZURE_STORAGE_CONNECTION_STRING"])
for attempt in range(60):
    try:
        service.create_container(CONTAINER)
        print(f"Created container {CONTAINER}")
        break
    except ResourceExistsError:
        print(f"Container {CONTAINER} exists")
        break
    except ServiceRequestError:
        time.sleep(1)
else:
    sys.exit("Azurite did not become reachable")
