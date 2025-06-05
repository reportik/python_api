from fastapi import FastAPI, HTTPException
import pyodbc
from pydantic import BaseModel
from ProfileState import odoo_tela_items
import os
from dotenv import load_dotenv
import xmlrpc.client
# Cargar las variables de entorno desde .env
load_dotenv()
import base64
import requests

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
    return {"message": "Welcome to FastAPI on port 3036!"}

@app.get("/item/{item_name}")
def read_item(item_name: str):
    # Aquí puedes usar el parámetro item_name que toma el valor de 'BLACKOUT'
    return odoo_tela_items(item_name)
 
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
        #  Obtener variables de entorno
        url = os.getenv('ODOO_URL')
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        #  Conectar con el servidor XML-RPC
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")

        #  Autenticación del usuario normal
        uid = common.authenticate(db, user_id, password, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Credenciales inválidas")

        #  Autenticación del usuario admin
        admin_uid = common.authenticate(db, admin_username, admin_password, {})
        if not admin_uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        #  Obtener el `partner_id` del usuario autenticado
        user_data = models.execute_kw(
            db, admin_uid, admin_password,
            "res.users", "read",
            [[uid]],  
            {"fields": ["partner_id"]}
        )

        if not user_data or "partner_id" not in user_data[0]:
            raise HTTPException(status_code=404, detail="No se encontró el partner del usuario")

        partner_id = user_data[0]["partner_id"][0]  # Obtener el ID del partner

        #  Obtener la información del cliente (partner)
        cliente = models.execute_kw(
            db, admin_uid, admin_password,
            "res.partner", "read",
            [[partner_id]],  # Aquí sí pasamos el ID del partner en una lista
            {"fields": ["id", "name", "property_product_pricelist"]}
        )

        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        return {
            "partner_id": cliente[0]["id"],
            "user_id": uid,
            "name": cliente[0]["name"],
            "price_list": cliente[0]["property_product_pricelist"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
                   
# Ruta para obtener la imagen de un producto en base64                   
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

# Ruta para obtener el precio de un producto en una lista de precios específica
@app.get("/product/{product_id}/price/{pricelist_id}")
async def get_product_price(product_id: int, pricelist_id: int):
    try:
        #  Obtener variables de entorno
        url = os.getenv('ODOO_URL')
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        #  Conectar con el servidor XML-RPC
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        admin_uid = common.authenticate(db, admin_username, admin_password, {})

        if not admin_uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        #  Buscar producto en product
        product_data = models.execute_kw(
            db, admin_uid, admin_password,
            "product.product", "search_read",
            [[["id", "=", product_id]]],  # Filtrar por ID del producto
            {"fields": ["id", "name", "list_price"]}
        )

        if not product_data:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        #  Obtener el precio en la lista de precios con `compute_price`
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

# Ruta para guardar todas las imágenes de la tabla RPT_ODOO_CORTINAS en disco
@app.get("/save-all-images")
async def get_all_images():
    if not os.getenv("DB_DRIVER"):
        raise ValueError("No se encontró la variable DB_DRIVER en el archivo .env")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT Id, image, Tipo FROM RPT_ODOO_CORTINAS")
    results = cursor.fetchall()
    conn.close()

    if not results:
        raise HTTPException(status_code=404, detail="No se encontraron imágenes")

    for row in results:
        id, image_base64, tipo = row  # Asegúrate de capturar los tres valores correctamente
        save_image_to_disk(image_base64, id, tipo)  # Pasar 'tipo' a la función

    return {"ok"}

def save_image_to_disk(image_base64: str, id: int, tipo: str) -> str:
    """ Guarda una imagen en base64 en disco y devuelve su nombre de archivo con el formato `img_{id}_{tipo}.png`. """
    try:
        image_bytes = base64.b64decode(image_base64)

        # Normalizar el nombre del tipo (sin espacios ni caracteres especiales)
        tipo_clean = tipo.lower().replace(" ", "_")

        image_name = f"img_{id}_{tipo_clean}.png"
        image_path = os.path.join("images", image_name)

        os.makedirs("images", exist_ok=True)

        with open(image_path, "wb") as image_file:
            image_file.write(image_bytes)

        return image_name
    except Exception as e:
        return f"Error guardando imagen {id}: {str(e)}"

@app.get("/update_product_ids")
async def update_odoo_product_ids():
    try:
        #  Conectar a Odoo
        url = os.getenv('ODOO_URL')
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        admin_uid = common.authenticate(db, admin_username, admin_password, {})

        if not admin_uid:
            raise HTTPException(status_code=500, detail="Error al autenticar en Odoo")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        #  Conectar a la BD y obtener todas las telas
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT Id, Name FROM RPT_ODOO_CORTINAS")  
        telas = cursor.fetchall()

        if not telas:
            return {"message": "No hay telas pendientes de actualización"}

        productos_actualizados = []

        for id_tela, nombre_tela in telas:
            #  Buscar producto en Odoo por nombre
            product_data = models.execute_kw(
                db, admin_uid, admin_password,
                "product.product", "search_read",
                [[["name", "ilike", nombre_tela]]],  
                {"fields": ["id", "name"]}
            )

            if not product_data:
                productos_actualizados.append({"id": id_tela, "name": nombre_tela, "error": "Producto no encontrado"})
                continue

            product_id = product_data[0]["id"]

            #  Actualizar la base de datos con el ID de Odoo
            cursor.execute("UPDATE RPT_ODOO_CORTINAS SET Odoo_id = ? WHERE Id = ?", (product_id, id_tela))
            conn.commit()

            # productos_actualizados.append({
            #     "id": id_tela,
            #     "name": nombre_tela,
            #     "odoo_id": product_id
            # })

        conn.close()
        return {"updated_products": productos_actualizados}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/create-quotation2/")
async def create_quotation2(data: dict):
    try:
        # 🔹 Variables de entorno
        ODOO_URL = os.getenv("ODOO_URL")
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")

        # 🔹 Conectar con Odoo
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        # 🔹 Crear la cotización
        order_id = models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order", "create", [{
            "partner_id": data["partner_id"],
            "pricelist_id": data["pricelist_id"],
        }])

        if not order_id:
            raise HTTPException(status_code=500, detail="Error al crear la cotización")

        # 🔹 Crear líneas sin producto (solo descripción y precio)
        for line in data["order_lines"]:
            models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                "order_id": order_id,
                "name": line["description"],  # Descripción libre
                "product_uom_qty": line["quantity"],
                "price_unit": line["price_unit"],
                "product_uom": line.get("uom_id", 1),  # Default: unidad (id=1)
                "product_uom_qty": 1,
            }])

        return {
            "status": "success",
            "message": "Cotización creada con éxito con líneas personalizadas",
            "order_id": order_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/create-quotation/")
async def create_quotation(data: dict):
    try:
        # 🔹 Variables de entorno
        ODOO_URL = os.getenv("ODOO_URL")
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")

        # 🔹 Conectar con Odoo
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        # 🔹 Crear la cotización en `sale.order`
        order_id = models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order", "create", [{
            "partner_id": data["partner_id"],
            "pricelist_id": data["pricelist_id"],
        }])

        if not order_id:
            raise HTTPException(status_code=500, detail="Error al crear la cotización en Odoo")

        # 🔹 Agregar líneas de productos
        for line in data["order_lines"]:
            product = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "product.product", "search_read",
                [[["id", "=", line["product_id"]]]],
                {"fields": ["id", "name", "uom_id"]}
            )

            if not product:
                raise HTTPException(status_code=404, detail=f"Producto con ID {line['product_id']} no encontrado")

            product_name = product[0]["name"]
            product_uom = product[0]["uom_id"][0]

            models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                "order_id": order_id,
                "product_id": line["product_id"],
                "name": product_name,
                "product_uom_qty": line["quantity"],
                "product_uom": product_uom,
                "price_unit": line["price_unit"],
            }])

        return {
            "status": "success",
            "message": "Cotización creada con éxito",
            "order_id": order_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/generate-quotation-pdf/{order_id}")
async def generate_quotation_pdf(order_id: int):
    try:
        ODOO_URL = os.getenv("ODOO_URL")
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")
        PDF_PATH = os.getenv("PDF_PATH", "C:\\xampp\\htdocs\\invtek_frontend\\public\\pdfs" if os.name == "nt" else "/var/www/html/invtek_frontend/public/pdfs")

        session = requests.Session()
        session.headers.update({'Content-Type': 'application/json'})

        # 🔹 Autenticación en Odoo para obtener sesión
        login_payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "db": ODOO_DB,
                "login": ODOO_USER,
                "password": ODOO_PASS,
            }
        }
        session.post(f"{ODOO_URL}/web/session/authenticate", json=login_payload)

        # 🔹 Descargar el PDF
        pdf_response = session.get(f"{ODOO_URL}/report/pdf/sale.report_saleorder/{order_id}")
        if pdf_response.status_code == 200:
            pdf_filename = f"Cotizacion_{order_id}.pdf"
            pdf_filepath = os.path.join(PDF_PATH, pdf_filename)

            os.makedirs(PDF_PATH, exist_ok=True)
            with open(pdf_filepath, "wb") as f:
                f.write(pdf_response.content)

            return {
                "status": "success",
                "pdf_name": pdf_filename
            }
        else:
            raise HTTPException(status_code=500, detail="Error al generar el PDF")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create-contact/")
async def create_contact(contact: dict):
    try:
        # 🔹 Variables de entorno
        ODOO_URL = os.getenv("ODOO_URL")
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")

        # 🔹 Conexión con Odoo
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        # 🔹 Verificar si ya existe un contacto con ese correo
        existing = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, 'res.partner', 'search_read',
            [[['email', '=', contact["email"]]]],
            {'fields': ['id', 'name'], 'limit': 1}
        )

        if existing:
            # return para el caso de contacto existente
            return {
                "status": "exists",
                "message": "El contacto ya existe",
                "partner_id": existing[0]["id"]
            }

        # 🔹 Crear el nuevo contacto
        partner_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, 'res.partner', 'create',
            [{
                'name': contact["name"],
                'email': contact["email"],
            }]
        )

        # return para el caso de nuevo registro
        return {
            "status": "created",
            "message": "Contacto creado con éxito",
            "partner_id": partner_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/products/active/sellable")
async def get_active_sellable_products():
    try:
        # Obtener variables de entorno
        url = os.getenv('ODOO_URL').replace("\\x3a", ":")
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        # Conectar con Odoo
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, admin_username, admin_password, {})

        if not uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        # Buscar productos activos y vendibles
        products = models.execute_kw(
            db, uid, admin_password,
            'product.product', 'search_read',
            [[
                ['active', '=', True],
                ['sale_ok', '=', True],
                ['categ_id', '<>', 66],
            ]],
            {'fields': ['id', 'name', 'categ_id', 'list_price']}
        )

        return products

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class RegisterData(BaseModel):
    name: str
    user_id: EmailStr
    password: str

@app.post("/register")
async def register_user(data: RegisterData):
    try:
        # 🔹 Configuración de conexión Odoo
        ODOO_URL = os.getenv("ODOO_URL").replace("\\x3a", ":")
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")

        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        # 🔍 Verificar si el contacto ya existe por email
        existing_contacts = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "res.partner", "search_read",
            [[["email", "=", data.user_id]]],
            {"fields": ["id", "email"], "limit": 1}
        )

        if existing_contacts:
            raise HTTPException(status_code=409, detail="El usuario ya existe")

        # 🧑 Crear contacto
        partner_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "res.partner", "create",
            [{
                "name": data.name,
                "email": data.user_id,
                "customer_rank": 1  # Se marca como cliente
            }]
        )

        # 👤 Crear usuario en Odoo
        user_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "res.users", "create",
            [{
                "name": data.name,
                "login": data.user_id,
                "email": data.user_id,
                "password": data.password,
                "partner_id": partner_id,
                "groups_id": [(6, 0, [])]  # Sin permisos especiales
            }]
        )

        # Obtener info del partner
        cliente = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "res.partner", "read",
            [[partner_id]],
            {"fields": ["id", "name", "property_product_pricelist"]}
        )

        return {
            "partner_id": partner_id,
            "user_id": user_id,
            "name": cliente[0]["name"] if cliente else "",
            "price_list": cliente[0]["property_product_pricelist"] if cliente else None
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
