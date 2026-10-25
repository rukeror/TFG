"""
01 - Descarga de datos de Understat (LaLiga, temporadas 2018/19 - 2025/26).

Fuente : https://understat.com  (vía la librería soccerdata)
Salida : db/1_brutos/understat/*.csv  (junto a este script)
Caché  : ~/soccerdata/data/Understat  (respuestas en bruto; si el script se corta,
         al relanzarlo no se repiten las descargas)

Tablas que genera:
  - understat_jugadores_partido   : una fila por jugador y partido (minutos, goles,
                                    asistencias, tiros, xG, xA, xGChain, xGBuildup...)
  - understat_equipos_partido     : una fila por partido con stats de ambos equipos (xG, PPDA...)
  - understat_jugadores_temporada : totales por jugador y temporada
  - understat_calendario          : partidos, fechas y resultados
  - understat_tiros               : cada tiro con coordenadas, xG y situación

Uso: python 01_descarga_understat.py [--forzar]
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import soccerdata as sd

LIGA = "ESP-La Liga"
TEMPORADAS = ["1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526"]
DB = Path(__file__).resolve().parent / "db"   # carpeta db junto a este script
SALIDA = DB / "1_brutos" / "understat"
PARTIDOS_POR_TEMPORADA = 380


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--forzar", action="store_true", help="Rehace los CSV aunque ya existan")
    forzar = p.parse_args().forzar

    SALIDA.mkdir(parents=True, exist_ok=True)
    us = sd.Understat(leagues=LIGA, seasons=TEMPORADAS)
    tareas = {
        "understat_calendario": us.read_schedule,
        "understat_jugadores_partido": us.read_player_match_stats,
        "understat_equipos_partido": us.read_team_match_stats,
        "understat_jugadores_temporada": us.read_player_season_stats,
        "understat_tiros": us.read_shot_events,
    }

    for nombre, funcion in tareas.items():
        destino = SALIDA / f"{nombre}.csv"
        if destino.exists() and not forzar:
            print(f"  ya existe {destino}, se salta")
            continue
        print(f"-> {nombre}")
        df = funcion().reset_index()
        df["fecha_descarga"] = date.today().isoformat()
        df.to_csv(destino, index=False, encoding="utf-8-sig")
        print(f"   {len(df)} filas, {df.shape[1]} columnas")

    # Validación: 380 partidos por temporada
    print("\nValidación (partidos por temporada en el calendario):")
    import pandas as pd
    cal = pd.read_csv(SALIDA / "understat_calendario.csv")
    conteo = cal.groupby("season").size()
    for temporada, n in conteo.items():
        marca = "OK" if n == PARTIDOS_POR_TEMPORADA else "REVISAR"
        print(f"  {temporada}: {n} partidos  {marca}")


if __name__ == "__main__":
    main()