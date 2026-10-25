"""
03 - Une todas las fuentes de LaLiga 2018/19 - 2025/26.

Entrada : db/1_brutos/understat/   understat_jugadores_partido.csv, understat_equipos_partido.csv
          db/1_brutos/fotmob/      fotmob_jugadores_partido_*.csv
          db/1_brutos/elo/         elo_clubes_espana.csv (se descarga si no existe)
          db/1_brutos/resultados_cuotas/   un CSV por temporada de football-data.co.uk
          db/1_brutos/transfermarkt/       player_valuations.csv, players.csv
                                           + valoraciones_web.csv (historial descargado de
                                           transfermarkt.es para los jugadores a los que les falta;
                                           el 03 descarga lo que falte salvo con --sin-descarga-tm)
                                           (también vale la carpeta 'transfermarket')
Salida  : db/2_unido/jugador_partido.csv   una fila por jugador y partido (solo quien jugó):
                                           Understat + FotMob (fm_) + Transfermarkt (tm_)
          db/2_unido/partidos.csv          una fila por partido: ids de Understat y FotMob,
                                           resultado, estadísticas de equipo de Understat y de
                                           football-data (descanso, tiros, faltas, córners,
                                           tarjetas) y ELO
          Con --auxiliares, además, ficheros de control en db/2_unido/auxiliares/:
            equivalencias_equipos.csv         nombre de cada equipo en cada fuente
            equivalencias_jugadores.csv       id Understat <-> id FotMob
            equivalencias_transfermarkt.csv   id Understat <-> id Transfermarkt
            revisar_emparejamientos.csv       dudosos Understat-FotMob
            revisar_transfermarkt.csv         dudosos y sin pareja en Transfermarkt

Fuentes:
  Understat (soccerdata) y FotMob (con permiso, fmscraper): scripts 01 y 02.
  ELO: "Club Football Match Data (2000 - 2025)", Adam Gábor (2026), licencia MIT.
       https://github.com/xgabora/Club-Football-Match-Data-2000-2025 (data/EloRatings.csv).
       Instantáneas de ClubElo los días 1 y 15 de cada mes; desde el 15/06/2025 son una
       continuación provisional calculada por el autor con el mismo método.
  football-data.co.uk: resultado al descanso y estadísticas del partido (las cuotas no se usan).
  Transfermarkt: "transfermarkt-datasets", David Cariboo (CC0), Kaggle davidcariboo/player-scores,
       versión 679 (05/09/2026); valoraciones hasta el 12/06/2026.

Sin fuga de datos: el ELO y el valor de mercado son los últimos valores con fecha
ESTRICTAMENTE anterior al día del partido.

Método:
  1. Partidos: se emparejan por temporada + equipo local + equipo visitante
     (combinación única dentro de una temporada), tras unificar nombres de equipo.
     No se usa la fecha porque Understat y FotMob guardan horas en zonas distintas.
  2. Jugadores, dentro de cada partido y equipo:
       puntuación = similitud del nombre (0-1) + 0,3 x parecido de minutos jugados
     y emparejamiento voraz (de mayor a menor puntuación, cada jugador una sola vez).
  3. Consolidación: cada id de Understat se asocia al id de FotMob con el que se
     ha emparejado más veces (voto mayoritario). Los nombres distintos que se repiten
     en varios partidos se aceptan como apodos ('Chicharito' / 'Javier Hernández');
     los poco repetidos se marcan como dudosos y los que contradicen la pareja
     habitual del jugador no se unen.
  Limpieza: se eliminan filas duplicadas de Understat y los jugadores de FotMob con
  id 0 se identifican por su nombre.

  Transfermarkt: ver emparejar_transfermarkt().

Solo usa pandas, numpy y la librería estándar.
Uso: python 03_unir_datos.py [--forzar-elo]
"""

from __future__ import annotations

import argparse
import glob
import re
import unicodedata
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd

UMBRAL_NOMBRE = 0.55   # por debajo, el emparejamiento se considera dudoso
PESO_MINUTOS = 0.3
MIN_REPETICIONES = 3   # veces que debe repetirse una pareja con nombre distinto para aceptarla

# Revisión manual de los casos dudosos (06/10/2026): (id Understat, clave FotMob) -> se aceptan.
# Criterio: único jugador libre en ambas fuentes para ese equipo y partido, con minutos parecidos.
CORRECCIONES_MANUALES = {
    (14586, "1842315"): "Cubo = Miguel Llorente (Atlético, 2025/26)",
    (10952, "nombre:casals"): "Joel Roca = Casals (Joel Roca Casals; FotMob sin id en 2022/23)",
    (9173, "1094493"): "Lautaro de León = Lauti (Celta, 2020/21)",
    (8525, "971643"): "Álex Blesa = Alejandro Blesa (FotMob le da dos ids)",
}
# Understat da dos ids a un mismo jugador (nunca coinciden en un partido y tienen la misma ficha
# de Transfermarkt). Se unifican al cargar: id duplicado -> id que se conserva (el de más minutos).
UNIFICAR_US = {
    11471: 11328,    # Ángel Alarcón (Barcelona 2022/23)
    13276: 13294,    # Álvaro Pascual -> García Pascual (Álvaro García Pascual, Sevilla 2024/25)
}

# Columnas redundantes que no se guardan en db/2_unido (los datos completos siguen en db/1_brutos).
# Comprobado el 10/10/2026 sobre las 92.532 filas: cada una es copia de otra o suma de otras.
QUITAR_JUGADOR = [
    "league", "league_id", "fecha_descarga",   # constantes
    "season_id",                                # = season
    "position_id",                              # = position (codificada)
    "fm_match_id", "fm_team_id",                # ya están en partidos.csv / equivalencias_equipos.csv
    "fm_minutes_played",                        # = minutes (FotMob suma el añadido; Understat corta en 90)
    "fm_goals", "fm_assists",                   # = goals, assists de Understat (99,97 %)
    "fm_total_shots",                           # = ShotsOnTarget + ShotsOffTarget + blocked_shots (≈ shots)
    "fm_shot_accuracy",                         # = fm_ShotsOnTarget
    "fm_shot_accuracy_total",                   # = fm_ShotsOnTarget + fm_ShotsOffTarget
    "fm_duel_won",                              # = ground_duels_won + aerials_won
    "fm_duel_lost",                             # = intentos - ganados de los dos tipos de duelo
    "fm_defensive_actions",                     # = tackles + interceptions + clearances + shot_blocks
    "fm_xg_and_xa",                             # = fm_expected_goals + fm_expected_assists
    "fm_Shotmap",                               # indicador interno de FotMob (≈ tiró o no)
    # Medido en Understat y en FotMob: se queda la mejor versión (comprobado el 10/10/2026)
    "fm_expected_goals",                        # xG: el de Understat cubre las 8 temporadas (FotMob desde 20/21)
    "fm_expected_goals_non_penalty",            # npxG: se calcula con los tiros de Understat (np_xg)
    "fm_expected_assists",                      # xA: el de Understat cubre las 8 temporadas
    "fm_chances_created",                       # = key_passes de Understat (99,2 %)
    "fm_ShotsOffTarget",                        # = shots - fm_ShotsOnTarget - fm_blocked_shots
    "fm_owngoal",                               # = own_goals de Understat (100 %)
    "fm_is_goalkeeper",                         # = tm_posicion == "Goalkeeper" (100 %)
    "fm_goals_prevented",                       # = xGOT recibido - goles encajados (y FotMob solo lo da en 1 de cada 4 partidos)
]

# FotMob deja vacías las estadísticas de conteo que valen 0. Se rellenan con 0 (solo en las filas
# con datos de FotMob), salvo donde el vacío no es un 0:
FM_SIN_RELLENAR = ["fm_player_id", "fm_player", "fm_rating_title"]      # sin nota != nota 0
FM_SOLO_PORTERO = ["fm_goals_conceded", "fm_expected_goals_on_target_faced"]  # no aplican a jugadores de campo
FM_DESDE_2021 = ["fm_expected_goals_on_target_variant", "fm_expected_goals_on_target_faced"]  # antes de la
# 20/21 FotMob casi no los tiene (solo 2 partidos de la 19/20): ahí se dejan tal cual vienen
# football-data.co.uk: solo se usan el resultado al descanso y las estadísticas del partido, que
# se añaden a partidos.csv. Las cuotas de las casas de apuestas no se usan (no son objeto del TFG;
# siguen en db/1_brutos/resultados_cuotas) y el resultado final ya viene de Understat.
ESTADISTICAS_FD = {
    "HTHG": "home_goals_ht", "HTAG": "away_goals_ht",           # goles al descanso
    "HS": "home_shots", "AS": "away_shots",                     # tiros
    "HST": "home_shots_on_target", "AST": "away_shots_on_target",
    "HF": "home_fouls", "AF": "away_fouls",                     # faltas cometidas
    "HC": "home_corners", "AC": "away_corners",
    "HY": "home_yellow_cards", "AY": "away_yellow_cards",
    "HR": "home_red_cards", "AR": "away_red_cards",
}

# Descartado a propósito: Elche-Cádiz 04/06/2023, Understat pone a Álex Collado (1 min) y
# FotMob a Ezequiel Ponce (6 min). Las fuentes no coinciden en quién jugó: no se une.

# Nombres de equipo de FotMob -> nombres de Understat (tras quitar acentos)
EQUIPOS = {
    "alaves": "Alaves", "deportivo alaves": "Alaves",
    "atletico madrid": "Atletico Madrid", "atletico de madrid": "Atletico Madrid",
    "celta de vigo": "Celta Vigo",
    "granada cf": "Granada",
    "huesca": "SD Huesca",
    "valencia cf": "Valencia",
}


# =============================================================================
# NORMALIZACIÓN
# =============================================================================
def sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c))


def norm_equipo(nombre: str, validos: set[str]) -> str:
    base = sin_acentos(nombre).strip()
    if base in validos:
        return base
    return EQUIPOS.get(base.lower(), base)


def norm_nombre(s: str) -> str:
    s = sin_acentos(s).lower()
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similitud(a: str, b: str) -> float:
    """Combina parecido de cadena y solapamiento de palabras.
    Cubre 'Bernardo' vs 'Bernardo Espinosa' y 'Vinicius Junior' vs 'Vinicius Jr'."""
    a, b = norm_nombre(a), norm_nombre(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    contenido = len(ta & tb) / min(len(ta), len(tb))       # palabras del corto en el largo
    cadena = SequenceMatcher(None, a, b).ratio()
    # apellido: última palabra igual o prefijo (abreviaturas)
    ua, ub = a.split()[-1], b.split()[-1]
    apellido = 1.0 if (ua == ub or (len(ua) > 3 and len(ub) > 3 and (ua.startswith(ub) or ub.startswith(ua)))) else 0.0
    return max(cadena, 0.9 * contenido, 0.75 * apellido)


# =============================================================================
# CARGA
# =============================================================================
def cargar(datos: Path):
    us = pd.read_csv(datos / "understat/understat_jugadores_partido.csv")
    us_eq = pd.read_csv(datos / "understat/understat_equipos_partido.csv")
    fm = pd.concat([pd.read_csv(f, low_memory=False)
                    for f in sorted(glob.glob(str(datos / "fotmob/fotmob_jugadores_partido_*.csv")))],
                   ignore_index=True)

    validos = set(us_eq.home_team) | set(us_eq.away_team)
    fm["season"] = fm["season"].str.replace(r"^20(\d\d)/20(\d\d)$", r"\1\2", regex=True).astype(int)
    for col in ("home", "away", "team"):
        fm[col] = fm[col].map(lambda x: norm_equipo(x, validos) if pd.notna(x) else x)

    sin_mapear = (set(fm.home) | set(fm.away) | set(fm.team.dropna())) - validos
    if sin_mapear:
        raise ValueError(f"Equipos de FotMob sin equivalencia en Understat: {sin_mapear}. "
                         "Añádelos al diccionario EQUIPOS.")

    # Understat duplica las filas de algunos partidos: se queda una por jugador y partido
    n0 = len(us)
    us = us.drop_duplicates(["game_id", "player_id"]).reset_index(drop=True)
    if len(us) < n0:
        print(f"Understat: eliminadas {n0 - len(us)} filas duplicadas (mismo jugador y partido)")

    # Understat: un mismo jugador con dos ids -> un solo id (y el nombre del id que se conserva)
    dup = us.player_id.isin(list(UNIFICAR_US))
    if dup.any():
        nombre = us[us.player_id.isin(list(UNIFICAR_US.values()))].groupby("player_id").player.first()
        us.loc[dup, "player_id"] = us.loc[dup, "player_id"].map(UNIFICAR_US)
        afect = us.player_id.isin(nombre.index)
        us.loc[afect, "player"] = us.loc[afect, "player_id"].map(nombre)
        if us.duplicated(["game_id", "player_id"]).any():
            raise ValueError("UNIFICAR_US junta dos ids que coinciden en un mismo partido")
        print(f"Understat: unificados {len(UNIFICAR_US)} jugadores con dos ids ({dup.sum()} filas)")

    # FotMob: jugadores cuyo equipo no es ninguno de los dos del partido (error de la fuente)
    raro = fm[fm.team.notna() & (fm.team != fm.home) & (fm.team != fm.away)]
    if len(raro):
        print("FotMob: partidos con jugadores de un equipo que no juega ese partido:")
        for (h, a, d, t), g in raro.groupby(["home", "away", "date", "team"]):
            print(f"   {d[:10]} {h}-{a}: {len(g)} jugadores con equipo '{t}'")
        # Si a ese partido le falta uno de sus dos equipos, el equipo extraño es el que falta
        # (Espanyol-Levante 20/06/2020: FotMob pone a los jugadores del Levante como 'Leganés').
        # Los suplentes del otro equipo que vengan mezclados no se emparejan con nadie.
        for mid, g in raro.groupby("match_id"):
            p = fm[fm.match_id == mid]
            falta = {p.home.iat[0], p.away.iat[0]} - set(p.team.dropna())
            if len(falta) == 1:
                fm.loc[g.index, "team"] = (equipo := falta.pop())
                print(f"   -> se asignan a {equipo}")
    return us, us_eq, fm


# =============================================================================
# 1) PARTIDOS
# =============================================================================
def emparejar_partidos(us_eq: pd.DataFrame, fm: pd.DataFrame) -> pd.DataFrame:
    p_us = us_eq[["season", "game_id", "date", "home_team", "away_team",
                  "home_goals", "away_goals"]].rename(columns={"game_id": "us_game_id"})
    p_fm = (fm[["season", "match_id", "home", "away", "round"]].drop_duplicates("match_id")
            .rename(columns={"match_id": "fm_match_id", "home": "home_team", "away": "away_team"}))
    partidos = p_us.merge(p_fm, on=["season", "home_team", "away_team"], how="outer", indicator=True)
    partidos["origen"] = partidos.pop("_merge").map(
        {"both": "ambas", "left_only": "solo_understat", "right_only": "solo_fotmob"})
    return partidos


# =============================================================================
# 2) JUGADORES
# =============================================================================
def emparejar_grupo(a: pd.DataFrame, b: pd.DataFrame) -> list[tuple]:
    """a: jugadores Understat de un equipo en un partido; b: los de FotMob."""
    pares = []
    for ia, ra in a.iterrows():
        for ib, rb in b.iterrows():
            s = similitud(ra.player, rb.player)
            m = rb.minutes_played
            sm = 0.0 if pd.isna(m) else max(0.0, 1 - abs(ra.minutes - m) / 45)
            pares.append((s + PESO_MINUTOS * sm, s, ia, ib))
    pares.sort(reverse=True)
    usados_a, usados_b, res = set(), set(), []
    for total, s, ia, ib in pares:
        if ia in usados_a or ib in usados_b:
            continue
        usados_a.add(ia); usados_b.add(ib)
        res.append((ia, ib, round(s, 3)))
    return res


def emparejar_jugadores(us, fm, partidos):
    ok = partidos[partidos.origen == "ambas"][["us_game_id", "fm_match_id"]]
    us = us.merge(ok, left_on="game_id", right_on="us_game_id", how="left")
    # Solo jugadores de FotMob que han jugado (FotMob incluye suplentes sin minutos).
    # En algún partido FotMob no trae minutos pero sí el resto de stats: cuenta como jugado
    # si tiene toques registrados.
    jugo = (fm.minutes_played.fillna(0) > 0) | (fm.minutes_played.isna() & fm.touches.notna())
    fm_j = fm[jugo].copy()
    # Algunos jugadores tienen id 0 en FotMob: se identifican por el nombre
    fm_j["fm_key"] = fm_j.player_id.astype(str)
    sin_id = fm_j.player_id == 0
    fm_j.loc[sin_id, "fm_key"] = "nombre:" + fm_j.loc[sin_id, "player"].map(norm_nombre)
    grupos_fm = {k: g for k, g in fm_j.groupby(["match_id", "team"])}

    filas = []
    for (mid, team), g_us in us.dropna(subset=["fm_match_id"]).groupby(["fm_match_id", "team"]):
        g_fm = grupos_fm.get((int(mid), team))
        if g_fm is None:
            continue
        for ia, ib, s in emparejar_grupo(g_us, g_fm):
            filas.append({"us_idx": ia, "fm_idx": ib, "sim_nombre": s,
                          "us_player_id": us.at[ia, "player_id"],
                          "fm_key": fm_j.at[ib, "fm_key"]})
    return us, fm_j, pd.DataFrame(filas)


# =============================================================================
# 3) CONSOLIDACIÓN POR VOTO MAYORITARIO
# =============================================================================
def consolidar(emp: pd.DataFrame, us: pd.DataFrame, fm_j: pd.DataFrame):
    """Estados de cada emparejamiento:
      ok           nombre parecido y coherente con la equivalencia del jugador
      ok_apodo     nombre distinto (apodo: 'Chicharito' / 'Javier Hernández') pero la
                   misma pareja se repite en >= MIN_REPETICIONES partidos
      dudoso       nombre distinto y pareja poco repetida -> revisar a mano
      contradice   no coincide con la pareja habitual de ese jugador y el nombre no se parece
      ok_manual    aceptado tras revisión manual (CORRECCIONES_MANUALES)
    """
    votos = emp.groupby(["us_player_id", "fm_key"]).size().rename("n").reset_index()
    votos["total"] = votos.groupby("us_player_id").n.transform("sum")
    eq = votos.sort_values("n", ascending=False).drop_duplicates("us_player_id")
    eq["confianza"] = (eq.n / eq.total).round(3)
    mapa = dict(zip(eq.us_player_id, eq.fm_key))
    reps = dict(zip(eq.us_player_id, eq.n))

    emp = emp.copy()
    coherente = emp.us_player_id.map(mapa) == emp.fm_key
    repetido = emp.us_player_id.map(reps) >= MIN_REPETICIONES
    parecido = emp.sim_nombre >= UMBRAL_NOMBRE
    emp["estado"] = "contradice"
    emp.loc[parecido & (coherente | (emp.sim_nombre >= 0.8)), "estado"] = "ok"
    emp.loc[~parecido & coherente & repetido, "estado"] = "ok_apodo"
    emp.loc[~parecido & coherente & ~repetido, "estado"] = "dudoso"
    manual = [(u, f) in CORRECCIONES_MANUALES for u, f in zip(emp.us_player_id, emp.fm_key)]
    emp.loc[manual, "estado"] = "ok_manual"

    nombres_us = us.drop_duplicates("player_id").set_index("player_id").player
    nombres_fm = fm_j.drop_duplicates("fm_key").set_index("fm_key").player
    eq["us_player"] = eq.us_player_id.map(nombres_us)
    eq["fm_player"] = eq.fm_key.map(nombres_fm)
    return emp, eq


# =============================================================================
# 4) EQUIPOS: nombre en Understat -> nombre en cada fuente
# =============================================================================
# ClubElo y football-data.co.uk usan los mismos nombres cortos
EQUIPOS_ELO = {
    "Alaves": "Alaves", "Almeria": "Almeria", "Athletic Club": "Ath Bilbao",
    "Atletico Madrid": "Ath Madrid", "Barcelona": "Barcelona", "Cadiz": "Cadiz",
    "Celta Vigo": "Celta", "Eibar": "Eibar", "Elche": "Elche", "Espanyol": "Espanol",
    "Getafe": "Getafe", "Girona": "Girona", "Granada": "Granada",
    "Las Palmas": "Las Palmas", "Leganes": "Leganes", "Levante": "Levante",
    "Mallorca": "Mallorca", "Osasuna": "Osasuna", "Rayo Vallecano": "Vallecano",
    "Real Betis": "Betis", "Real Madrid": "Real Madrid", "Real Oviedo": "Oviedo",
    "Real Sociedad": "Sociedad", "Real Valladolid": "Valladolid", "SD Huesca": "Huesca",
    "Sevilla": "Sevilla", "Valencia": "Valencia", "Villarreal": "Villarreal",
}
# Transfermarkt: club de cada valoración (current_club_name). El primero es el primer equipo;
# el resto son filiales y juveniles (canteranos valorados antes de subir).
EQUIPOS_TM = {
    "Alaves": ["Deportivo Alavés", "Deportivo Alavés B", "Deportivo Alavés U19"],
    "Almeria": ["UD Almería", "UD Almería B"],
    "Athletic Club": ["Athletic Bilbao", "Athletic Club", "Bilbao Athletic", "Athletic Bilbao U19"],
    "Atletico Madrid": ["Atlético de Madrid", "Atlético de Madrid B"],
    "Barcelona": ["FC Barcelona", "FC Barcelona B", "FC Barcelona Atlètic", "FC Barcelona U19",
                  "FC Barcelona U18"],
    "Cadiz": ["Cádiz CF", "Cádiz CF B", "Cádiz CF Mirandilla", "Cádiz CF U19"],
    "Celta Vigo": ["Celta de Vigo", "Celta Vigo B", "RC Celta Fortuna", "Celta de Vigo U19"],
    "Eibar": ["SD Eibar", "SD Eibar B"],
    "Elche": ["Elche CF", "Elche Ilicitano"],
    "Espanyol": ["RCD Espanyol Barcelona", "RCD Espanyol B", "Espanyol Barcelona U19"],
    "Getafe": ["Getafe CF", "Getafe CF B", "Getafe CF U19"],
    "Girona": ["Girona FC", "Girona FC B"],
    "Granada": ["Granada CF", "Recreativo Granada", "Granada CF B", "Granada CF C", "Granada CF U19"],
    "Las Palmas": ["UD Las Palmas", "Las Palmas Atlético", "UD Las Palmas C", "UD Las Palmas U19"],
    "Leganes": ["CD Leganés", "CD Leganés B"],
    "Levante": ["Levante UD", "Atlético Levante UD", "Levante UD U19"],
    "Mallorca": ["RCD Mallorca", "RCD Mallorca B"],
    "Osasuna": ["CA Osasuna", "CA Osasuna Promesas"],
    "Rayo Vallecano": ["Rayo Vallecano", "Rayo Vallecano B"],
    "Real Betis": ["Real Betis Balompié", "Betis Deportivo Balompié", "Real Betis Balompié U19"],
    "Real Madrid": ["Real Madrid", "Real Madrid Castilla", "Real Madrid U19", "Real Madrid U18"],
    "Real Oviedo": ["Real Oviedo", "Real Oviedo Vetusta"],
    "Real Sociedad": ["Real Sociedad", "Real Sociedad B", "Real Sociedad C"],
    "Real Valladolid": ["Real Valladolid CF", "Real Valladolid Promesas", "Real Valladolid B",
                        "Real Valladolid U19"],
    "SD Huesca": ["SD Huesca", "SD Huesca B"],
    "Sevilla": ["Sevilla FC", "Sevilla Atlético", "Sevilla FC C", "Sevilla FC U19"],
    "Valencia": ["Valencia CF", "Valencia Mestalla", "Valencia CF U19"],
    "Villarreal": ["Villarreal CF", "Villarreal CF B", "FC Villarreal C", "Villarreal CF U19"],
}


def tabla_equipos(partidos: pd.DataFrame) -> pd.DataFrame:
    equipos = sorted(set(partidos.home_team) | set(partidos.away_team))
    fm_inv = defaultdict(list)
    for k, v in EQUIPOS.items():
        fm_inv[v].append(k)
    faltan = [e for e in equipos if e not in EQUIPOS_ELO or e not in EQUIPOS_TM]
    if faltan:
        raise ValueError(f"Equipos sin equivalencia en EQUIPOS_ELO / EQUIPOS_TM: {faltan}")
    return pd.DataFrame({
        "understat": equipos,
        "fotmob": [" / ".join(fm_inv.get(e, [e])) for e in equipos],
        "clubelo_y_football_data": [EQUIPOS_ELO[e] for e in equipos],
        "transfermarkt": [EQUIPOS_TM[e][0] for e in equipos],
    })


# =============================================================================
# 5) ELO (antes script 04)
# =============================================================================
URL_ELO = ("https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data-2000-2025/"
           "main/data/EloRatings.csv")


def cargar_elo(datos: Path, forzar: bool) -> pd.DataFrame:
    fichero = datos / "elo" / "elo_clubes_espana.csv"
    if fichero.exists() and not forzar:
        elo = pd.read_csv(fichero)
    else:
        print(f"  Descargando {URL_ELO} ...")
        elo = pd.read_csv(URL_ELO)
        elo = elo[elo.country == "ESP"]
        elo["fecha_descarga"] = date.today().isoformat()
        fichero.parent.mkdir(parents=True, exist_ok=True)
        elo.to_csv(fichero, index=False)
    elo["date"] = pd.to_datetime(elo["date"])
    return elo.sort_values("date")


def anadir_elo(partidos: pd.DataFrame, elo: pd.DataFrame) -> pd.DataFrame:
    """ELO de cada equipo con la última instantánea ESTRICTAMENTE anterior al día del partido."""
    p = partidos.copy()
    p["_fecha"] = pd.to_datetime(p.date).dt.normalize()
    for lado in ("home", "away"):
        izq = p[["us_game_id", "_fecha"]].assign(club=p[f"{lado}_team"].map(EQUIPOS_ELO))
        r = pd.merge_asof(izq.sort_values("_fecha"), elo[["date", "club", "elo"]],
                          left_on="_fecha", right_on="date", by="club",
                          allow_exact_matches=False, direction="backward")
        r[f"_dias_{lado}"] = (r._fecha - r.date).dt.days
        p = p.merge(r[["us_game_id", "elo", f"_dias_{lado}"]].rename(columns={"elo": f"elo_{lado}"}),
                    on="us_game_id", how="left")
    p["elo_diff"] = (p.elo_home - p.elo_away).round(2)
    # Prob. esperada de victoria local según la fórmula ELO (sin ventaja de campo)
    p["elo_p_home"] = (1 / (1 + 10 ** (-p.elo_diff / 400))).round(4)
    p["dias_desde_elo"] = p[["_dias_home", "_dias_away"]].max(axis=1)
    p["elo_provisional"] = p._fecha >= pd.Timestamp("2025-06-15")
    return p.drop(columns=["_fecha", "_dias_home", "_dias_away"])


def np_xg_understat(datos: Path, us: pd.DataFrame) -> pd.Series:
    """xG sin penaltis por jugador y partido: xg - xG de sus penaltis, con los tiros de Understat
    (en Understat los penaltis son los tiros con 'situation' vacía). Así np_xg <= xg siempre."""
    t = pd.read_csv(datos / "understat/understat_tiros.csv", keep_default_na=False,
                    usecols=["shot_id", "game_id", "player_id", "xg", "situation"])
    t = t.drop_duplicates("shot_id")
    t["player_id"] = t.player_id.replace(UNIFICAR_US)
    pen = (t[t.situation.isin(["", "Penalty"])].assign(xg=lambda d: pd.to_numeric(d.xg))
             .groupby(["game_id", "player_id"]).xg.sum())
    idx = pd.MultiIndex.from_arrays([us.game_id, us.player_id])
    xg_pen = pd.Series(pen.reindex(idx).fillna(0).values, index=us.index)
    return (us.xg - xg_pen).clip(lower=0)


def rellenar_fotmob(final: pd.DataFrame) -> pd.DataFrame:
    """Vacíos de FotMob -> 0 en las estadísticas de conteo (ver FM_SIN_RELLENAR, FM_SOLO_PORTERO
    y FM_DESDE_2021). Las filas sin datos de FotMob se quedan vacías."""
    f = final.copy()
    con_fm = f.fm_player_id.notna()
    portero = f.fm_is_goalkeeper.fillna(False).astype(bool)
    cols = [c for c in f.columns if c.startswith("fm_") and c not in FM_SIN_RELLENAR
            and pd.api.types.is_numeric_dtype(f[c])]
    antiguo = f.season < 2021
    original = f.loc[antiguo, FM_DESDE_2021].copy()
    f.loc[con_fm, cols] = f.loc[con_fm, cols].fillna(0)
    f.loc[antiguo, FM_DESDE_2021] = original
    f.loc[~portero, FM_SOLO_PORTERO] = np.nan
    return f


# Historial de valor de mercado de transfermarkt.es: completa el dataset de Kaggle, al que le faltan
# valoraciones (sobre todo de canteranos y las más recientes de algunos jugadores).
TM_URL = "https://www.transfermarkt.es/ceapi/marketValueDevelopment/graph/{id}"
TM_CABECERAS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Accept": "application/json", "Referer": "https://www.transfermarkt.es/"}
TM_PAUSA, TM_PAUSA_BLOQUEO = 4, 60   # segundos entre peticiones / tras un 403-429
TM_DIAS_MAX = 400                    # valoración "desactualizada" si es más antigua
TM_COLUMNAS_WEB = ["player_id", "date", "market_value_in_eur", "current_club_name", "fuente",
                   "fecha_descarga"]


def tm_jugadores_sin_valor(f: pd.DataFrame) -> list[int]:
    """tm_player_id con algún partido sin valoración anterior reciente."""
    falta = (f.tm_valor_mercado.isna() | f.tm_valoracion.eq("posterior")
             | (f.tm_dias_desde_valoracion > TM_DIAS_MAX))
    return sorted(f.loc[falta, "tm_player_id"].dropna().astype(int).unique())


def tm_a_filas(player_id: int, data: dict) -> list[dict]:
    """Respuesta de transfermarkt.es -> una fila por valoración (un 0 es 'sin valor')."""
    return [{"player_id": player_id,
             "date": pd.to_datetime(p["datum_mw"], format="%d/%m/%Y").date().isoformat(),
             "market_value_in_eur": int(p["y"]), "current_club_name": p.get("verein")}
            for p in data.get("list", []) if p.get("y")]


def tm_descargar_web(ids: list[int], ruta: Path) -> int:
    """Descarga el historial de valor de 'ids' y lo añade a 'ruta'. Devuelve las filas nuevas.
    Si no hay conexión o Transfermarkt bloquea, se sigue con lo que haya."""
    import time
    import requests
    filas, fallos = [], []
    with requests.Session() as s:
        for n, pid in enumerate(ids, 1):
            for _ in range(3):
                time.sleep(TM_PAUSA)
                try:
                    r = s.get(TM_URL.format(id=pid), headers=TM_CABECERAS, timeout=30)
                except requests.RequestException:
                    r = None
                if r is not None and r.status_code == 200:
                    filas += tm_a_filas(pid, r.json())
                    break
                if r is None:          # sin conexión: no tiene sentido seguir
                    print("  No se puede conectar con transfermarkt.es; se usan las valoraciones que ya hay.")
                    return _tm_guardar(filas, ruta)
                time.sleep(TM_PAUSA_BLOQUEO)
            else:
                fallos.append(pid)
            if n % 25 == 0 or n == len(ids):
                print(f"    {n}/{len(ids)} jugadores consultados")
    if fallos:
        print(f"  Transfermarkt no respondió para {len(fallos)} jugadores: {fallos}")
    return _tm_guardar(filas, ruta)


def _tm_guardar(filas: list[dict], ruta: Path) -> int:
    if not filas:
        return 0
    nuevo = pd.DataFrame(filas)
    nuevo["fuente"], nuevo["fecha_descarga"] = "transfermarkt.es", date.today().isoformat()
    previo = pd.read_csv(ruta) if ruta.exists() else pd.DataFrame(columns=TM_COLUMNAS_WEB)
    (pd.concat([previo, nuevo], ignore_index=True)
       .drop_duplicates(["player_id", "date"], keep="last")
       .sort_values(["player_id", "date"])[TM_COLUMNAS_WEB].to_csv(ruta, index=False))
    return len(nuevo)


def tm_con_web(val: pd.DataFrame, ruta: Path) -> pd.DataFrame:
    """Valoraciones de Kaggle + las de transfermarkt.es (si existe el fichero)."""
    if not ruta.exists():
        return val
    w = pd.read_csv(ruta, parse_dates=["date"])
    print(f"  + {len(w)} valoraciones de transfermarkt.es ({w.player_id.nunique()} jugadores)")
    return (pd.concat([val, w], ignore_index=True)
              .drop_duplicates(["player_id", "date"], keep="first"))


# =============================================================================
# 6) ESTADÍSTICAS DEL PARTIDO (football-data.co.uk)
# =============================================================================
def estadisticas_fd(datos: Path, partidos: pd.DataFrame) -> pd.DataFrame:
    """Resultado al descanso y estadísticas del partido de football-data, por us_game_id."""
    inv = {v: k for k, v in EQUIPOS_ELO.items()}
    trozos = []
    for f in sorted((datos / "resultados_cuotas").glob("*.csv")):
        d = pd.read_csv(f, encoding="latin1")
        d.columns = [c.lstrip("﻿").lstrip("ï»¿") for c in d.columns]
        d.insert(0, "season", int(f.stem.replace("-", "")))
        trozos.append(d)
    c = pd.concat(trozos, ignore_index=True).copy()
    c["home_team"], c["away_team"] = c.HomeTeam.map(inv), c.AwayTeam.map(inv)
    if c[["home_team", "away_team"]].isna().any().any():
        raise ValueError("Equipos de football-data sin equivalencia en EQUIPOS_ELO")
    c = partidos[["season", "us_game_id", "home_team", "away_team", "home_goals", "away_goals"]] \
        .merge(c, on=["season", "home_team", "away_team"], how="left")
    sin = c.FTHG.isna().sum()
    distinto = ((c.FTHG != c.home_goals) | (c.FTAG != c.away_goals)).sum() - sin
    print(f"  football-data: {c.FTHG.notna().sum()} partidos enlazados, {sin} sin datos, "
          f"{distinto} con resultado distinto al de Understat")
    return c[["us_game_id"] + list(ESTADISTICAS_FD)].rename(columns=ESTADISTICAS_FD)


# =============================================================================
# 7) TRANSFERMARKT: emparejamiento de jugadores y valor de mercado
# =============================================================================
# Revisión manual: id Understat -> id Transfermarkt
# Revisión del 07/10/2026: apodos o nombres que el emparejamiento automático no resuelve.
CORRECCIONES_TM = {
    6120: 207834,    # Bono = Yassine Bounou (Girona, Sevilla)
    5126: 87469,     # José Ángel = Cote (José Ángel Valdés; Eibar, Osasuna)
    5138: 341272,    # Alejandro Pozo = Álex Pozo (Sevilla, Mallorca, Eibar, Almería)
    7981: 538996,    # Nianzou Kouassi = Tanguy Nianzou (Sevilla)
    6434: 354361,    # Franck Zambo = Frank Anguissa (Villarreal)
    10981: 895480,   # Malcom Adu = Adu Ares (Athletic)
    # dudosos confirmados (mismo apellido poco común, mismo club y temporada)
    12817: 949116,   # Rodrigo Abajas = Ro Abajas
    10948: 522784,   # Valentín Castellanos = Taty Castellanos
    9918: 527838,    # Alex Petxarromán = Álex Petxa
    10873: 487180,   # Nabili Zoubdi Touaizi = Nabil Touaizi
    6538: 409106,    # Oghenekaro Etebo = Peter Etebo
    7132: 396333,    # Francisco Montero = Javi Montero
    10601: 939445,   # Alejandro Primo = Álex Primo
    14089: 974251,   # Lancinet Kourouma = Lass Kourouma
    10319: 341263,   # Adrián De la Fuente = Adrián Dela
    # Revisión del 10/10/2026: dudosos (mismo club/temporada, fecha de nacimiento y posición coherentes)
    13278: 741289,   # Youssef Lekhedim = Youssef Enríquez (Alavés)
    2574: 527964,    # Miguelón = Migue Leal (Villarreal, Espanyol)
    13110: 1066206,  # Toni Fernández (Barcelona; el automático le asignaba Guille Fernández)
    13266: 962124,   # Daniel Yáñez (Real Madrid; el automático le asignaba Manuel Ángel)
    13271: 1176519,  # Adrián Arnu / Arnuncio (Valladolid; el automático le asignaba Adrián Verde)
    13748: 1009765,  # Jofre Torrents (Barcelona; no Pablo Torre)
    12098: 1061665,  # Jastin García (Girona; no Borja García)
    13790: 1000135,  # Joselu Pérez (Getafe; no Carles Pérez)
    13645: 1411351,  # José Carlos González = Josito González (Las Palmas)
    14116: 1035156,  # Diego Morcillo (Alavés; no Santiago Mouriño)
    8266: 396340,    # Óscar Clemente (Atlético; no Óscar Castro)
    10915: 932349,   # Diego Méndez (Rayo; no Diego López)
    # Revisión del 10/10/2026: tienen ficha pero el paso 3 los descartaba (valorados en otro
    # club: filial, cesión o club posterior) o el nombre no coincide
    5124: 354697,    # Alberto Rodríguez = Tachi (Alavés)
    12371: 938146,   # Adam Aznou (Valladolid, cedido por el Bayern)
    14225: 741253,   # Javier Boñar (Atlético)
    11617: 706947,   # Yago Alonso = Yago Santiago (Elche)
    9744: 904712,    # Justin Smith (Espanyol)
    12903: 1178721,  # Rafael Bauza = Rafel Bauzà (Espanyol)
    7251: 505675,    # Robert = Rober González (Betis)
    11737: 1061660,  # Silvi Clua = Selvi Clua (Girona)
    14555: 1250522,  # Javier Morcillo = Javi Morcillo (Atlético)
    12257: 945500,   # Rayane Belaid (Atlético)
    8507: 789328,    # Manuel Garrido = Manu Garrido (Leganés)
    13843: 435493,   # Bambo Diaby (Elche)
    9170: 841121,    # John Finn = John Patrick (Getafe)
    6659: 396324,    # Carlos Isaac (Atlético)
    14013: 1064506,  # Adam El Mokhtari = Adam Boayar (Elche)
    13543: 1186245,  # Valde = Víctor Valdepeñas (Real Madrid)
    8901: 786438,    # Jony Álamo (Elche)
    7555: 398738,    # Miguel Marí (Eibar)
    9795: 668177,    # Javier Serrano = Javi Serrano (Atlético)
    12301: 923915,   # Antal Yaakobishvili (Girona)
    7099: 282689,    # Paik Seung-Ho (Girona)
    14586: 1453065,  # Cubo = Miguel Cubo (Atlético; FotMob: Miguel Llorente)
    11761: 711394,   # Javier Martón = Javi Martón (Athletic)
    11738: 1061656,  # Iker Almena (Girona)
    11634: 834470,   # Daniel Rodríguez = Dani Rodríguez (Barcelona, nacido en 2005)
    8708: 783434,    # Unai Arietaleanizbeaskoa = Unai Arieta (Eibar)
    10885: 337070,   # Sekou Gassama (Valladolid)
    9457: 355336,    # José Boacho = Josete Miranda (Getafe)
    9245: 715650,    # Mamor Niang (Getafe)
    11328: 646739,   # Ángel Alarcón (Barcelona; el id 11471 se unifica con este en UNIFICAR_US)
    12197: 891310,   # Rachad Dhimi = Rachad Fettal (Almería)
    6969: 607779,    # Sergio Moreno (Rayo)
    10998: 720488,   # Alejandro Alfaro (Elche)
    10916: 1045767,  # Pablo Muñoz (Rayo, nacido en 2003)
    10213: 888694,   # Álvaro Aguirre (Rayo)
    10502: 891075,   # Alejandro Asensio (Rayo)
    14205: 938156,   # Tomás Marqués = Tommy Marqués (Barcelona)
    10430: 930415,   # Antonio Gomis (Atlético)
    14630: 940464,   # Manuel Serrano = Manu Serrano (Real Madrid)
    12736: 1210422,  # Aboubacar Bassinga (Las Palmas)
    8210: 684253,    # Sergio Cubero (Eibar)
    8554: 646747,    # Rafael Obrador = Rafa Obrador (Mallorca)
    10990: 631093,   # Álex Revuelta (Getafe)
    # Comprobados en transfermarkt.es (10/10/2026)
    9424: 617059,    # Pablo Cuñat = Pablo Campos (nombre completo: Pablo Cuñat Campos; Levante)
    11831: 991268,   # Samu Omorodion = Samu Aghehowa (cambió de nombre; Alavés/Granada 2023/24)
    9778: 204072,    # Henrique = Henrique Silva (Valladolid 24/25); el paso 3 lo unía a otro Henrique (n. 1983)
}
# Las correcciones añadidas el 10/10/2026 se comprobaron en transfermarkt.es (nombre completo,
# fecha de nacimiento y posición).
UMBRAL_TM = 0.8     # parecido de nombre a partir del cual se acepta sin más


def carpeta_tm(datos: Path) -> Path | None:
    for nombre in ("transfermarkt", "transfermarket"):
        if (datos / nombre / "player_valuations.csv").exists():
            return datos / nombre
    return None


def _grupo_us(pos: str) -> str | None:
    if pos == "GK":
        return "Goalkeeper"
    if pos in ("DC", "DL", "DR"):
        return "Defender"
    if pos == "FW":
        return "Attack"
    return None if pos == "Sub" else "Midfield"


def _ajuste_posicion(a, b) -> float:
    """Premia la misma línea, penaliza fuerte portero / no portero."""
    if a is None or not isinstance(b, str):
        return 0.0
    if a == b:
        return 0.15
    if "Goalkeeper" in (a, b):
        return -1.0
    return 0.0 if "Midfield" in (a, b) else -0.3


def emparejar_transfermarkt(final: pd.DataFrame, tm: Path):
    """Empareja cada jugador de Understat con su id de Transfermarkt.
      1. Dentro de cada equipo y temporada: candidatos = jugadores valorados en ese club
         (o su filial) entre enero del primer año y diciembre del segundo.
         Puntuación = parecido de nombre + ajuste por posición; emparejamiento voraz.
      2. Voto mayoritario por jugador de Understat.
      3. Los no emparejados se buscan en todo players.csv (nombre idéntico y misma línea).
    Estados: ok (nombre >= 0,8), ok_apodo (nombre distinto pero la pareja se repite en
    >= 2 temporadas), ok_sin_club (paso 3), ok_manual, dudoso (no se une; revisar)."""
    val = pd.read_csv(tm / "player_valuations.csv")
    val["date"] = pd.to_datetime(val.date)
    club = {n: e for e, lista in EQUIPOS_TM.items() for n in lista}
    val["team"] = val.current_club_name.map(club)
    jug = pd.read_csv(tm / "players.csv", usecols=["player_id", "name", "first_name", "last_name",
                                                   "date_of_birth", "position", "sub_position",
                                                   "foot", "height_in_cm"]).set_index("player_id")
    completo = (jug.first_name.fillna("") + " " + jug.last_name.fillna("")).str.strip()

    def parecido(nombre, pid):
        return max(similitud(nombre, jug.name.get(pid, "")), similitud(nombre, completo.get(pid, "")))

    pos = (final.assign(g=final.position.map(_grupo_us)).dropna(subset=["g"])
           .groupby("player_id").g.agg(lambda s: s.mode().iat[0]))
    us = final.groupby(["season", "team", "player_id"]).agg(player=("player", "first"),
                                                            minutos=("minutes", "sum")).reset_index()
    filas = []
    con_club = val.dropna(subset=["team"])
    for (s, t), g in us.groupby(["season", "team"]):
        ini, fin = pd.Timestamp(f"20{str(s)[:2]}-01-01"), pd.Timestamp(f"20{str(s)[2:]}-12-31")
        cand = con_club[(con_club.team == t) & con_club.date.between(ini, fin)].player_id.unique()
        pares = sorted(((sm + _ajuste_posicion(pos.get(r.player_id), jug.position.get(pid)), sm,
                         r.player_id, pid)
                        for r in g.itertuples() for pid in cand
                        for sm in [parecido(r.player, pid)]), reverse=True)
        usados_a, usados_b = set(), set()
        for total, sm, a, b in pares:
            if a in usados_a or b in usados_b or sm < 0.55 or total < 0.6:
                continue
            usados_a.add(a); usados_b.add(b)
            filas.append((s, t, a, b, round(sm, 3)))
    emp = pd.DataFrame(filas, columns=["season", "team", "us_player_id", "tm_player_id", "sim_nombre"])

    votos = (emp.groupby(["us_player_id", "tm_player_id"])
             .agg(temporadas=("season", "size"), sim_nombre=("sim_nombre", "max")).reset_index())
    votos["total"] = votos.groupby("us_player_id").temporadas.transform("sum")
    eq = votos.sort_values(["temporadas", "sim_nombre"], ascending=False).drop_duplicates("us_player_id")
    eq["estado"] = np.where(eq.sim_nombre >= UMBRAL_TM, "ok",
                            np.where(eq.temporadas >= 2, "ok_apodo", "dudoso"))

    # Paso 3: sin pareja en su club -> nombre idéntico en todo Transfermarkt, misma línea, único
    # y SIN valoraciones desde 2018 (si las tuviera, habría salido en el paso 1: con valoraciones
    # en otro club sería otro jugador con el mismo nombre)
    nombres = us.drop_duplicates("player_id").set_index("player_id").player
    con_val = set(val[val.date >= "2018-01-01"].player_id)
    jug_sin_val = jug[~jug.index.isin(con_val)]
    norm_tm = jug_sin_val.name.fillna("").map(norm_nombre)
    extra = []
    for uid in sorted(set(nombres.index) - set(eq.us_player_id)):
        c = jug_sin_val[(norm_tm == norm_nombre(nombres[uid]))]
        c = c[[_ajuste_posicion(pos.get(uid), p) >= 0 for p in c.position]]
        if len(c) == 1:
            extra.append({"us_player_id": uid, "tm_player_id": c.index[0], "temporadas": 0,
                          "sim_nombre": 1.0, "total": 0, "estado": "ok_sin_club"})
    eq = pd.concat([eq, pd.DataFrame(extra)], ignore_index=True)

    # Correcciones manuales
    eq = eq[~eq.us_player_id.isin(CORRECCIONES_TM)]
    eq = pd.concat([eq, pd.DataFrame([{"us_player_id": u, "tm_player_id": t, "estado": "ok_manual"}
                                      for u, t in CORRECCIONES_TM.items()])], ignore_index=True)
    eq["us_player"] = eq.us_player_id.map(nombres)
    eq["tm_player"] = eq.tm_player_id.map(jug.name)
    minutos = us.groupby("player_id").minutos.sum()
    eq["minutos"] = eq.us_player_id.map(minutos)
    sin = pd.DataFrame({"us_player_id": sorted(set(nombres.index) - set(eq.us_player_id))})
    sin = sin.assign(us_player=sin.us_player_id.map(nombres), minutos=sin.us_player_id.map(minutos),
                     estado="sin_transfermarkt")
    eq = pd.concat([eq, sin], ignore_index=True).astype({"tm_player_id": "Int64"})
    return eq, val, jug


def anadir_transfermarkt(final: pd.DataFrame, eq: pd.DataFrame, val: pd.DataFrame,
                         jug: pd.DataFrame, fechas: pd.Series) -> pd.DataFrame:
    """Columnas tm_ en jugador_partido. El valor es la última valoración ANTERIOR al día del
    partido (sin fuga de datos); si no hay ninguna, la primera posterior, y tm_valoracion lo indica
    ('anterior' / 'posterior'). tm_dias_desde_valoracion es negativo si la valoración es posterior.
    La edad es la del día del partido."""
    validos = eq[eq.estado.str.startswith("ok")].set_index("us_player_id")
    f = final.copy()
    f["tm_player_id"] = f.player_id.map(validos.tm_player_id).astype("Int64")
    f["emparejamiento_tm"] = f.player_id.map(eq.set_index("us_player_id").estado)
    f["_fecha"] = f.game_id.map(fechas).dt.normalize()
    izq = f[["_fecha", "tm_player_id"]].dropna().astype({"tm_player_id": "int64"}).reset_index()
    der = (val[["player_id", "date", "market_value_in_eur"]]
           .rename(columns={"player_id": "tm_player_id", "date": "_fval"}).sort_values("_fval"))
    r = pd.merge_asof(izq.sort_values("_fecha"), der, left_on="_fecha", right_on="_fval",
                      by="tm_player_id", allow_exact_matches=False, direction="backward")
    r = r.set_index("index")
    # Sin valoración anterior (sobre todo canteranos): la primera posterior, marcada
    sin = r.market_value_in_eur.isna()
    post = pd.merge_asof(izq[izq["index"].isin(r.index[sin])].sort_values("_fecha"), der,
                         left_on="_fecha", right_on="_fval", by="tm_player_id",
                         direction="forward").set_index("index")
    r.loc[post.index, ["_fval", "market_value_in_eur"]] = post[["_fval", "market_value_in_eur"]]
    f["tm_valor_mercado"] = (r.market_value_in_eur / 1e6).reindex(f.index)       # millones de €
    f["tm_dias_desde_valoracion"] = (r._fecha - r._fval).dt.days.reindex(f.index)
    f["tm_valoracion"] = np.where(f.tm_valor_mercado.isna(), None,
                                  np.where(f.tm_dias_desde_valoracion > 0, "anterior", "posterior"))
    nac = pd.to_datetime(f.tm_player_id.map(jug.date_of_birth))
    f["tm_edad"] = ((f._fecha - nac).dt.days / 365.25).round(2)
    f["tm_posicion"] = f.tm_player_id.map(jug.position)
    f["tm_demarcacion"] = f.tm_player_id.map(jug.sub_position)
    f["tm_pie"] = f.tm_player_id.map(jug.foot)
    f["tm_altura"] = f.tm_player_id.map(jug.height_in_cm)
    return f.drop(columns="_fecha")


# =============================================================================
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--forzar-elo", action="store_true", help="Vuelve a descargar el ELO")
    p.add_argument("--sin-descarga-tm", action="store_true",
                   help="No descarga de transfermarkt.es las valoraciones que falten")
    p.add_argument("--auxiliares", action="store_true",
                   help="Guarda equivalencias y casos a revisar en db/2_unido/auxiliares/")
    a = p.parse_args()
    db = Path(__file__).resolve().parent / "db"   # carpeta db junto a este script
    datos = db / "1_brutos"
    salida = db / "2_unido"
    salida.mkdir(parents=True, exist_ok=True)
    aux = salida / "auxiliares"
    if a.auxiliares:
        aux.mkdir(exist_ok=True)

    def guardar_aux(df: pd.DataFrame, nombre: str, **kw) -> None:
        """Ficheros de control: solo se guardan con --auxiliares."""
        if a.auxiliares:
            df.to_csv(aux / nombre, index=False, **kw)

    print("1) Understat + FotMob")
    us, us_eq, fm = cargar(datos)
    partidos = emparejar_partidos(us_eq, fm)
    print("  Partidos:", partidos.origen.value_counts().to_dict())

    print("  Emparejando jugadores (tarda un par de minutos)...")
    us, fm_j, emp = emparejar_jugadores(us, fm, partidos)
    emp, eq = consolidar(emp, us, fm_j)
    guardar_aux(eq, "equivalencias_jugadores.csv")

    # Tabla jugador-partido: Understat + columnas de FotMob con prefijo fm_
    # Los 'dudoso' se unen pero quedan marcados; los 'contradice' no se unen.
    validos = emp[emp.estado != "contradice"]
    fm_cols = fm_j.drop(columns=["season", "match_id", "date", "round", "home", "away", "team",
                                 "fecha_descarga", "fm_key"], errors="ignore").add_prefix("fm_")
    # Se quitan las columnas de FotMob sin datos: vacías del todo o casi (fm_crosses solo tiene
    # un valor en 92.532 filas; FotMob da los centros en fm_accurate_crosses)
    vacias = [c for c in fm_cols.columns if fm_cols.loc[validos.fm_idx, c].notna().sum() <= 1]
    if vacias:
        print(f"  Columnas de FotMob sin datos (se quitan): {vacias}")
        fm_cols = fm_cols.drop(columns=vacias)
    final = us.drop(columns=["us_game_id"]).copy()
    final.insert(final.columns.get_loc("xg") + 1, "np_xg", np_xg_understat(datos, final))
    final["emparejamiento"] = "sin_fotmob"
    final.loc[validos.us_idx, "emparejamiento"] = validos.estado.values
    final.loc[validos.us_idx, "sim_nombre"] = validos.sim_nombre.values
    final = final.join(fm_cols.loc[validos.fm_idx].set_axis(validos.us_idx))

    rev = emp[emp.estado.isin(["dudoso", "contradice"])].copy()
    rev["us_player"] = us.loc[rev.us_idx, "player"].values
    rev["fm_player"] = fm_j.loc[rev.fm_idx, "player"].values
    rev["partido"] = us.loc[rev.us_idx, "game"].values
    rev["us_min"] = us.loc[rev.us_idx, "minutes"].values
    rev["fm_min"] = fm_j.loc[rev.fm_idx, "minutes_played"].values
    guardar_aux(rev[["partido", "us_player", "fm_player", "us_min", "fm_min", "sim_nombre", "estado"]]
                .sort_values("sim_nombre"), "revisar_emparejamientos.csv", encoding="utf-8-sig")
    if len(rev):
        print(f"  Casos dudosos o contradictorios Understat-FotMob: {len(rev)} "
              "(detalle con --auxiliares)")
    n = len(final)
    for k, v in final.emparejamiento.value_counts().items():
        print(f"  {k:<25} {v:>7}  ({v / n:.1%})")

    print("2) Equipos")
    guardar_aux(tabla_equipos(partidos), "equivalencias_equipos.csv")

    print("3) Partidos: estadísticas de equipo de Understat y football-data + ELO")
    stats = [f"{lado}_{c}" for lado in ("home", "away")
             for c in ("xg", "np_xg", "expected_points", "ppda", "deep_completions")]  # points = f(goles)
    partidos = partidos.merge(us_eq[["game_id"] + stats].rename(columns={"game_id": "us_game_id"}),
                              on="us_game_id", how="left")
    partidos = partidos.merge(estadisticas_fd(datos, partidos), on="us_game_id", how="left")
    partidos = anadir_elo(partidos, cargar_elo(datos, a.forzar_elo))
    print(f"  Partidos sin ELO: {(partidos.elo_home.isna() | partidos.elo_away.isna()).sum()}; "
          f"con ELO provisional (desde 15/06/2025): {partidos.elo_provisional.sum()}")
    partidos.sort_values("date").to_csv(salida / "partidos.csv", index=False)

    print("4) Transfermarkt")
    tm = carpeta_tm(datos)
    if tm is None:
        print("  No encuentro db/1_brutos/transfermarkt/ (player_valuations.csv, players.csv); se salta.")
    else:
        eq_tm, val, jug = emparejar_transfermarkt(final, tm)
        guardar_aux(eq_tm, "equivalencias_transfermarkt.csv", encoding="utf-8-sig")
        ok_tm = eq_tm[eq_tm.estado.str.startswith("ok")]
        compartidos = ok_tm[ok_tm.tm_player_id.duplicated(keep=False)].sort_values("tm_player_id")
        if len(compartidos):
            print("  AVISO: varios ids de Understat con la misma ficha de Transfermarkt. Si es el mismo "
                  "jugador, añádelo a UNIFICAR_US; si no, corrige CORRECCIONES_TM:")
            print(compartidos[["us_player_id", "us_player", "tm_player_id", "tm_player", "estado"]]
                  .to_string(index=False))
        rev_tm = eq_tm[eq_tm.estado.isin(["dudoso", "sin_transfermarkt"])].sort_values(
            "minutos", ascending=False)
        guardar_aux(rev_tm, "revisar_transfermarkt.csv", encoding="utf-8-sig")
        if len(rev_tm):
            print(f"  Jugadores dudosos o sin Transfermarkt: {len(rev_tm)} (detalle con --auxiliares)")
        fechas = pd.to_datetime(partidos.set_index("us_game_id").date)
        web = tm / "valoraciones_web.csv"      # historial completado desde transfermarkt.es
        con_tm = anadir_transfermarkt(final, eq_tm, tm_con_web(val, web), jug, fechas)
        if not a.sin_descarga_tm:
            ya = set(pd.read_csv(web, usecols=["player_id"]).player_id) if web.exists() else set()
            ids = [i for i in tm_jugadores_sin_valor(con_tm) if i not in ya]
            if ids:
                print(f"  Descargando de transfermarkt.es el historial de {len(ids)} jugadores "
                      f"sin valoración anterior reciente (~{len(ids) * TM_PAUSA // 60 + 1} min)...")
                if tm_descargar_web(ids, web):
                    con_tm = anadir_transfermarkt(final, eq_tm, tm_con_web(val, web), jug, fechas)
        final = con_tm
        m = final.minutes
        print("  Jugadores:", eq_tm.estado.value_counts().to_dict())
        print(f"  Filas con id de Transfermarkt: {final.tm_player_id.notna().mean():.1%} "
              f"(minutos: {m[final.tm_player_id.notna()].sum() / m.sum():.1%}); "
              f"con valor de mercado: {final.tm_valor_mercado.notna().mean():.1%} "
              f"(minutos: {m[final.tm_valor_mercado.notna()].sum() / m.sum():.1%})")

    final = rellenar_fotmob(final).drop(columns=QUITAR_JUGADOR, errors="ignore")
    final.to_csv(salida / "jugador_partido.csv", index=False, encoding="utf-8-sig")
    print(f"\nJugador-partido: {len(final)} filas, {final.shape[1]} columnas")
    print(f"Salida en {salida.resolve()}")
    print("Ejecuta ahora el 04 para añadir a partidos.csv las variables de contexto "
          "(descanso, bajas, valor de mercado).")


if __name__ == "__main__":
    main()
