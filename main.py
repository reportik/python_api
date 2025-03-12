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
class AuthRequest(BaseModel):
    user_id: str
    password: str

@app.post("/auth/")
async def auth(data: AuthRequest):
    user_id = data.user_id
    password = data.password

    try:
        # 🔹 Obtener variables de entorno
        url = os.getenv('ODOO_URL')
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        # 🔹 Conectar con el servidor XML-RPC
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")

        # 🔹 Autenticación del usuario normal
        uid = common.authenticate(db, user_id, password, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Credenciales inválidas")

        # 🔹 Autenticación del usuario admin
        admin_uid = common.authenticate(db, admin_username, admin_password, {})
        if not admin_uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        # 🔹 Obtener el `partner_id` del usuario autenticado
        user_data = models.execute_kw(
            db, admin_uid, admin_password,
            "res.users", "read",
            [[uid]],  
            {"fields": ["partner_id"]}
        )

        if not user_data or "partner_id" not in user_data[0]:
            raise HTTPException(status_code=404, detail="No se encontró el partner del usuario")

        partner_id = user_data[0]["partner_id"][0]  # Obtener el ID del partner

        # 🔹 Obtener la información del cliente (partner)
        cliente = models.execute_kw(
            db, admin_uid, admin_password,
            "res.partner", "read",
            [[partner_id]],  # Aquí sí pasamos el ID del partner en una lista
            {"fields": ["id", "name", "property_product_pricelist"]}
        )

        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        return {
            "id": cliente[0]["id"],
            "name": cliente[0]["name"],
            "price_list": cliente[0]["property_product_pricelist"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
                   
                   
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

@app.get("/product/{product_id}/price/{pricelist_id}")
async def get_product_price(product_id: int, pricelist_id: int):
    try:
        # 🔹 Obtener variables de entorno
        url = os.getenv('ODOO_URL')
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        # 🔹 Conectar con el servidor XML-RPC
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        admin_uid = common.authenticate(db, admin_username, admin_password, {})

        if not admin_uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        # 🔹 Buscar producto en product.template
        product_data = models.execute_kw(
            db, admin_uid, admin_password,
            "product.template", "search_read",
            [[["id", "=", product_id]]],  # Filtrar por ID del producto
            {"fields": ["id", "name", "list_price"]}
        )

        if not product_data:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        # 🔹 Obtener el precio en la lista de precios con `compute_price`
        pricelist_price = models.execute_kw(
            db, admin_uid, admin_password,
            "product.pricelist.item", "search_read",
            [[["pricelist_id", "=", pricelist_id], ["product_tmpl_id", "=", product_id]]],
            {"fields": ["fixed_price"]}
        )

        # Si no hay precio específico en la lista de precios, usar `list_price`
        final_price = pricelist_price[0]["fixed_price"] if pricelist_price else product_data[0]["list_price"]

        return {
            "id": product_data[0]["id"],
            "name": product_data[0]["name"],
            "pricelist_price": final_price
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
