from fastapi import FastAPI, HTTPException
import pyodbc
from pydantic import BaseModel
from ProfileState import odoo_tela_items
import os
from dotenv import load_dotenv
import xmlrpc.client

# Cargar las variables de entorno desde .env
load_dotenv()

app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Origen permitido (tu frontend)
    allow_credentials=True,
    allow_methods=["*"],  # Métodos permitidos (GET, POST, etc.)
    allow_headers=["*"],  # Encabezados permitidos
)

# Modelo para recibir datos en solicitudes POST
class Item(BaseModel):
    name: str
    price: float
    is_offer: bool = None

# Ruta GET
@app.get("/")
def read_root():
    return {"message": "Welcome to FastAPI on port 8035!"}

@app.get("/item/{item_name}")
def read_item(item_name: str):
    # Aquí puedes usar el parámetro item_name que toma el valor de 'BLACKOUT'
    return odoo_tela_items(item_name)

# Ruta POST
@app.post("/items/")
def create_item(item: Item):
    return {"name": item.name, "price": item.price, "is_offer": item.is_offer}

# Configurar la conexión a SQL Server
def get_db_connection():
    return pyodbc.connect(
        f"DRIVER={{{os.getenv('DB_DRIVER')}}};"  # Nota: Sin espacios extra en las llaves
        f"SERVER={os.getenv('DB_SERVER')};"
        f"DATABASE={os.getenv('DB_DATABASE')};"
        f"UID={os.getenv('DB_USER')};"
        f"PWD={os.getenv('DB_PASSWORD')};"
        "Encrypt=yes;"  # Opcional para conexiones seguras
        "TrustServerCertificate=yes;"  # Si es necesario para evitar errores de certificados
    )

# Ruta para obtener el userName de un usuario logueado, recibiendo el id del usuario y la contraseña con POST
@app.post("/auth/")
async def get_user_name(user_id: str, password: str):
    # colocar un try catch para manejar errores
    try:
        url = os.getenv('ODOO_URL')
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')   
        
        common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))    
        
        uid = common.authenticate(db, user_id, password, {})
        admin_uid = common.authenticate(db, admin_username, admin_password, {})
                            
        models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))
        #email_normalized email                
        #ids = models.execute_kw(db, uid, password, 'res.partner', 'search', [[['write_uid', '=', uid]]], { 'limit': 1})                                                                                    
        socios = models.execute_kw(db, admin_uid, admin_password, 'res.users', 'read', [uid], {})
        
        #print(socios[0])
        temp_user = ""+socios[0]["name"]+""
        return {"user_name": temp_user}
    except Exception as e:
        return {"user_name": "Error"}
                   
                   
@app.get("/get-image/{id}")
async def get_image(id: int):

    if not os.getenv("DB_DRIVER"):
        raise ValueError("No se encontró la variable DB_DRIVER en el archivo .env")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT image FROM RPT_ODOO_CORTINAS WHERE Id = ?", (id))
    result = cursor.fetchone()
    conn.close()

    if result:
        return {"image": result[0]}
    else:
        raise HTTPException(status_code=404, detail="Item not found")
