import json
import os
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

router = APIRouter(
    prefix="/sat",
    tags=["SAT - Descarga Masiva"]
)

SAT_BASE_DIR = Path("/var/www/sat-iteknia")
SAT_DATA_DIR = SAT_BASE_DIR / "datos"
ENV_PATH = Path("/var/www/html/python_api/.env")
_LOCK_SAT = threading.Lock()


def _obtener_api_key() -> str:
    clave = os.getenv("SAT_API_KEY", "").strip().strip('"').strip("'")
    if clave:
        return clave
    if ENV_PATH.exists():
        for linea in ENV_PATH.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea.startswith("SAT_API_KEY="):
                return linea.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def verificar_api_key(x_api_key: str = Header(...)):
    clave_esperada = _obtener_api_key()
    if not clave_esperada or x_api_key != clave_esperada:
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
    """Devuelve el último reporte JSON de conciliación generado."""
    archivo_reporte = SAT_DATA_DIR / "reporte-marisol.json"
    if not archivo_reporte.exists():
        raise HTTPException(status_code=404, detail="Aún no se ha generado el reporte")
    return json.loads(archivo_reporte.read_text(encoding="utf-8"))


def _ejecutar_seguimiento_sat():
    """Recupera inciertos, reenvía solicitudes, descarga pendientes (seguir), extrae XML al NAS y actualiza conciliación."""
    if not _LOCK_SAT.acquire(blocking=False):
        return

    try:
        env = os.environ.copy()
        env["SAT_CERT_FILE"] = "/etc/sat-iteknia-credenciales/efirma.cer"
        env["SAT_KEY_FILE"] = "/etc/sat-iteknia-credenciales/efirma.key"
        env["SAT_PASSWORD_FILE"] = "/etc/sat-iteknia-credenciales/clave.txt"

        python_sat = SAT_BASE_DIR / ".venv" / "bin" / "python"
        script_sat = SAT_BASE_DIR / "sat_cli.py"
        script_extraer = SAT_BASE_DIR / "extraer_xml_nas.py"
        archivo_estado = SAT_DATA_DIR / "estado.json"
        archivo_reporte = SAT_DATA_DIR / "reporte-marisol.json"

        # 1. Detectar flujos en "incierto" (o ya marcados como "nuevo") para recuperarlos
        fechas_reenvio = set()
        if archivo_estado.exists():
            estado_data = json.loads(archivo_estado.read_text(encoding="utf-8"))
            for clave, info in estado_data.get("flujos", {}).items():
                fase = info.get("fase")
                if fase == "incierto":
                    cmd_rec = [str(python_sat), str(script_sat), "--datos", str(SAT_DATA_DIR), "recuperar"]
                    if info.get("id"):
                        cmd_rec.extend(["--id", info["id"], clave])
                    else:
                        cmd_rec.extend(["--autorizar-reenvio", clave])
                        fechas_reenvio.add((info["inicio"], info["fin"]))
                    subprocess.run(cmd_rec, cwd=str(SAT_BASE_DIR), env=env, check=False)
                elif fase == "nuevo":
                    fechas_reenvio.add((info["inicio"], info["fin"]))

        # 2. Volver a solicitar al SAT los flujos que pasaron a fase "nuevo"
        for inicio_f, fin_f in sorted(fechas_reenvio):
            subprocess.run(
                [
                    str(python_sat), str(script_sat), "--datos", str(SAT_DATA_DIR),
                    "solicitar", "--desde", inicio_f, "--hasta", fin_f
                ],
                cwd=str(SAT_BASE_DIR),
                env=env,
                check=False
            )

        ahora_mx = datetime.now(ZoneInfo("America/Mexico_City")).date()
        hoy_fiscal = (ahora_mx - timedelta(days=4)).isoformat()
        ayer_fiscal = (ahora_mx - timedelta(days=5)).isoformat()

        # 3. Consultar pendientes y descargar ZIPs listos
        subprocess.run(
            [str(python_sat), str(script_sat), "--datos", str(SAT_DATA_DIR), "seguir"],
            cwd=str(SAT_BASE_DIR),
            env=env,
            check=False
        )

        # 4. Extraer XML descargados al NAS (ayer, hoy y cualquier fecha recuperada)
        fechas_extraer = sorted({ayer_fiscal, hoy_fiscal} | {ini for ini, _ in fechas_reenvio})
        for fecha in fechas_extraer:
            subprocess.run(
                [str(python_sat), str(script_extraer), "--fecha", fecha],
                cwd=str(SAT_BASE_DIR),
                env=env,
                check=False
            )

        # 5. Regenerar reporte JSON de conciliación para el chatbot (sin enviar correo)
        subprocess.run(
            [
                str(python_sat), str(script_sat), "--datos", str(SAT_DATA_DIR),
                "reporte", "--desde", hoy_fiscal, "--hasta", hoy_fiscal,
                "--salida", str(archivo_reporte)
            ],
            cwd=str(SAT_BASE_DIR),
            env=env,
            check=False
        )
    finally:
        _LOCK_SAT.release()


@router.post("/seguir", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verificar_api_key)])
def disparar_seguimiento_manual(background_tasks: BackgroundTasks):
    """Lanza descarga, extracción al NAS y actualización de conciliación en segundo plano."""
    if _LOCK_SAT.locked():
        return {"mensaje": "Ya hay una descarga y actualización en curso en segundo plano"}
    background_tasks.add_task(_ejecutar_seguimiento_sat)
    return {"mensaje": "Descarga, extracción al NAS y actualización de reporte iniciadas en segundo plano"}