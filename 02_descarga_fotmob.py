"""
02 - Descarga de estadísticas jugador-partido de FotMob (LaLiga, 2018/19 - 2025/26).

Fuente : https://www.fotmob.com  (descarga autorizada por FotMob para uso académico)
Método : endpoints públicos de la web, firmados con la cabecera x-mas que genera la
         librería fmscraper (MIT, no oficial). FotMob no publica una API oficial.
Salida : db/1_brutos/fotmob/fotmob_jugadores_partido_<temporada>.csv  (junto a este script)
Caché  : db/cache_fotmob/  (JSON en bruto de cada partido; si el script se corta,
         al relanzarlo no se repiten las descargas)

Pasos:
  1. Lista los partidos de cada temporada (LaLiga = id 87 en FotMob).
  2. Descarga el detalle de cada partido que no esté en caché o esté vacío/corrupto.
  3. Valida: 380 partidos por temporada y todos con estadísticas de jugador.
  4. Aplana las estadísticas a un CSV por temporada (una fila por jugador y partido).

Uso:
  python 02_descarga_fotmob.py
  python 02_descarga_fotmob.py --refrescar-calendario   # vuelve a pedir el calendario
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from fmscraper.xmas_generator import generate_xmas_header

# =============================================================================
# CONFIGURACIÓN
# =============================================================================
LIGA_ID = 87  # LaLiga en FotMob
TEMPORADAS = ["2018/2019", "2019/2020", "2020/2021", "2021/2022",
              "2022/2023", "2023/2024", "2024/2025", "2025/2026"]
PARTIDOS_POR_TEMPORADA = 380
PAUSA = 3.0  # segundos entre peticiones
DB = Path(__file__).resolve().parent / "db"   # carpeta db junto a este script
CACHE = DB / "cache_fotmob"
# Si aún no has movido la caché antigua (Estadística/cache_fotmob), se usa esa
_CACHE_ANTIGUA = Path(__file__).resolve().parents[2] / "cache_fotmob"
if not CACHE.exists() and _CACHE_ANTIGUA.exists():
    CACHE = _CACHE_ANTIGUA
SALIDA = DB / "1_brutos" / "fotmob"
API = "https://www.fotmob.com/api/data"
XMAS_URL_COMPLETA = False  # si da 401/403, probar con True
CABECERAS = {"User-Agent": "TFG-UAB-estadistica (uso academico autorizado)"}


# =============================================================================
# PETICIONES CON CACHÉ
# =============================================================================
def get_json(url: str, fichero: Path, reintentos: int = 3) -> dict:
    if fichero.exists():
        return json.loads(fichero.read_text(encoding="utf-8"))
    for intento in range(1, reintentos + 1):
        firma = url if XMAS_URL_COMPLETA else url.replace("https://www.fotmob.com", "")
        h = CABECERAS | {"x-mas": generate_xmas_header(firma)}
        r = requests.get(url, headers=h, timeout=30)
        time.sleep(PAUSA)
        if r.status_code == 200:
            fichero.parent.mkdir(parents=True, exist_ok=True)
            fichero.write_text(r.text, encoding="utf-8")
            return r.json()
        if r.status_code in (429, 503):
            espera = 60 * intento
            print(f"    {r.status_code}: demasiadas peticiones, espero {espera} s...")
            time.sleep(espera)
            continue
        r.raise_for_status()
    raise RuntimeError(f"No se pudo descargar {url}")


def partidos_temporada(temporada: str) -> list[dict]:
    url = f"{API}/leagues?id={LIGA_ID}&season={temporada.replace('/', '%2F')}"
    data = get_json(url, CACHE / f"liga_{temporada.replace('/', '-')}.json")
    bloque = data.get("fixtures") or data.get("matches")
    if not bloque or "allMatches" not in bloque:
        raise KeyError(f"No encuentro el calendario en el JSON. Claves: {list(data)}")
    return [m for m in bloque["allMatches"] if m.get("status", {}).get("finished", True)]


def json_valido(fichero: Path) -> bool:
    """El partido está bien descargado si el JSON se lee y trae stats de jugador."""
    if not fichero.exists():
        return False
    try:
        data = json.loads(fichero.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool((data.get("content") or {}).get("playerStats"))


# =============================================================================
# APLANADO DE ESTADÍSTICAS DE JUGADOR
# =============================================================================
def _valor(stat: dict):
    s = stat.get("stat", stat)
    return s.get("value"), s.get("total")


def aplanar_partido(match_id: int, data: dict, temporada: str) -> list[dict]:
    gen = data.get("general", {})
    base = {
        "season": temporada,
        "match_id": match_id,
        "date": gen.get("matchTimeUTCDate") or gen.get("matchTimeUTC"),
        "round": gen.get("matchRound"),
        "home": gen.get("homeTeam", {}).get("name"),
        "away": gen.get("awayTeam", {}).get("name"),
    }
    jugadores = (data.get("content", {}) or {}).get("playerStats") or {}
    filas = []
    for pid, p in jugadores.items():
        fila = base | {
            "player_id": p.get("id", pid),
            "player": p.get("name"),
            "team_id": p.get("teamId"),
            "team": p.get("teamName"),
            "is_goalkeeper": p.get("isGoalkeeper"),
        }
        for seccion in p.get("stats", []) or []:
            for nombre, stat in (seccion.get("stats") or {}).items():
                if not isinstance(stat, dict):
                    continue
                clave = stat.get("key") or nombre
                valor, total = _valor(stat)
                fila[clave] = valor
                if total is not None:  # p. ej. pases precisos 34 de 40
                    fila[f"{clave}_total"] = total
        filas.append(fila)
    return filas


# =============================================================================
def procesar_temporada(temporada: str, refrescar_calendario: bool) -> None:
    etiqueta = temporada.replace("/", "-")
    if refrescar_calendario:
        (CACHE / f"liga_{etiqueta}.json").unlink(missing_ok=True)

    partidos = partidos_temporada(temporada)
    ids = [int(m["id"]) for m in partidos]
    pendientes = [mid for mid in ids if not json_valido(CACHE / etiqueta / f"{mid}.json")]
    print(f"\n== {temporada}: {len(ids)} partidos, {len(pendientes)} por descargar")

    for i, mid in enumerate(pendientes, 1):
        fichero = CACHE / etiqueta / f"{mid}.json"
        fichero.unlink(missing_ok=True)  # borra JSON vacío o corrupto antes de reintentar
        try:
            get_json(f"{API}/matchDetails?matchId={mid}", fichero)
        except Exception as e:  # noqa: BLE001
            print(f"   FALLO partido {mid}: {e}")
        if i % 50 == 0:
            print(f"   {i}/{len(pendientes)}")

    # Validación
    sin_stats = [mid for mid in ids if not json_valido(CACHE / etiqueta / f"{mid}.json")]
    if len(ids) != PARTIDOS_POR_TEMPORADA:
        print(f"   REVISAR: {len(ids)} partidos en calendario (esperados {PARTIDOS_POR_TEMPORADA})."
              " Prueba con --refrescar-calendario")
    if sin_stats:
        print(f"   REVISAR: {len(sin_stats)} partidos sin stats de jugador: {sin_stats}")

    # Aplanado a CSV
    filas = []
    for mid in ids:
        f = CACHE / etiqueta / f"{mid}.json"
        if f.exists():
            filas += aplanar_partido(mid, json.loads(f.read_text(encoding="utf-8")), temporada)
    df = pd.DataFrame(filas)
    df["fecha_descarga"] = date.today().isoformat()
    df.to_csv(SALIDA / f"fotmob_jugadores_partido_{etiqueta}.csv", index=False)
    print(f"   -> {df['match_id'].nunique()} partidos, {len(df)} filas, {df.shape[1]} columnas")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--refrescar-calendario", action="store_true",
                   help="Vuelve a pedir el calendario a FotMob en vez de usar el de la caché")
    a = p.parse_args()

    SALIDA.mkdir(parents=True, exist_ok=True)
    for temporada in TEMPORADAS:
        procesar_temporada(temporada, a.refrescar_calendario)


if __name__ == "__main__":
    main()