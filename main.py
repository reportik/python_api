from fastapi import FastAPI, HTTPException
import pyodbc
from pydantic import BaseModel, EmailStr
from ProfileState import odoo_tela_items
import os
from dotenv import load_dotenv
import xmlrpc.client
from datetime import datetime, timedelta
import jwt
# Cargar las variables de entorno desde .env
load_dotenv()
import base64
import requests
#uvicorn main:app --host 0.0.0.0 --port 3036

app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Origen permitido (tu frontend)
    allow_credentials=True,
    allow_methods=["*"],  # Métodos permitidos (GET, POST, etc.)
    allow_headers=["*"],  # Encabezados permitidos
)

def get_odoo_url() -> str:
    """
    Normaliza ODOO_URL para evitar errores de protocolo XML-RPC.
    Acepta valores con/sin comillas, con \\x3a y con/sin esquema.
    """
    raw_url = (os.getenv("ODOO_URL") or "").strip().strip('"').strip("'")
    raw_url = raw_url.replace("\\x3a", ":")

    if not raw_url:
        raise HTTPException(status_code=500, detail="ODOO_URL no está configurado")

    if not raw_url.startswith(("http://", "https://")):
        raw_url = f"https://{raw_url}"

    return raw_url.rstrip("/")

# Modelo para recibir datos en solicitudes POST
class Item(BaseModel):
    name: str
    price: float
    product_id: int
    is_offer: bool = None
class Product(BaseModel):
    name: str
    product_id: int
    price: float
    is_offer: bool = None
#getOdooProductPrices endpoint
#example data dict:
#array:1 [▼ // app\Http\Controllers\dashboard\Analytics.php:129
 # 0 => "7061"
#]
@app.post("/getOdooPrices")
async def get_odoo_product_prices(data: dict):
    """
    Recibe: {"ids": [id1, id2, ...], "partner_id": 123}
    - partner_id: ID del partner en Odoo, se usa para obtener su lista de precios actual
    - pricelist_id: (fallback) Si no se envía partner_id, se usa este valor directo
    Devuelve: {id1: {"id": id1, "price": precio, ...}, ...}
    """
    try:
        ids = data.get("ids", [])

        if not ids or not isinstance(ids, list):
            raise HTTPException(status_code=400, detail="Debes enviar un array de ids en 'ids'.")

        url = get_odoo_url()
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, admin_username, admin_password, {})
        if not uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        # Resolver pricelist_id: prioridad a partner_id (consulta en vivo desde Odoo)
        pricelist_id = None
        partner_id = data.get("partner_id")
        if partner_id is not None:
            partner_id = int(partner_id)
            partner_data = models.execute_kw(
                db, uid, admin_password,
                "res.partner", "read",
                [[partner_id]],
                {"fields": ["property_product_pricelist"]}
            )
            if partner_data and partner_data[0].get("property_product_pricelist"):
                ppl = partner_data[0]["property_product_pricelist"]
                pricelist_id = ppl[0] if isinstance(ppl, (list, tuple)) else int(ppl)

        # Fallback: usar pricelist_id directo si no se pudo resolver desde partner
        if pricelist_id is None:
            raw_pricelist = data.get("pricelist_id")
            if raw_pricelist is not None:
                pricelist_id = int(raw_pricelist)

        # Buscar todos los productos de una sola vez
        products = models.execute_kw(
            db, uid, admin_password,
            "product.product", "search_read",
            [[["id", "in", ids]]],
            {"fields": ["id", "name", "list_price", "product_tmpl_id", "standard_price"]}
        )

        result = {}
        for prod in products:
            product_id = prod["id"]
            if pricelist_id is None:
                precio_unitario = prod.get("list_price")
                result[product_id] = {"id": product_id, "price": precio_unitario, "name": prod.get("name")}
            else:
                final_price, debug = compute_pricelist_price(models, db, uid, admin_password, prod, pricelist_id)
                if final_price is None:
                    result[product_id] = {"id": product_id, "price": None, "error": debug.get("error"), "debug": debug}
                else:
                    result[product_id] = {"id": product_id, "price": round(final_price, 2), "name": prod.get("name"), "debug": debug}

        if not result:
            raise HTTPException(status_code=404, detail="No se encontraron productos con los IDs proporcionados")
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# Ruta GET
@app.get("/")
def read_root():
    return {"message": "Welcome to FastAPI on port 3036!"}

@app.get("/item/{item_name}")
def read_item(item_name: str):
    # Aquí puedes usar el parámetro item_name que toma el valor de 'BLACKOUT'
    #return odoo_tela_items(item_name)

    #obtener datos de un item por su nombre de product.product y de product.template
    try:
        # Obtener variables de entorno
        url = get_odoo_url()
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        # Conectar con el servidor XML-RPC
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, admin_username, admin_password, {})

        if not uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
        #item_name = item_name.strip()  # Eliminar espacios extra al inicio y al final del nombre del producto
        # Buscar el producto por nombre
        product_data = models.execute_kw(
            db, uid, admin_password,
            "product.product", "search_read",
            [[["name", "=", item_name]]],
            {"fields": ["id", "name", "list_price", "product_tmpl_id"], "limit": 1}
        )

        if not product_data:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        product_id = product_data[0]["id"]
        product_template_id = product_data[0]["product_tmpl_id"][0]  # Obtener el ID del template

        # Obtener datos del template
        template_data = models.execute_kw(
            db, uid, admin_password,
            "product.template", "read",
            [product_template_id],
            {"fields": ["id", "name", "list_price"]}
        )
         #  Obtener el precio en la lista de precios con `compute_price`
        pricelist_price = models.execute_kw(
            db, uid, admin_password,
            "product.pricelist.item", "search_read",
            [[["pricelist_id", "=", 1], ["product_tmpl_id", "=", product_id]]],
            {"fields": ["fixed_price"]}
        )

        # Si no hay precio específico en la lista de precios, usar `list_price`
        final_price = pricelist_price[0]["fixed_price"] if pricelist_price else product_data[0]["list_price"]

        return {
            "product": product_data[0],
            "template": template_data[0],
            "price_list_1": final_price
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


# Helper: calcular precio de pricelist para un producto (devuelve precio final y debug)
def compute_pricelist_price(models, db, admin_uid, admin_password, product_data, pricelist_id):
    """product_data debe contener: id, name, list_price, product_tmpl_id, standard_price
       Devuelve: (final_price, debug_dict)
    """
    try:
        list_price = product_data.get("list_price", 0)
        std_price = product_data.get("standard_price")
        try:
            std_price = float(std_price) if std_price is not None else None
        except Exception:
            std_price = None

        # Calcular precio Directo (desde coste)
        if std_price is not None:
            direct_from_cost = std_price / 0.65 if 0.65 != 0 else std_price
            if direct_from_cost < list_price:
                direct_price = list_price
                base_used = "list_price"
            else:
                direct_price = direct_from_cost
                base_used = "standard_price"
        else:
            direct_price = list_price
            base_used = "list_price"

        # Buscar regla específica product.product
        pricelist_item = models.execute_kw(
            db, admin_uid, admin_password,
            "product.pricelist.item", "search_read",
            [[
                ["pricelist_id", "=", pricelist_id],
                ["product_id", "=", product_data.get("id")]
            ]],
            {"fields": ["fixed_price", "percent_price", "price_discount", "compute_price"], "limit": 1}
        )
        # fallback a product.template
        if not pricelist_item and product_data.get("product_tmpl_id"):
            tmpl = product_data["product_tmpl_id"]
            tmpl_id = tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
            pricelist_item = models.execute_kw(
                db, admin_uid, admin_password,
                "product.pricelist.item", "search_read",
                [[
                    ["pricelist_id", "=", pricelist_id],
                    ["product_tmpl_id", "=", tmpl_id]
                ]],
                {"fields": ["fixed_price", "percent_price", "price_discount", "compute_price"], "limit": 1}
            )

        item = pricelist_item[0] if pricelist_item else None

        def _apply_pricelist_item_to_base(base, it):
            if it is None:
                return None, None
            if it.get("fixed_price") is not None:
                return it["fixed_price"], None
            if it.get("percent_price") is not None:
                pct = it.get("percent_price") or 0
                return base * (1 - (pct / 100.0)), pct
            if it.get("price_discount") is not None:
                disc = it.get("price_discount") or 0
                if disc > 1:
                    pct = disc
                    return base * (1 - (pct / 100.0)), pct
                else:
                    pct = disc * 100.0
                    return base * (1 - float(disc)), pct
            return None, None

        applied_pct = None
        applied_fixed = None
        final_price = list_price

        if pricelist_id in (1, 2, 4):
            defaults = {1: 0.0, 2: 7.0, 4: 19.0}
            if item:
                applied, pct = _apply_pricelist_item_to_base(direct_price, item)
                if applied is not None:
                    final_price = round(applied, 2)
                    applied_pct = pct
                    if item.get("fixed_price") is not None:
                        applied_fixed = item.get("fixed_price")
                else:
                    if item.get("compute_price") == "percentage" and item.get("percent_price") is not None:
                        pct = item.get("percent_price") or 0
                        final_price = round(direct_price * (1 - (pct/100.0)), 2)
                        applied_pct = pct
                    else:
                        final_price = round(direct_price * (1 - (defaults.get(pricelist_id, 0.0)/100.0)), 2)
            else:
                final_price = round(direct_price * (1 - (defaults.get(pricelist_id, 0.0)/100.0)), 2)
        else:
            # otras listas: aplicar regla si existe
            if item:
                applied, pct = _apply_pricelist_item_to_base(product_data.get("list_price", 0), item)
                if applied is not None:
                    final_price = round(applied, 2)
                    applied_pct = pct
                    if item.get("fixed_price") is not None:
                        applied_fixed = item.get("fixed_price")
                elif item.get("compute_price") == "percentage" and item.get("percent_price") is not None:
                    pct = item.get("percent_price") or 0
                    final_price = product_data.get("list_price", 0) * (1 - (pct / 100.0))
                    applied_pct = pct

        debug = {
            "base_used": base_used,
            "standard_price": std_price,
            "list_price": list_price,
            "direct_price_used": direct_price,
            "pricelist_id": pricelist_id,
            "applied_pricelist_item": True if (applied_fixed is not None or applied_pct is not None) else False,
            "applied_pct": applied_pct,
            "applied_fixed": applied_fixed
        }

        return final_price, debug
    except Exception as e:
        return None, {"error": str(e)}

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
        url = get_odoo_url()
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

        #  Obtener el `partner_id` del usuario autenticado y la imagen
        user_data = models.execute_kw(
            db, admin_uid, admin_password,
            "res.users", "read",
            [[uid]],
           {"fields": ["partner_id", "image_1920"]}
        )

        if not user_data or "partner_id" not in user_data[0]:
            raise HTTPException(status_code=404, detail="No se encontró el partner del usuario")

        partner_id = user_data[0]["partner_id"][0]  # Obtener el ID del partner


        #  Obtener la información del cliente (partner)
        cliente = models.execute_kw(
            db, admin_uid, admin_password,
            "res.partner", "read",
            [[partner_id]],  # Aquí sí pasamos el ID del partner en una lista
            {"fields": ["id", "name", "property_product_pricelist", "x_studio_configuracin_cotizador", "image_1920"]}
        )
        #return cliente

        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        return {
            "partner_id": cliente[0]["id"],
            "user_id": uid,
            "name": cliente[0]["name"],
            "price_list": cliente[0]["property_product_pricelist"],
            "config": cliente[0].get("x_studio_configuracin_cotizador", None),  # Usar get para evitar KeyError
            "user_image": cliente[0].get("image_1920", None)  # Usar get para evitar KeyError
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- SSO Autologin a Odoo (token JWT temporal) ---
AUTOLOGIN_ALGORITHM = "HS256"
AUTOLOGIN_EXP_MINUTES = 2


def _get_autologin_secret():
    secret = os.getenv("AUTOLOGIN_SECRET")
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="AUTOLOGIN_SECRET no configurado en el servidor",
        )
    return secret


def generar_token_autologin(login: str) -> str:
    """Genera un JWT de corta duración para autologin en Odoo (sin contraseña)."""
    secret = _get_autologin_secret()
    payload = {
        "login": login,
        "exp": datetime.utcnow() + timedelta(minutes=AUTOLOGIN_EXP_MINUTES),
    }
    return jwt.encode(payload, secret, algorithm=AUTOLOGIN_ALGORITHM)


class AutologinTokenRequest(BaseModel):
    """Solo para uso interno desde Laravel."""
    login: str


@app.post("/autologin-token")
async def autologin_token(data: AutologinTokenRequest):
    """
    Genera un token JWT para redirigir al usuario a Odoo sin volver a iniciar sesión.
    Laravel llama a este endpoint con el login (email) del usuario.
    """
    odoo_base_url = (os.getenv("ODOO_BASE_URL") or "").rstrip("/")
    if not odoo_base_url:
        raise HTTPException(
            status_code=500,
            detail="ODOO_BASE_URL no configurado en .env (URL pública de Odoo)",
        )

    token = generar_token_autologin(data.login)
    autologin_url = f"{odoo_base_url}/autologin?token={token}"
    return {
        "token": token,
        "autologin_url": autologin_url,
        "expires_minutes": AUTOLOGIN_EXP_MINUTES,
    }


class ValidateTokenRequest(BaseModel):
    token: str


@app.post("/validate-autologin-token")
async def validate_autologin_token(data: ValidateTokenRequest):
    """
    Valida un JWT de autologin (generado por Odoo o FastAPI).
    Usado por Laravel cuando recibe un token desde Odoo → Laravel.
    """
    secret = _get_autologin_secret()
    try:
        payload = jwt.decode(data.token, secret, algorithms=[AUTOLOGIN_ALGORITHM])
        login = payload.get("login")
        if not login:
            raise HTTPException(status_code=400, detail="Token sin campo login")
        return {"login": login, "valid": True}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")


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
        url = get_odoo_url()
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        #  Conectar con el servidor XML-RPC
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        admin_uid = common.authenticate(db, admin_username, admin_password, {})

        if not admin_uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        # Buscar producto y calcular precio usando helper
        product_data = models.execute_kw(
            db, admin_uid, admin_password,
            "product.product", "search_read",
            [[["id", "=", product_id]]],
            {"fields": ["id", "name", "list_price", "product_tmpl_id", "standard_price"], "limit": 1}
        )

        if not product_data:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        prod = product_data[0]
        final_price, debug = compute_pricelist_price(models, db, admin_uid, admin_password, prod, pricelist_id)

        if final_price is None:
            raise HTTPException(status_code=500, detail=f"Error calculando precio: {debug.get('error')}")

        return {
            "id": prod["id"],
            "name": prod["name"],
            "pricelist_price": round(final_price, 2) if isinstance(final_price, (int, float)) else final_price,
            "debug": debug
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
        url = get_odoo_url()
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

@app.post("/create-quotation-main/")
async def create_quotation_main(data: dict):
    try:
        # 🔹 Variables de entorno
        ODOO_URL = get_odoo_url()
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")

        # 🔹 Conectar con Odoo
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        # Valores seguros para evitar enviar None por XML-RPC
        partner_id = int(data.get("partner_id") or 1)
        pricelist_id = int(data.get("pricelist_id") or 1)

        # 🔹 Buscar término de pago "Pago inmediato" (fallback en inglés)
        immediate_payment_term = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
            [[["name", "ilike", "Pago inmediato"]]],
            {"limit": 1}
        )
        if not immediate_payment_term:
            immediate_payment_term = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
                [[["name", "ilike", "Immediate Payment"]]],
                {"limit": 1}
            )

        order_vals = {
            "partner_id": partner_id,
            "pricelist_id": pricelist_id,
            "partner_invoice_id": partner_id,
            "partner_shipping_id": partner_id,
        }
        if immediate_payment_term:
            order_vals["payment_term_id"] = immediate_payment_term[0]

        #🔹 Crear la cotización
        order_id = models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order", "create", [order_vals])

        if not order_id:
            raise HTTPException(status_code=500, detail="Error al crear la cotización")
        # order_id = 102 # Esto es un ejemplo, deberías usar el ID real de la cotización creada
        # 🔹 Crear líneas
        for line in data["order_lines"]:
            if line.get("type") == "note":
                models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                    "order_id": order_id,
                    "name": (line.get("description") or ""),
                    "display_type": "line_note"
                }])
            else:
                # Obtener UoM real del producto para evitar errores por product_uom fijo
                product_id = line.get("product_id")
                if product_id is None:
                    raise HTTPException(status_code=400, detail="Línea de producto sin product_id")
                product = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS, "product.product", "search_read",
                    [[["id", "=", int(product_id)]]],
                    {"fields": ["id", "uom_id"], "limit": 1}
                )
                if not product:
                    raise HTTPException(status_code=404, detail=f"Producto con ID {product_id} no encontrado")
                product_uom = product[0]["uom_id"][0] if product[0].get("uom_id") else 1

                models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                    "order_id": order_id,
                    "product_id": int(product_id),
                    "name": (line.get("description") or ""),
                    "product_uom_qty": float(line.get("quantity") or 0),
                    "price_unit": float(line.get("price_unit") or 0),
                    "product_uom": product_uom
                }])
        # 🔹 Leer totales de una cotizacion

        order_data = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "sale.order", "search_read",
            [[["id", "=", order_id]]],
            {"fields": ["amount_untaxed", "amount_total", "amount_tax"]}
        )

        data["subtotal"] = order_data[0]["amount_untaxed"]
        data["total"] = order_data[0]["amount_total"]
        data["taxes"] = order_data[0]["amount_tax"]

        return {
            "status": "success",
            "message": "Cotización creada con éxito con líneas personalizadas",
            "order_id": order_id,
            "subtotal": data.get("subtotal", 0),
            "total": data.get("total", 0),
            "taxes": data.get("taxes", 0)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create-quotation-products/")
async def create_quotation_products(data: dict):
    try:
        # 🔹 Variables de entorno
        ODOO_URL = get_odoo_url()
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")

        # 🔹 Conectar con Odoo
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        # Valores seguros para evitar enviar None por XML-RPC
        partner_id = int(data.get("partner_id") or 1)
        pricelist_id = int(data.get("pricelist_id") or 1)

        # 🔹 Buscar término de pago "Pago inmediato" (fallback en inglés)
        immediate_payment_term = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
            [[["name", "ilike", "Pago inmediato"]]],
            {"limit": 1}
        )
        if not immediate_payment_term:
            immediate_payment_term = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
                [[["name", "ilike", "Immediate Payment"]]],
                {"limit": 1}
            )

        order_vals = {
            "partner_id": partner_id,
            "pricelist_id": pricelist_id,
        }
        if immediate_payment_term:
            order_vals["payment_term_id"] = immediate_payment_term[0]

        # 🔹 Crear la cotización en `sale.order`
        order_id = models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order", "create", [order_vals])

        if not order_id:
            raise HTTPException(status_code=500, detail="Error al crear la cotización en Odoo")

        # 🔹 Agregar líneas de productos / notas
        for line in data["order_lines"]:
            if line.get("type") == "note":
                models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                    "order_id": order_id,
                    "name": (line.get("description") or ""),
                    "display_type": "line_note"
                }])
            else:
                product_id = line.get("product_id")
                if product_id is None:
                    raise HTTPException(status_code=400, detail="Línea de producto sin product_id")
                product = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS, "product.product", "search_read",
                    [[["id", "=", int(product_id)]]],
                    {"fields": ["id", "name", "uom_id"]}
                )

                if not product:
                    raise HTTPException(status_code=404, detail=f"Producto con ID {product_id} no encontrado")

                product_name = product[0]["name"]
                product_uom = product[0]["uom_id"][0]

                models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                    "order_id": order_id,
                    "product_id": int(product_id),
                    "name": (product_name or ""),
                    "product_uom_qty": float(line.get("quantity") or 0),
                    "product_uom": product_uom,
                    "price_unit": float(line.get("price_unit") or 0),
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
        ODOO_URL = get_odoo_url()
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

def _get_quotation_status_from_odoo(order_id: int):
    ODOO_URL = get_odoo_url()
    ODOO_DB = os.getenv("ODOO_DB")
    ODOO_USER = os.getenv("ADMIN_USER")
    ODOO_PASS = os.getenv("ADMIN_PASS")

    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
    if not uid:
        raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    order_data = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASS,
        "sale.order",
        "search_read",
        [[["id", "=", int(order_id)]]],
        {"fields": ["id", "name", "state"], "limit": 1},
    )

    if not order_data:
        raise HTTPException(status_code=404, detail=f"No existe la cotización {order_id} en Odoo")

    state = (order_data[0].get("state") or "").lower()
    label_map = {
        "draft": "en_revision",
        "sent": "enviada",
        "sale": "orden_venta",
        "cancel": "cancelada",
    }

    return {
        "order_id": int(order_data[0]["id"]),
        "name": order_data[0].get("name"),
        "state": state,
        "status": label_map.get(state, "en_revision"),
    }

@app.get("/quotation-status/{order_id}")
async def get_quotation_status(order_id: int):
    try:
        return _get_quotation_status_from_odoo(order_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/quotation-status")
async def post_quotation_status(data: dict):
    """
    Compatibilidad para clientes que envían JSON:
    {
      "order_id": 123
    }
    """
    try:
        order_id = data.get("order_id")
        if order_id is None:
            raise HTTPException(status_code=400, detail="Debes enviar 'order_id'")

        return _get_quotation_status_from_odoo(int(order_id))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create-contact/")
async def create_contact(contact: dict):
    try:
        # 🔹 Variables de entorno
        ODOO_URL = get_odoo_url()
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

        # 🔹 Buscar término de pago "Pago inmediato" (fallback a inglés)
        immediate_payment_term = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
            [[[["name", "ilike", "Pago inmediato"]]]],
            {"limit": 1}
        )
        if not immediate_payment_term:
            immediate_payment_term = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
                [[[["name", "ilike", "Immediate Payment"]]]],
                {"limit": 1}
            )

        # 🔹 Crear el nuevo contacto (incluyendo término de pago si se encontró)
        partner_vals = {
            'name': contact["name"],
            'email': contact["email"],
        }
        if immediate_payment_term:
            partner_vals['property_payment_term_id'] = immediate_payment_term[0]

        partner_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, 'res.partner', 'create',
            [partner_vals]
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
        url = get_odoo_url()
        db = os.getenv('ODOO_DB')
        admin_username = os.getenv('ADMIN_USER')
        admin_password = os.getenv('ADMIN_PASS')

        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, admin_username, admin_password, {})

        if not uid:
            raise HTTPException(status_code=500, detail="Error al autenticar admin")

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

        # Buscar productos activos y vendibles con display_name y product_variant_id
        products = models.execute_kw(
            db, uid, admin_password,
            'product.product', 'search_read',
            [[
                ['active', '=', True],
                ['sale_ok', '=', True],
                ['categ_id', '<>', 66],
                #["name", "ilike", 'RIEL DE ALUMINIO A TECHO']
            ]],
            {'fields': ['id', 'variant_seller_ids', 'product_template_variant_value_ids', 'standard_price', 'display_name', 'categ_id', 'list_price', 'product_variant_id', 'product_variant_ids', 'lst_price']}
        )
        #return products
        result = []
        # Leer los valores de variante específicos de cada producto y cruzarlos con product_template_attribute_value_ids
        for prod in products:
            # Usar display_name directamente, ya que incluye default_code, name y variantes
            display_name = prod.get('display_name', '')
            variant_value_ids = prod.get("product_variant_id", [])
            #variant_values_full = [variant_value_details[vid] for vid in variant_value_ids if vid in variant_value_details]
            result.append({
                "id": prod["id"],
                "name": display_name,
                "price": prod["list_price"],
                "list_price": prod["lst_price"],
                "categ_id": prod["categ_id"],
                "variant_values": variant_value_ids
            })

        # Agregar los diccionarios de mapeo al resultado general
        return result

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
        ODOO_URL = get_odoo_url()
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

        # Buscar el término de pago "Pago inmediato" en Odoo
        immediate_payment_term = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
            [[["name", "ilike", "Pago inmediato"]]],
            {"limit": 1}
        )
        # Fallback: buscar "Immediate Payment" (Odoo en inglés)
        if not immediate_payment_term:
            immediate_payment_term = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
                [[["name", "ilike", "Immediate Payment"]]],
                {"limit": 1}
            )

        # Crear contacto con término de pago inmediato
        partner_vals = {
            "name": data.name,
            "email": data.user_id,
            "customer_rank": 1
        }
        if immediate_payment_term:
            partner_vals["property_payment_term_id"] = immediate_payment_term[0]

        partner_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "res.partner", "create",
            [partner_vals]
        )

        # 👤 Crear usuario en Odoo
        # Para que el usuario sea "Portal" (cliente que puede cotizar y comprar), debe pertenecer al grupo Portal.
        # El ID del grupo Portal suele ser 9, pero es mejor buscarlo dinámicamente.
        portal_group = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "res.groups", "search",
            [[["category_id.name", "=", "User types"], ["name", "=", "Portal"]]]
        )
        user_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "res.users", "create",
            [{
            "name": data.name,
            "login": data.user_id,
            "email": data.user_id,
            "password": data.password,
            "partner_id": partner_id,
            "groups_id": [(6, 0, portal_group)]  # Asignar grupo Portal
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

@app.post("/update-quotation-main/")
async def update_quotation_main(data: dict):
    """
    Actualiza una cotización existente: elimina todas las líneas y agrega las nuevas líneas y comentario.
    Espera: {
        "order_id": int,
        "order_lines": [ ... ],  # igual que en create_quotation_1
    }
    """

    try:
        ODOO_URL = get_odoo_url()
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")

        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        order_id = data["order_id"]

        # Forzar término de pago "Pago inmediato" en cada actualización
        immediate_payment_term = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
            [[["name", "ilike", "Pago inmediato"]]],
            {"limit": 1}
        )
        if not immediate_payment_term:
            immediate_payment_term = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
                [[["name", "ilike", "Immediate Payment"]]],
                {"limit": 1}
            )
        if immediate_payment_term:
            models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "sale.order", "write",
                [[int(order_id)], {"payment_term_id": immediate_payment_term[0]}]
            )

        # 1. Buscar todas las líneas actuales de la cotización
        line_ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "sale.order.line", "search",
            [[["order_id", "=", order_id]]]
        )
        # 2. Eliminar todas las líneas existentes (deben ir como lista simple, no lista anidada)
        if line_ids:
            for lid in line_ids:
                models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS, "sale.order.line", "unlink",
                    [[lid]]
                )

        # 3. Crear nuevas líneas (igual que en create_quotation_1)
        for line in data["order_lines"]:
            if line.get("type") == "note":
                models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                    "order_id": order_id,
                    "name": (line.get("description") or ""),
                    "display_type": "line_note"
                }])
            else:
                # Obtener UoM real del producto para evitar errores por product_uom fijo
                product_id = line.get("product_id")
                if product_id is None:
                    raise HTTPException(status_code=400, detail="Línea de producto sin product_id")
                product = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS, "product.product", "search_read",
                    [[["id", "=", int(product_id)]]],
                    {"fields": ["id", "uom_id"], "limit": 1}
                )
                if not product:
                    raise HTTPException(status_code=404, detail=f"Producto con ID {product_id} no encontrado")
                product_uom = product[0]["uom_id"][0] if product[0].get("uom_id") else 1

                # Buscar el impuesto del 16% en Odoo (solo una vez)
                tax_ids = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS, "account.tax", "search",
                    [[["amount", "=", 16], ["type_tax_use", "in", ["sale", "all"]]]],
                    {"limit": 1}
                )
                models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                    "order_id": order_id,
                    "product_id": int(product_id),
                    "name": (line.get("description") or ""),
                    "product_uom_qty": float(line.get("quantity") or 0),
                    "price_unit": float(line.get("price_unit") or 0),
                    "product_uom": product_uom,
                    "tax_id": [[6, 0, tax_ids]] if tax_ids else []
                }])

        return {
            "status": "success",
            "message": "Cotización actualizada con éxito",
            "order_id": order_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update-quotation-products/")
async def update_quotation_products(data: dict):
    """
    Actualiza una cotización de productos: elimina todas las líneas y agrega las nuevas líneas de productos.
    Espera: {
        "order_id": int,
        "order_lines": [ ... ]  # igual que en create_quotation_products
    }
    """
    try:
        ODOO_URL = get_odoo_url()
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")

        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        order_id = data["order_id"]

        # Forzar término de pago "Pago inmediato" en cada actualización
        immediate_payment_term = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
            [[["name", "ilike", "Pago inmediato"]]],
            {"limit": 1}
        )
        if not immediate_payment_term:
            immediate_payment_term = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "account.payment.term", "search",
                [[["name", "ilike", "Immediate Payment"]]],
                {"limit": 1}
            )
        if immediate_payment_term:
            models.execute_kw(
                ODOO_DB, uid, ODOO_PASS, "sale.order", "write",
                [[int(order_id)], {"payment_term_id": immediate_payment_term[0]}]
            )

        # 1. Buscar todas las líneas actuales de la cotización
        line_ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS, "sale.order.line", "search",
            [[["order_id", "=", order_id]]]
        )
        # 2. Eliminar todas las líneas existentes (deben ir como lista simple, no lista anidada)
        if line_ids:
            for lid in line_ids:
                models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS, "sale.order.line", "unlink",
                    [[lid]]
                )

        # 3. Crear nuevas líneas de productos / notas (igual que en create_quotation_products)
        for line in data["order_lines"]:
            if line.get("type") == "note":
                models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                    "order_id": order_id,
                    "name": (line.get("description") or ""),
                    "display_type": "line_note"
                }])
            else:
                product_id = line.get("product_id")
                if product_id is None:
                    raise HTTPException(status_code=400, detail="Línea de producto sin product_id")
                product = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS, "product.product", "search_read",
                    [[["id", "=", int(product_id)]]],
                    {"fields": ["id", "name", "uom_id"]}
                )

                if not product:
                    raise HTTPException(status_code=404, detail=f"Producto con ID {product_id} no encontrado")

                product_name = product[0]["name"]
                product_uom = product[0]["uom_id"][0]

                models.execute_kw(ODOO_DB, uid, ODOO_PASS, "sale.order.line", "create", [{
                    "order_id": order_id,
                    "product_id": int(product_id),
                    "name": (product_name or ""),
                    "product_uom_qty": float(line.get("quantity") or 0),
                    "product_uom": product_uom,
                    "price_unit": float(line.get("price_unit") or 0),
                }])

        return {
            "status": "success",
            "message": "Cotización de productos actualizada con éxito",
            "order_id": order_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/products/by-category/")
async def get_products_by_category(data: dict):
    """
    Recibe: {"path_filter": "CORTINAS/SHADES/TELAS/BLACKOUT"}
    Devuelve: [{id, name, price}]
    Guarda la imagen en disco solo si no existe.
    """
    try:
        ODOO_URL = get_odoo_url()
        ODOO_DB = os.getenv("ODOO_DB")
        ODOO_USER = os.getenv("ADMIN_USER")
        ODOO_PASS = os.getenv("ADMIN_PASS")
        IMAGE_PATH = os.getenv("CATEG_IMAGE_PATH", "./images/") # Ruta para guardar imágenes de productos =

        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})

        if not uid:
            raise HTTPException(status_code=401, detail="Error de autenticación en Odoo")

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        path_filter = data.get("path_filter", "")
        if not path_filter:
            raise HTTPException(status_code=400, detail="Debes enviar el filtro en 'path_filter'.")

        # Obtener todas las categorías públicas y buscar la que coincide con el path
        categories = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS,
            'product.public.category', 'search_read',
            [[]],
            {'fields': ['id', 'name', 'parent_id']}
        )
        category_dict = {cat['id']: {'name': cat['name'], 'parent_id': cat['parent_id'][0] if cat['parent_id'] else None} for cat in categories}

        def build_path(category_id):
            path = []
            while category_id:
                category = category_dict[category_id]
                path.insert(0, category['name'])
                category_id = category['parent_id']
            return '/'.join(path)

        # Buscar el id de la categoría que coincide con el path_filter
        category_paths = {cat_id: build_path(cat_id) for cat_id in category_dict}
        category_id = None
        for cid, path in category_paths.items():
            if path_filter.strip() == path.strip():  # Comparación exacta del path completo
                category_id = cid
                break

        if not category_id:
            return []
        # return both
        #return {"0":category_paths , "1":category_id, "2":path_filter, "3":category_paths.get(category_id, "")}
        # Buscar productos publicados en esa categoría
        products = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS,
            'product.template', 'search_read',
            [[["website_published", "=", True], ["public_categ_ids", "in", [category_id]]]],
            {'fields': ['id', 'name', 'list_price', 'image_1920', 'attribute_line_ids', 'product_variant_ids']}
        )

        # Obtener product.product (variantes) desde los templates
        template_to_variants = {}
        all_variant_ids = []
        for p in products:
            variant_ids = p.get('product_variant_ids', [])
            template_to_variants[p['id']] = variant_ids
            all_variant_ids.extend(variant_ids)

        # Leer precios de variantes si existen
        variant_prices = {}
        if all_variant_ids:
            variants = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS,
                'product.product', 'read',
                [all_variant_ids],
                {'fields': ['id', 'list_price']}
            )
            for v in variants:
                variant_prices[v['id']] = v.get('list_price', 0)

        # Obtener todos los attribute_line_ids, luego leer líneas, atributos y valores para mapear nombres
        all_line_ids = []
        for p in products:
            all_line_ids.extend(p.get('attribute_line_ids', []))
        all_line_ids = list(set(all_line_ids))

        line_map = {}
        attr_names = {}
        value_names = {}

        if all_line_ids:
            lines = models.execute_kw(
                ODOO_DB, uid, ODOO_PASS,
                'product.template.attribute.line', 'read',
                [all_line_ids],
                {'fields': ['id', 'attribute_id', 'value_ids']}
            )
            # Recolectar ids de atributos y valores
            attr_ids = set()
            value_ids = set()
            for line in lines:
                line_map[line['id']] = line
                if line.get('attribute_id'):
                    # attribute_id puede venir como [id, name] o como id
                    aid = line['attribute_id'][0] if isinstance(line['attribute_id'], (list, tuple)) else line['attribute_id']
                    if aid:
                        attr_ids.add(aid)
                for vid in line.get('value_ids', []):
                    value_ids.add(vid)

            # Leer nombres de atributos
            if attr_ids:
                attrs = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS,
                    'product.attribute', 'read',
                    [list(attr_ids)],
                    {'fields': ['id', 'name']}
                )
                for a in attrs:
                    attr_names[a['id']] = a['name']

            # Leer nombres de valores
            if value_ids:
                vals = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS,
                    'product.attribute.value', 'read',
                    [list(value_ids)],
                    {'fields': ['id', 'name', 'attribute_id']}
                )
                for v in vals:
                    # attribute_id puede venir como tupla
                    value_names[v['id']] = {'name': v['name'], 'attribute_id': v.get('attribute_id')[0] if v.get('attribute_id') else None}

        os.makedirs(IMAGE_PATH, exist_ok=True)
        result = []

        for product in products:


            # Construir lista de atributos para este template
            attributes = []
            for lid in product.get('attribute_line_ids', []):
                line = line_map.get(lid)
                if not line:
                    continue
                raw_attr = line.get('attribute_id')
                attr_id = raw_attr[0] if isinstance(raw_attr, (list, tuple)) else raw_attr
                attr_name = attr_names.get(attr_id, None)
                values = []
                for vid in line.get('value_ids', []):
                    vinfo = value_names.get(vid, {})
                    values.append({"id": vid, "name": vinfo.get('name')})
                attributes.append({
                    "attribute_id": attr_id,
                    "attribute_name": attr_name,
                    "values": values
                })

            # Obtener variantes y usar precio de variante si existe, sino usar template price
            variant_ids = template_to_variants.get(product['id'], [])
            for var_id in variant_ids:
                result.append({
                    "id": var_id,  # Usar product.product ID (variante)
                    "template_id": product["id"],
                    "name": product["name"],
                    "price": variant_prices.get(var_id, product["list_price"]),
                    "attributes": attributes
                })

                # Guardar imagen con el ID de la variante
                image_name = f"{var_id}.png"
                image_path = os.path.join(IMAGE_PATH, image_name)
                if product.get('image_1920') and not os.path.isfile(image_path):
                    with open(image_path, "wb") as image_file:
                        image_file.write(base64.b64decode(product['image_1920']))

            # Si no hay variantes, devolver el template como fallback
            if not variant_ids:
                result.append({
                    "id": product["id"],
                    "template_id": product["id"],
                    "name": product["name"],
                    "price": product["list_price"],
                    "attributes": attributes
                })

                # Guardar imagen con el ID del template
                image_name = f"{product['id']}.png"
                image_path = os.path.join(IMAGE_PATH, image_name)
                if product.get('image_1920') and not os.path.isfile(image_path):
                    with open(image_path, "wb") as image_file:
                        image_file.write(base64.b64decode(product['image_1920']))

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
