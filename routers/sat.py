import json
import os
import subprocess
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

router = APIRouter(
    prefix="/sat",
    tags=["SAT - Descarga Masiva"]
)

SAT_BASE_DIR = Path("/var/www/sat-iteknia")
SAT_DATA_DIR = SAT_BASE_DIR / "datos"
SAT_API_KEY = os.getenv("SAT_API_KEY", "")


def verificar_api_key(x_api_key: str = Header(...)):
    if not SAT_API_KEY or x_api_key != SAT_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida o no configurada"
        )


@router.get("/estado", dependencies=[Depends(verificar_api_key)])
def obtener_estado():
    """Devuelve el estado de los últimos 3 periodos y cualquier flujo aún no concluido."""
    archivo_estado = SAT_DATA_DIR / "estado.json"
    if not archivo_estado.exists():
        raise HTTPException(status_code=404, detail="Aún no existe estado.json")

    data = json.loads(archivo_estado.read_text(encoding="utf-8"))
    flujos = data.get("flujos", {})

    periodos = sorted({clave.split("/")[0] for clave in flujos}, reverse=True)
    recientes = set(periodos[:3])

    flujos_filtrados = {}
    historico_listos = 0

    for clave, info in flujos.items():
        periodo = clave.split("/")[0]
        if periodo in recientes or info.get("fase") != "listo":
            flujos_filtrados[clave] = info
        else:
            historico_listos += 1

    return {
        "historico_flujos_listos": historico_listos,
        "flujos": flujos_filtrados,
    }


@router.get("/reporte", dependencies=[Depends(verificar_api_key)])
def obtener_reporte_diario():
    """Devuelve el último reporte JSON de conciliación generado a las 08:00."""
    archivo_reporte = SAT_DATA_DIR / "reporte-marisol.json"
    if not archivo_reporte.exists():
        raise HTTPException(status_code=404, detail="Aún no se ha generado el reporte")
    return json.loads(archivo_reporte.read_text(encoding="utf-8"))


def _ejecutar_seguimiento_sat():
    """Corre sat_cli.py seguir en su propio .venv sin tocar las dependencias de Odoo."""
    env = os.environ.copy()
    env["SAT_CERT_FILE"] = "/etc/sat-iteknia-credenciales/efirma.cer"
    env["SAT_KEY_FILE"] = "/etc/sat-iteknia-credenciales/efirma.key"
    env["SAT_PASSWORD_FILE"] = "/etc/sat-iteknia-credenciales/clave.txt"

    python_sat = SAT_BASE_DIR / ".venv" / "bin" / "python"
    script_sat = SAT_BASE_DIR / "sat_cli.py"

    subprocess.run(
        [str(python_sat), str(script_sat), "--datos", str(SAT_DATA_DIR), "seguir"],
        cwd=str(SAT_BASE_DIR),
        env=env,
        check=False
    )


@router.post("/seguir", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verificar_api_key)])
def disparar_seguimiento_manual(background_tasks: BackgroundTasks):
    """Lanza la descarga de paquetes pendientes en segundo plano sin bloquear la API."""
    background_tasks.add_task(_ejecutar_seguimiento_sat)
    return {"mensaje": "Seguimiento del SAT iniciado en segundo plano"}
