"""
04 - Variables de contexto de cada partido de LaLiga (2018/19 - 2025/26).

Para cada partido y cada equipo (local / visitante), con información conocida ANTES del partido:

  Bajas (FotMob, lista de no disponibles de cada partido, guardada en la caché del script 02)
    n_bajas, n_lesionados, n_sancionados
    bajas_peso      = minutos que los jugadores de baja jugaron en los 10 partidos de liga
                      anteriores del equipo / 900  ->  "nº de titulares equivalentes" que faltan
  Calendario (FotMob: LaLiga + Copa del Rey + Champions + Europa League + Conference League)
    descanso_dias   días desde el partido anterior del equipo en cualquier competición
    partidos_14d    partidos jugados en los 14 días anteriores (congestión)
    europa_previo   el partido anterior fue europeo y hace <= 4 días
    competicion_europea  champions / europa_league / conference_league / ninguna (esa temporada)
  Valor de mercado (Transfermarkt, en millones de euros; sale de las columnas tm_ de
  db/2_unido/jugador_partido.csv, que crea el script 03)
    valor_xi        suma del valor de los 11 titulares (Understat), con la última valoración
                    ANTERIOR al día del partido (sin fuga de datos); xi_sin_valor = titulares sin valor
    *_log_ratio     log(local / visitante)

Salida : db/2_unido/partidos.csv  se le añaden estas columnas (una fila por partido, us_game_id).
         Si se vuelve a ejecutar, sustituye las columnas de contexto que ya tuviera.
         db/1_brutos/fotmob/otras_competiciones.csv  partidos de copa y Europa de equipos de LaLiga

Necesita, en la misma carpeta: 02_descarga_fotmob.py (para la caché y las peticiones a FotMob)
y 03_unir_datos.py (para unificar nombres de equipos). Hay que ejecutar antes el 03.
Cada bloque es independiente: si FotMob no responde, el script sigue y lo indica
(p. ej. el descanso se calcula solo con partidos de liga).

Uso: python 04_variables_contexto.py [--sin-transfermarkt]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / "db"
BRUTOS, UNIDO = DB / "1_brutos", DB / "2_unido"
TEMPORADAS = ["2018/2019", "2019/2020", "2020/2021", "2021/2022",
              "2022/2023", "2023/2024", "2024/2025", "2025/2026"]
COMPETICIONES = {42: "champions", 73: "europa_league", 10216: "conference_league",
                 138: "copa_del_rey"}
EUROPA = ["champions", "europa_league", "conference_league"]   # en orden de prioridad
VENTANA_PESO = 10   # partidos de liga anteriores para medir la importancia de una baja


def cargar_modulo(nombre: str):
    spec = importlib.util.spec_from_file_location(nombre.replace(".py", ""), BASE / nombre)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ruta_cache() -> Path:
    """Misma lógica que 02: db/cache_fotmob o, si no existe, Estadística/cache_fotmob."""
    nueva, antigua = DB / "cache_fotmob", BASE.parents[1] / "cache_fotmob"
    return nueva if nueva.exists() or not antigua.exists() else antigua


def partidos_de_json(data: dict, comp: str) -> list[dict]:
    bloque = data.get("fixtures") or data.get("matches") or {}
    filas = []
    for m in bloque.get("allMatches", []):
        st = m.get("status", {})
        if st.get("cancelled") or not st.get("utcTime"):
            continue
        filas.append({"fm_match_id": int(m["id"]), "comp": comp,
                      "utc": pd.Timestamp(st["utcTime"]).tz_localize(None),
                      "home_id": int(m["home"]["id"]), "away_id": int(m["away"]["id"]),
                      "home_name": m["home"]["name"], "away_name": m["away"]["name"],
                      "jugado": bool(st.get("finished", False))})
    return filas


# =============================================================================
# 1) CALENDARIOS
# =============================================================================
def calendario_liga(cache: Path, unir) -> pd.DataFrame:
    """Partidos de liga de la caché de FotMob, enlazados con el us_game_id de Understat."""
    partidos = pd.read_csv(UNIDO / "partidos.csv")
    validos = set(partidos.home_team) | set(partidos.away_team)
    filas = []
    for t in TEMPORADAS:
        etiqueta = t.replace("/", "-")
        data = json.loads((cache / f"liga_{etiqueta}.json").read_text(encoding="utf-8"))
        for f in partidos_de_json(data, "laliga"):
            f["season"] = int(t[2:4] + t[7:9])
            f["etiqueta"] = etiqueta
            f["home_team"] = unir.norm_equipo(f["home_name"], validos)
            f["away_team"] = unir.norm_equipo(f["away_name"], validos)
            filas.append(f)
    liga = pd.DataFrame(filas)
    liga = liga.merge(partidos[["season", "home_team", "away_team", "us_game_id"]],
                      on=["season", "home_team", "away_team"], how="left")
    sin = liga.us_game_id.isna().sum()
    if sin:
        print(f"  Aviso: {sin} partidos de liga de FotMob sin pareja en Understat")
    return liga.dropna(subset=["us_game_id"]).astype({"us_game_id": int})


def calendario_otras(fm, ids_espanoles: set[int]) -> pd.DataFrame | None:
    """Copa del Rey y competiciones europeas desde FotMob (se guardan en la caché)."""
    if fm is None:
        return None
    filas, fallos = [], []
    for cid, comp in COMPETICIONES.items():
        for t in TEMPORADAS:
            if comp == "conference_league" and t < "2021/2022":
                continue  # la Conference League empezó en 2021/22
            url = f"{fm.API}/leagues?id={cid}&season={t.replace('/', '%2F')}"
            fichero = fm.CACHE / f"comp{cid}_{t.replace('/', '-')}.json"
            try:
                filas += partidos_de_json(fm.get_json(url, fichero), comp)
            except Exception as e:  # noqa: BLE001
                fallos.append(f"{comp} {t}: {e}")
    if fallos:
        print("  No se pudieron descargar:\n   " + "\n   ".join(fallos))
    if not filas:
        return None
    otras = pd.DataFrame(filas).drop_duplicates("fm_match_id")
    otras = otras[otras.home_id.isin(ids_espanoles) | otras.away_id.isin(ids_espanoles)]
    (BRUTOS / "fotmob").mkdir(parents=True, exist_ok=True)
    otras.to_csv(BRUTOS / "fotmob" / "otras_competiciones.csv", index=False)
    print(f"  Partidos de copa/Europa de equipos de LaLiga: {len(otras)} "
          f"({otras.comp.value_counts().to_dict()})")
    return otras


# =============================================================================
# 2) DESCANSO Y CONGESTIÓN
# =============================================================================
def variables_calendario(liga: pd.DataFrame, otras: pd.DataFrame | None) -> pd.DataFrame:
    todos = pd.concat([liga, otras], ignore_index=True) if otras is not None else liga
    todos = todos[todos.jugado]
    largo = pd.concat([
        todos[["fm_match_id", "comp", "utc", "home_id"]].rename(columns={"home_id": "team_id"}),
        todos[["fm_match_id", "comp", "utc", "away_id"]].rename(columns={"away_id": "team_id"}),
    ]).sort_values(["team_id", "utc"]).reset_index(drop=True)

    largo["prev_utc"] = largo.groupby("team_id").utc.shift()
    largo["prev_comp"] = largo.groupby("team_id").comp.shift()
    largo["descanso_dias"] = ((largo.utc - largo.prev_utc).dt.total_seconds() / 86400).round(1)
    largo.loc[largo.descanso_dias > 30, "descanso_dias"] = 30.0   # tope (pretemporada)
    # Primer partido de cada equipo en los datos (1ª jornada 2018/19): no hay partido anterior,
    # se trata como pretemporada, igual que la 1ª jornada de las demás temporadas
    largo["descanso_dias"] = largo.descanso_dias.fillna(30.0)
    largo["europa_previo"] = largo.prev_comp.isin(EUROPA) & (largo.descanso_dias <= 4)

    # partidos en los 14 días anteriores (sin contar el actual)
    def ventana(s: pd.Series) -> np.ndarray:
        t = s.values
        return np.arange(len(t)) - np.searchsorted(t, t - np.timedelta64(14, "D"), side="left")
    largo["partidos_14d"] = largo.groupby("team_id")["utc"].transform(ventana)

    # competición europea de la temporada (julio-junio)
    largo["temporada"] = largo.utc.dt.year - (largo.utc.dt.month < 7)
    eu = largo[largo.comp.isin(EUROPA)]
    nivel = (eu.assign(n=eu.comp.map({c: i for i, c in enumerate(EUROPA)}))
             .groupby(["team_id", "temporada"]).n.min().map(dict(enumerate(EUROPA))))
    largo["competicion_europea"] = [nivel.get((tid, t), "ninguna")
                                    for tid, t in zip(largo.team_id, largo.temporada)]
    cols = ["fm_match_id", "team_id", "descanso_dias", "partidos_14d", "europa_previo",
            "competicion_europea"]
    return largo[largo.comp == "laliga"][cols]


# =============================================================================
# 3) BAJAS
# =============================================================================
def variables_bajas(liga: pd.DataFrame, cache: Path) -> pd.DataFrame:
    fm = pd.concat([pd.read_csv(f, low_memory=False, usecols=["match_id", "date", "player_id",
                                                               "team_id", "minutes_played"])
                    for f in sorted((BRUTOS / "fotmob").glob("fotmob_jugadores_partido_*.csv"))])
    fm["date"] = pd.to_datetime(fm.date).dt.tz_localize(None)
    fm["minutes_played"] = fm.minutes_played.fillna(0)
    # minutos por equipo y partido: {team: [(fecha, {jugador: minutos}), ...]}
    hist = defaultdict(list)
    for (tid, mid, d), g in fm.groupby(["team_id", "match_id", "date"]):
        hist[int(tid)].append((d, dict(zip(g.player_id.astype(int), g.minutes_played))))
    for v in hist.values():
        v.sort(key=lambda x: x[0])

    def peso(tid: int, fecha, ids: list[int]) -> float:
        previos = [m for d, m in hist.get(tid, []) if d < fecha][-VENTANA_PESO:]
        if not previos or not ids:
            return 0.0
        return round(sum(m.get(i, 0) for m in previos for i in ids) / (90 * len(previos)), 2)

    filas = []
    for r in liga.itertuples():
        f = cache / r.etiqueta / f"{r.fm_match_id}.json"
        lineup = {}
        if f.exists():
            try:
                lineup = (json.loads(f.read_text(encoding="utf-8")).get("content") or {}) \
                    .get("lineup") or {}
            except json.JSONDecodeError:
                pass
        for lado, tid in (("homeTeam", r.home_id), ("awayTeam", r.away_id)):
            unav = (lineup.get(lado) or {}).get("unavailable") or []
            tipos = [(u.get("unavailability") or {}).get("type") for u in unav]
            ids = [int(u["id"]) for u in unav if u.get("id")]
            filas.append({"fm_match_id": r.fm_match_id, "team_id": tid,
                          "n_bajas": len(unav), "n_lesionados": tipos.count("injury"),
                          "n_sancionados": tipos.count("suspension"),
                          "bajas_peso": peso(tid, r.utc, ids),
                          "bajas_info": bool(lineup)})
    return pd.DataFrame(filas)


# =============================================================================
# 4) VALOR DE MERCADO (Transfermarkt, ya unido a jugador_partido por el script 03)
# =============================================================================
def valor_mercado(partidos: pd.DataFrame) -> pd.DataFrame | None:
    """Valor de mercado de local y visitante, en millones de euros.
    valor_xi        suma de tm_valor_mercado de los 11 titulares (Understat: position != 'Sub').
                    tm_valor_mercado es la última valoración ANTERIOR al día del partido."""
    jp = pd.read_csv(UNIDO / "jugador_partido.csv", low_memory=False,
                     usecols=lambda c: c in {"season", "game_id", "team", "player_id", "position",
                                             "tm_valor_mercado"})
    if "tm_valor_mercado" not in jp.columns:
        print("  jugador_partido.csv no tiene tm_valor_mercado: ejecuta antes 03_unir_datos.py")
        return None
    xi = jp[jp.position != "Sub"]
    por_eq = (xi.groupby(["game_id", "team"])
                .agg(valor_xi=("tm_valor_mercado", lambda s: s.sum(min_count=1)),
                     xi_sin_valor=("tm_valor_mercado", lambda s: int(s.isna().sum())))
                .reset_index().rename(columns={"game_id": "us_game_id"}))
    out = partidos[["us_game_id", "season", "home_team", "away_team"]]
    for lado in ("home", "away"):
        out = out.merge(por_eq.rename(columns={"team": f"{lado}_team", "valor_xi": f"valor_xi_{lado}",
                                               "xi_sin_valor": f"xi_sin_valor_{lado}"}),
                        on=["us_game_id", f"{lado}_team"], how="left")
    print(f"  Titulares sin valor de mercado: "
          f"{int(out.xi_sin_valor_home.sum() + out.xi_sin_valor_away.sum())} de {len(xi)}")
    return out.drop(columns=["season", "home_team", "away_team"]).round(2)


# =============================================================================
# 5) GUARDAR: las variables de contexto se añaden a partidos.csv
# =============================================================================
# Columnas que no se añaden: ya están en partidos.csv (season, equipos, fm_match_id; la hora
# es la de 'date') o son de control (fuente del descanso y si FotMob tenía la lista de bajas).
NO_GUARDAR = ["season", "utc", "home_team", "away_team", "fm_match_id",
              "fuente_descanso", "bajas_info_home", "bajas_info_away"]
# Columnas que versiones anteriores del 04 añadían y ya no se calculan: se quitan al re-ejecutar
OBSOLETAS = ["valor_plantilla_home", "valor_plantilla_away", "valor_plantilla_log_ratio"]


def guardar_en_partidos(out: pd.DataFrame) -> None:
    p = pd.read_csv(UNIDO / "partidos.csv")
    nuevas = [c for c in out.columns if c not in NO_GUARDAR and c != "us_game_id"]
    p = p.drop(columns=[c for c in nuevas + OBSOLETAS if c in p.columns])  # si se re-ejecuta el 04
    n = len(p)
    p = p.merge(out[["us_game_id"] + nuevas], on="us_game_id", how="left", validate="1:1")
    if len(p) != n or p[nuevas[0]].isna().all():
        raise ValueError("No se han podido enlazar las variables de contexto con partidos.csv")
    p.to_csv(UNIDO / "partidos.csv", index=False)
    print(f"  Añadidas {len(nuevas)} columnas de contexto a partidos.csv")


# =============================================================================
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sin-transfermarkt", action="store_true")
    a = p.parse_args()

    unir = cargar_modulo("03_unir_datos.py")
    try:
        fm = cargar_modulo("02_descarga_fotmob.py")
        cache = fm.CACHE
    except Exception as e:  # noqa: BLE001  (p. ej. falta fmscraper)
        print(f"No se puede usar FotMob en línea ({e}); se usa solo la caché.")
        fm, cache = None, ruta_cache()

    print("1) Calendarios")
    liga = calendario_liga(cache, unir)
    ids_es = set(liga.home_id) | set(liga.away_id)
    otras = calendario_otras(fm, ids_es)

    print("2) Descanso y congestión")
    cal = variables_calendario(liga, otras)
    print("3) Bajas")
    bajas = variables_bajas(liga, cache)

    por_equipo = cal.merge(bajas, on=["fm_match_id", "team_id"], how="outer")
    out = liga[["us_game_id", "season", "utc", "home_team", "away_team",
                "fm_match_id", "home_id", "away_id"]].copy()
    for lado in ("home", "away"):
        x = por_equipo.rename(columns={"team_id": f"{lado}_id"})
        x = x.rename(columns={c: f"{c}_{lado}" for c in x.columns
                              if c not in ("fm_match_id", f"{lado}_id")})
        out = out.merge(x, on=["fm_match_id", f"{lado}_id"], how="left")
    out["descanso_diff"] = out.descanso_dias_home - out.descanso_dias_away
    out["bajas_peso_diff"] = out.bajas_peso_home - out.bajas_peso_away
    out["fuente_descanso"] = "liga_copa_europa" if otras is not None else "solo_liga"

    if not a.sin_transfermarkt:
        print("4) Valor de mercado (Transfermarkt)")
        vm = valor_mercado(pd.read_csv(UNIDO / "partidos.csv"))
        if vm is not None:
            out = out.merge(vm, on="us_game_id", how="left")
            out["valor_xi_log_ratio"] = np.log(out.valor_xi_home / out.valor_xi_away).round(3)

    out = out.drop(columns=["home_id", "away_id"]).sort_values("utc")
    if not out.filter(like="bajas_info").all().all():
        print("  AVISO: hay partidos sin lista de bajas en FotMob (bajas = 0 en esos partidos)")
    guardar_en_partidos(out)

    # Validación
    print(f"\n{len(out)} partidos -> {UNIDO / 'partidos.csv'}  (descanso: {out.fuente_descanso.iloc[0]})")
    resumen = out.groupby("season").agg(
        descanso_medio=("descanso_dias_home", "mean"),
        bajas_medias=("n_bajas_home", "mean"),
        partidos_con_bajas=("n_bajas_home", lambda s: (s > 0).mean()),
        europa=("competicion_europea_home", lambda s: (s != "ninguna").mean()))
    print(resumen.round(2).to_string())
    nulos = out.isna().mean()
    print("\nColumnas con valores vacíos:\n" + nulos[nulos > 0].round(3).to_string()
          if (nulos > 0).any() else "\nSin valores vacíos")


if __name__ == "__main__":
    main()
