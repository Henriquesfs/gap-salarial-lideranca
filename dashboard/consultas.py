"""Acesso ao PostgreSQL. Os filtros do painel viram cláusulas WHERE e a agregação é feita
no banco (GROUP BY); o painel recebe só tabelas pequenas, já resumidas.

Os valores dos filtros saem de listas fixas do próprio painel e passam por validação de
tipo antes de entrar no SQL (inteiros ou letras de seção CNAE), então não há como
injetar texto arbitrário na consulta.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

MEDIDAS_RAIS = ["n_vinculos", "n_salario", "soma_remuneracao", "soma_salario_hora", "soma_salario_hora2"]
MEDIDAS_CAGED = ["n_mov", "n_salario", "soma_salario", "soma_salario_hora"]

# Colunas de cada tabela que aceitam filtro e podem ser usadas como dimensão.
COLUNAS = {
    "fato_rais_perfil": {"ano", "uf", "sexo", "raca", "faixa_etaria", "escolaridade", "nivel", "setor"},
    "fato_rais_setor": {"ano", "uf", "sexo", "raca", "nivel", "cnae_secao", "setor"},
    "fato_rais_ocupacao": {"ano", "regiao", "sexo", "raca", "setor", "cbo"},
    "amostra_rais": {"ano", "uf", "sexo", "raca", "faixa_etaria", "escolaridade", "nivel", "cnae_secao", "setor"},
    "fato_caged_perfil": {"competencia", "uf", "sexo", "raca", "faixa_etaria", "nivel", "tipo"},
    "fato_caged_motivo": {"competencia", "regiao", "sexo", "raca", "nivel", "motivo"},
    "fato_caged_setor": {"competencia", "uf", "sexo", "raca", "nivel", "cnae_secao", "tipo"},
}


@dataclass(frozen=True)
class Filtros:
    """Seleção atual do usuário. Tupla vazia = sem filtro naquela coluna."""
    anos: tuple = ()
    ufs: tuple = ()
    setores: tuple = ()
    sexos: tuple = ()
    racas: tuple = ()
    faixas: tuple = ()
    escolaridades: tuple = ()
    niveis: tuple = ()
    extra: tuple = field(default_factory=tuple)  # pares (coluna, valores) adicionais


def _url() -> str:
    url = None
    try:
        url = st.secrets.get("DATABASE_URL")
    except Exception:
        pass
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        st.error("Defina DATABASE_URL em .streamlit/secrets.toml ou nas variáveis de ambiente.")
        st.stop()
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)


@st.cache_resource(show_spinner=False)
def motor():
    # Pool pequeno: o plano gratuito da Aiven aceita no máximo 20 conexões (max_connections = 20).
    return create_engine(_url(), pool_size=2, max_overflow=2, pool_pre_ping=True, pool_recycle=600)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def sql(consulta: str) -> pd.DataFrame:
    with motor().connect() as c:
        return pd.read_sql(text(consulta), c)


def _lista(valores) -> str:
    itens = []
    for v in valores:
        if isinstance(v, str):
            if not re.fullmatch(r"[A-Z]", v):
                raise ValueError(f"valor de filtro inválido: {v!r}")
            itens.append(f"'{v}'")
        else:
            itens.append(str(int(v)))
    return ", ".join(itens)


def where(tabela: str, f: Filtros) -> str:
    cols = COLUNAS[tabela]
    cond = []

    def add(coluna: str, valores, expr: str | None = None):
        if valores:
            cond.append(f"{expr or coluna} IN ({_lista(valores)})")

    if "ano" in cols:
        add("ano", f.anos)
    elif "competencia" in cols:
        add("competencia", f.anos, "competencia / 100")
    if "uf" in cols:
        add("uf", f.ufs)
    elif "regiao" in cols and f.ufs:
        add("regiao", sorted({int(u) // 10 for u in f.ufs}))
    for coluna, valores in (("setor", f.setores), ("sexo", f.sexos), ("raca", f.racas),
                            ("faixa_etaria", f.faixas), ("escolaridade", f.escolaridades),
                            ("nivel", f.niveis)):
        if coluna in cols:
            add(coluna, valores)
    for coluna, valores in f.extra:
        if coluna in cols:
            add(coluna, valores)
    return ("WHERE " + " AND ".join(cond)) if cond else ""


def _dims(tabela: str, dims: list[str]) -> list[str]:
    """Valida as dimensões pedidas. `regiao` e `ano` podem ser derivadas de `uf` e `competencia`."""
    cols = COLUNAS[tabela]
    out = []
    for d in dims:
        if d in cols:
            out.append(d)
        elif d == "regiao" and "uf" in cols:
            out.append("uf / 10 AS regiao")
        elif d == "ano" and "competencia" in cols:
            out.append("competencia / 100 AS ano")
        elif d == "grupo" and {"sexo", "raca"} <= cols:
            # Código do grupo interseccional: sexo x 10 + grupo racial (1 branca, 2 negra,
            # 4 amarela/indígena, 9 não informada).
            out.append("sexo * 10 + CASE WHEN raca = 1 THEN 1 WHEN raca IN (2, 3) THEN 2 "
                       "WHEN raca IN (4, 5) THEN 4 ELSE 9 END AS grupo")
        else:
            raise ValueError(f"{d} não é dimensão de {tabela}")
    return out


def agregar(tabela: str, dims: list[str], f: Filtros) -> pd.DataFrame:
    """SELECT dims, SUM(medidas) FROM tabela WHERE filtros GROUP BY dims."""
    exprs = _dims(tabela, dims)
    med = MEDIDAS_CAGED if tabela == "fato_caged_perfil" else (["n_mov"] if tabela.startswith("fato_caged") else MEDIDAS_RAIS)
    sel = ", ".join(exprs + [f"SUM({m})::float8 AS {m}" for m in med])
    grp = f"GROUP BY {', '.join(str(i + 1) for i in range(len(exprs)))}" if exprs else ""
    return sql(f"SELECT {sel} FROM {tabela} {where(tabela, f)} {grp}")


# Recortes do gap controlado. As condições são fixas no código, não vêm do usuário.
RECORTES = {
    "mulheres_x_homens": ("sexo = 2", "sexo = 1", ["uf", "faixa_etaria", "escolaridade", "nivel", "setor", "raca"]),
    "mulher_negra_x_homem_branco": ("sexo = 2 AND raca IN (2, 3)", "sexo = 1 AND raca = 1",
                                    ["uf", "faixa_etaria", "escolaridade", "nivel", "setor"]),
}


def gap_controlado(recorte: str, f: Filtros, n_min: int = 5) -> dict:
    """Gap ajustado pela composição.

    Em cada célula (mesma UF, faixa etária, escolaridade, nível hierárquico e setor) o
    grupo-alvo é comparado ao grupo de referência. O salário-hora real do alvo é dividido
    pelo que ele receberia se ganhasse, em cada célula, a média da referência. O que sobra
    é a diferença que essas características não explicam. Células com menos de `n_min`
    vínculos de cada lado ficam de fora; `cobertura` informa a fração do alvo considerada.
    """
    alvo, ref, celulas = RECORTES[recorte]
    cel = ", ".join(celulas)
    q = f"""
        WITH c AS (
            SELECT {cel},
                   SUM(n_salario)         FILTER (WHERE {alvo}) AS na,
                   SUM(soma_salario_hora) FILTER (WHERE {alvo}) AS sa,
                   SUM(n_salario)         FILTER (WHERE {ref})  AS nr,
                   SUM(soma_salario_hora) FILTER (WHERE {ref})  AS sr
            FROM fato_rais_perfil {where('fato_rais_perfil', f)}
            GROUP BY {cel}
        )
        SELECT 1 - SUM(sa) FILTER (WHERE na >= {n_min} AND nr >= {n_min})
                 / NULLIF(SUM(na * sr / nr) FILTER (WHERE na >= {n_min} AND nr >= {n_min}), 0) AS gap,
               SUM(na) FILTER (WHERE na >= {n_min} AND nr >= {n_min})::float8
                 / NULLIF(SUM(na), 0) AS cobertura
        FROM c"""
    r = sql(q)
    return {"gap": r["gap"].iloc[0], "cobertura": r["cobertura"].iloc[0]}


def quantis(dims: list[str], f: Filtros, variavel: str = "salario_hora") -> pd.DataFrame:
    """Quantis ponderados (p10, p25, mediana, p75, p90) a partir da amostra estratificada."""
    if variavel not in ("salario_hora", "remuneracao"):
        raise ValueError(variavel)
    exprs = _dims("amostra_rais", dims)
    part = f"PARTITION BY {', '.join(dims)}" if dims else ""
    cols = (", ".join(dims) + ",") if dims else ""
    cols_base = (", ".join(exprs) + ",") if dims else ""
    grp = f"GROUP BY {', '.join(dims)}" if dims else ""
    q = f"""
        WITH b AS (
            SELECT {cols_base} {variavel}, peso FROM amostra_rais {where('amostra_rais', f)}
        ), s AS (
            SELECT {cols} {variavel} AS v, peso,
                   SUM(peso) OVER ({part} ORDER BY {variavel} ROWS UNBOUNDED PRECEDING) AS acum,
                   SUM(peso) OVER ({part}) AS total
            FROM b
        )
        SELECT {cols}
               COUNT(*) AS n_amostra,
               MIN(v) FILTER (WHERE acum >= 0.10 * total) AS p10,
               MIN(v) FILTER (WHERE acum >= 0.25 * total) AS p25,
               MIN(v) FILTER (WHERE acum >= 0.50 * total) AS p50,
               MIN(v) FILTER (WHERE acum >= 0.75 * total) AS p75,
               MIN(v) FILTER (WHERE acum >= 0.90 * total) AS p90
        FROM s {grp}"""
    return sql(q)


def histograma(f: Filtros, largura: float = 2.5, maximo: float = 150.0) -> pd.DataFrame:
    """Distribuição do salário-hora (amostra ponderada) em faixas de R$ {largura}."""
    q = f"""
        SELECT sexo, raca,
               LEAST(FLOOR(salario_hora / {largura}) * {largura}, {maximo}) AS faixa,
               SUM(peso)::float8 AS peso
        FROM amostra_rais {where('amostra_rais', f)}
        GROUP BY 1, 2, 3"""
    return sql(q)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def dimensoes() -> dict[str, pd.DataFrame]:
    nomes = ["dim_uf", "dim_sexo", "dim_raca", "dim_grupo", "dim_faixa_etaria", "dim_escolaridade",
             "dim_nivel", "dim_secao_cnae", "dim_setor", "dim_motivo", "dim_competencia"]
    with motor().connect() as c:
        return {n: pd.read_sql(text(f"SELECT * FROM {n}"), c) for n in nomes}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def ocupacoes() -> pd.DataFrame:
    return sql("SELECT cbo, titulo, nivel FROM dim_ocupacao")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def qualidade() -> pd.DataFrame:
    return sql("SELECT fonte, referencia, relatorio::text AS relatorio FROM etl_execucao ORDER BY id")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def coletas_api() -> pd.DataFrame:
    return sql("""
        SELECT url, coletado, status,
               CASE jsonb_typeof(resposta) WHEN 'array' THEN jsonb_array_length(resposta) END AS registros
        FROM api_coleta ORDER BY id""")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def salario_minimo() -> pd.DataFrame:
    """Valor vigente no último mês publicado de cada ano (série 1619 do Banco Central)."""
    return sql("""
        SELECT DISTINCT ON (competencia / 100) competencia / 100 AS ano, valor
        FROM ref_serie_mensal WHERE serie = 'salario_minimo'
        ORDER BY competencia / 100, competencia DESC""")
