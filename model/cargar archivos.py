import pyodbc
import json
from dotenv import load_dotenv
import os

# Cargar las variables de entorno desde .env
load_dotenv()

# Conexión a SQL Server
conn = pyodbc.connect(    
    f"DRIVER={{ {os.getenv('DB_DRIVER')} }};"
    f"SERVER={os.getenv('DB_SERVER')};"
    f"DATABASE={os.getenv('DB_DATABASE')};"
    f"UID={os.getenv('DB_USER')};"
    f"PWD={os.getenv('DB_PASSWORD')};"
)
cursor = conn.cursor()

# Leer el archivo JSON
with open("SHEER.json", "r", encoding="utf-8") as f:
    items = json.load(f)

# Insertar los datos en la tabla
for item in items:
    name = item["name"].strip()
    image = item["image"]
    cursor.execute("INSERT INTO RPT_ODOO_CORTINAS (name, image, Tipo) VALUES (?, ?, ?)", (name, image, "sheer"))

# Confirmar los cambios y cerrar conexión
conn.commit()
conn.close()

print("Datos cargados correctamente.")
