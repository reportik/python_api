from fastapi import FastAPI
from pydantic import BaseModel
from ProfileState import odoo_tela_items
app = FastAPI()

# Modelo para recibir datos en solicitudes POST
class Item(BaseModel):
    name: str
    price: float
    is_offer: bool = None

# Ruta GET
@app.get("/")
def read_root():
    return {"message": "Welcome to FastAPI on port 8035!"}

@app.get("/items")
def read_item():
    return odoo_tela_items('')

# Ruta POST
@app.post("/items/")
def create_item(item: Item):
    return {"name": item.name, "price": item.price, "is_offer": item.is_offer}
