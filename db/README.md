# Bases de datos del TFG — LaLiga 2018/19 a 2025/26

## Scripts

| Script | Qué hace |
|---|---|
| `01_descarga_understat.py` | Descarga Understat → `1_brutos/understat/` |
| `02_descarga_fotmob.py` | Descarga FotMob → `1_brutos/fotmob/` |
| `03_unir_datos.py` | Une todas las fuentes → `2_unido/` (incluye el ELO), quita redundancias, rellena con 0 los vacíos de FotMob y descarga de transfermarkt.es las valoraciones que falten (`--sin-descarga-tm` para no descargar) |
| `04_variables_contexto.py` | Añade a `2_unido/partidos.csv` descanso, congestión, bajas y valor de mercado |

Orden de ejecución: 01 → 02 → 03 → 04. Después del 03 hay que ejecutar siempre el 04 (el 03 reescribe `partidos.csv` sin el contexto).

## 1_brutos/ — datos tal como salen de cada fuente (no se modifican)

| Carpeta | Fuente | Contenido |
|---|---|---|
| `understat/` | Understat (librería soccerdata; uso académico) | Jugador-partido (minutos, goles, xG, xA, xGChain, xGBuildup…), equipo-partido (xG, PPDA, puntos esperados), jugador-temporada, calendario, tiros con coordenadas |
| `fotmob/` | FotMob (con permiso; librería fmscraper) | `fotmob_jugadores_partido_<temporada>`: estadísticas detalladas jugador-partido (nota, pases, duelos, acciones defensivas…). Incluye suplentes sin minutos |
| `elo/` | ClubElo vía "Club Football Match Data (2000-2025)", A. Gábor (GitHub, MIT) | Instantáneas de ELO de clubes españoles (días 1 y 15 de cada mes). Desde 15/06/2025, continuación provisional del autor |
| `resultados_cuotas/` | football-data.co.uk | Resultados, estadísticas de equipo y cuotas, un CSV por temporada. Solo se usan el descanso y las estadísticas; las cuotas no son objeto del TFG |
| `transfermarket/` | Transfermarkt vía "transfermarkt-datasets", D. Cariboo (CC0; Kaggle davidcariboo/player-scores, versión 679 del 05/09/2026) | `player_valuations.csv` (historial de valor de mercado con el club de cada fecha; hasta el 12/06/2026) y `players.csv` (fecha de nacimiento, posición, demarcación, pie, altura). `valoraciones_web.csv`: valoraciones que faltaban en Kaggle, descargadas de transfermarkt.es por el 03 (solo pide los jugadores que aún no tiene) |

## 2_unido/ — tablas integradas (las genera `03_unir_datos.py`; el 04 añade el contexto a `partidos.csv`)

| Fichero | Contenido |
|---|---|
| `jugador_partido.csv` | Una fila por jugador y partido (solo quien jugó). Columnas de Understat + FotMob (prefijo `fm_`) + Transfermarkt (prefijo `tm_`: valor de mercado anterior al partido —`tm_valoracion` indica si es anterior o posterior—, edad ese día, posición, demarcación, pie, altura). `emparejamiento` y `emparejamiento_tm` indican la calidad del cruce. Sin columnas redundantes: el 03 quita las que son copia o suma de otras y, cuando Understat y FotMob miden lo mismo, deja solo la mejor versión (lista `QUITAR_JUGADOR`): xG, xA, pases clave, tiros y autogoles de Understat; `np_xg` (xG sin penaltis) se calcula con los tiros de Understat para las 8 temporadas |
| `partidos.csv` | Una fila por partido: ids de Understat y FotMob, resultado, estadísticas de equipo de Understat (xG, xG sin penaltis, puntos esperados, PPDA, deep completions), de football-data (goles al descanso, tiros, tiros a puerta, faltas, córners, amarillas y rojas) ELO previo (`elo_home`, `elo_away`, `elo_diff`, `elo_p_home`, `dias_desde_elo`, `elo_provisional`) y variables de contexto previas al partido del 04: descanso y congestión (`descanso_dias`, `partidos_14d`, `europa_previo`, `competicion_europea`), bajas (`n_bajas`, `n_lesionados`, `n_sancionados`, `bajas_peso`) y valor de mercado (`valor_xi`: suma del valor de los 11 titulares, `xi_sin_valor` y `valor_xi_log_ratio`), cada una para local y visitante |

El 03 solo guarda las tablas de análisis. Con `python 03_unir_datos.py --auxiliares` guarda además, en `2_unido/auxiliares/`, las equivalencias de equipos y jugadores entre fuentes y los casos dudosos a revisar.

Sin fuga de datos: el ELO y el valor de mercado son los últimos valores con fecha estrictamente anterior al día del partido.

Cruce entre tablas: partido por `us_game_id` (`game_id` en `jugador_partido`), jugador por `player_id` de Understat.

## Incidencias conocidas
- Mallorca–Girona (03/03/2024): no está en la descarga de FotMob (34 filas sin datos de FotMob). Volver a ejecutar el 02 lo reintenta; si FotMob ya lo tiene, después hay que ejecutar el 03 y el 04.
- Espanyol–Levante (20/06/2020): FotMob asigna los jugadores del Levante al Leganés. El 03 lo corrige (si a un partido le falta uno de sus equipos, el equipo extraño pasa a ser el que falta).
- Understat duplicaba 62 filas en dos partidos; se eliminan al unir.
- Transfermarkt: 100 % de los minutos con id y 99,7 % con valor de mercado. 74 emparejamientos corregidos a mano (ver `CORRECCIONES_TM` en el 03); los 59 añadidos el 10/10/2026 se comprobaron en transfermarkt.es (nombre completo, fecha de nacimiento y posición). Samu Omorodion figura en Transfermarkt como Samu Aghehowa y Pablo Cuñat como Pablo Campos. Algunos jugadores tienen ficha pero ninguna valoración (p. ej. Seydouba Cissé, Viti Rozada, Dinko Horkas).
- Understat da dos ids a un mismo jugador en dos casos: Ángel Alarcón (11471 → 11328) y Álvaro García Pascual (13276 → 13294). El 03 los unifica al cargar (`UNIFICAR_US`), así que `player_id` ya identifica a un único jugador; si aparecen casos nuevos, el 03 avisa.
- Vacíos de FotMob: FotMob deja vacías las estadísticas de conteo que valen 0; el 03 las rellena con 0. Siguen vacías, porque ahí vacío no es 0: las filas sin datos de FotMob, `fm_rating_title` en jugadores con pocos minutos (FotMob no les pone nota), `fm_goals_conceded` y `fm_expected_goals_on_target_faced` en jugadores de campo, y el xGOT antes de la 20/21 (solo existe en 2 partidos de la 19/20). `fm_goals_prevented` se quita: es xGOT recibido − goles encajados. El 03 quita `fm_crosses` (un solo valor en todas las temporadas; los centros están en `fm_accurate_crosses`).
- Minutos: Understat corta en el 90 y FotMob cuenta el añadido; difieren más de 5 min en un 3,5 % de las filas (sobre todo desde la 23/24). Goles Understat vs FotMob: 30 discrepancias en 92.532 filas; los goles de Understat cuadran siempre con el marcador.
- Valor de mercado: el historial de Kaggle se completa con `1_brutos/transfermarket/valoraciones_web.csv` (1.832 valoraciones de 174 jugadores descargadas de transfermarkt.es el 10/10/2026; en las fechas que coinciden con Kaggle el valor es idéntico). Se usa la última valoración anterior al partido; si no hay ninguna, la primera posterior (`tm_valoracion` = 'posterior', 227 filas, mediana de 90 días después; `tm_dias_desde_valoracion` es entonces negativo). Sin ningún valor: Unai Arieta (4 filas). 31 filas con la última valoración a más de 400 días.
- FotMob no tiene xG ni xA en la 18/19 ni en la 19/20; por eso se usan los de Understat. De FotMob solo quedan las variables de xG que Understat no tiene (xGOT y xGOT recibido), desde la 20/21.
- Understat: la suma del xG de los jugadores no siempre coincide con el xG del equipo de `partidos.csv` (difiere más de 0,1 en el 4,6 % de los equipo-partido). Es una incoherencia de la propia fuente.

Fecha de descarga: 06/10/2026 (Transfermarkt: 07/10/2026).
