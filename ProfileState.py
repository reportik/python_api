"""The profile page."""

from key import *
from pydantic import BaseModel
#from ..auth import authenticate_user
from model.o_products import OProducts

def odoo_tela_items(path: str) -> list[str] :
    # Ejemplo de uso
    url = ODOO_URL
    db = ODOO_DB
    
    username = ADMIN_USER
    password = ADMIN_PASS  

    oproducts = OProducts(url, db, username, password)
    productos_filtrados = oproducts.get_products_telas()
    return productos_filtrados
    #ProfileState.products = productos_filtrados
    
def getNombresTelas(productos_filtrados: list[OProducts]):
    return [producto['name'] for producto in productos_filtrados]

class ProfileState(BaseModel):
    radio_tipo_tela_value: str =""
   
    
    
    
    
    
