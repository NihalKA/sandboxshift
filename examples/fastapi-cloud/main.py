"""FastAPI cloud example — REST API on AWS Fargate.

Identical app to fastapi-local but configured to always run in cloud.
Useful when the API needs to be reachable from outside your machine,
or when the service is too heavy for a developer laptop.

Endpoints:
  GET  /           → health check
  GET  /items      → list items
  POST /items      → create an item
  GET  /items/{id} → get an item by id

-------------------------------------------------------------------------------
Running with YAML config (uses sandboxshift.yaml in this directory):

  sandboxshift run examples/fastapi-cloud "uvicorn main:app --host 0.0.0.0 --port 8000"

Running with CLI flags only (no YAML needed):

  sandboxshift run examples/fastapi-cloud \\
    "uvicorn main:app --host 0.0.0.0 --port 8000" \\
    --mode cloud \\
    --port 8000 \\
    --cpu 1.0 \\
    --memory-mb 2048 \\
    --timeout 3600 \\
    --setup "pip install -r requirements.txt"

Once running:
  sandboxshift list              → get the public IP
  curl http://<public-ip>:8000/  → test the health endpoint
  open http://<public-ip>:8000/docs  → Swagger UI
  sandboxshift stop <id>         → stop when done

Fargate: 1 vCPU requires 2–8 GB memory.
-------------------------------------------------------------------------------
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="SandboxShift FastAPI cloud example", version="1.0.0")

_items: dict[int, dict] = {}
_next_id: int = 1


class ItemCreate(BaseModel):
    name: str
    price: float


class Item(BaseModel):
    id: int
    name: str
    price: float


@app.get("/")
def health() -> dict:
    return {"status": "ok", "env": "fargate", "items": len(_items)}


@app.get("/items", response_model=list[Item])
def list_items() -> list[dict]:
    return list(_items.values())


@app.post("/items", response_model=Item, status_code=201)
def create_item(body: ItemCreate) -> dict:
    global _next_id
    item = {"id": _next_id, "name": body.name, "price": body.price}
    _items[_next_id] = item
    _next_id += 1
    return item


@app.get("/items/{item_id}", response_model=Item)
def get_item(item_id: int) -> dict:
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="Item not found")
    return _items[item_id]
