"""Dimensão de ocupações (CBO 2002) e regra do nível hierárquico do funil de liderança.

A fonte é a aba "cbo2002ocupação" do layout oficial do Novo CAGED (PDET/MTE), que traz o
código e o título de cada ocupação. O nível hierárquico sai de duas informações:

1. A estrutura da CBO. O grande grupo 1 reúne dirigentes e gerentes. Dentro dele, os
   subgrupos 11 (poder público) e 12 (dirigentes de empresas) são direção; o 14
   (gerentes) é gerência; no 13 o título decide entre diretor e gerente.
2. O texto do título, para achar cargos de supervisão espalhados pelos grandes grupos 2 a 9
   ("Supervisor de...", "Encarregado de...", "Mestre de...", "Chefe de...").

Algumas ocupações do grande grupo 1 são profissões técnicas sem função de comando
(analistas de planejamento, turismólogo, sanitarista etc.) e ficam na base.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

from . import config, extrair

# Ocupações do grande grupo 1 que não exercem comando.
GG1_SEM_COMANDO_FAMILIAS = {1115}                    # EPPGG, analista de planejamento e orçamento
GG1_SEM_COMANDO_CODIGOS = {122520, 131215, 131220, 131225}  # turismólogo, tecnólogo em gestão hospitalar, gerontólogo, sanitarista

PADRAO_SUPERVISAO = re.compile(r"^(supervisor|supervisora|encarregad[oa]|chefe|coordenador|coordenadora|mestre|contramestre)\b")
EXCECOES_SUPERVISAO = re.compile(r"^mestre de cerimonias")


def normalizar(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", str(txt)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", txt).strip().lower()


def classificar(cod: int, titulo: str) -> tuple[int, str]:
    """Retorna (nível, regra aplicada)."""
    gg, sgp, fam = cod // 100000, cod // 10000, cod // 100
    t = normalizar(titulo)
    if gg == 1:
        if fam in GG1_SEM_COMANDO_FAMILIAS or cod in GG1_SEM_COMANDO_CODIGOS:
            return 1, "GG1 sem função de comando"
        if sgp in (11, 12):
            return 4, f"subgrupo principal {sgp}"
        if sgp == 13:
            return (4, "subgrupo 13, título de diretor") if t.startswith("diretor") else (3, "subgrupo 13, título de gerente")
        if sgp == 14:
            return 3, "subgrupo principal 14 (gerentes)"
    if PADRAO_SUPERVISAO.match(t) and not EXCECOES_SUPERVISAO.match(t):
        return 2, "título de supervisão"
    return 1, "demais ocupações"


def construir_dim_ocupacao() -> pd.DataFrame:
    destino = config.DIR_DADOS / "referencias" / "layout_novo_caged.xlsx"
    if not destino.exists():
        extrair.baixar(config.FTP_LAYOUT_CAGED, destino)
    df = pd.read_excel(destino, sheet_name="cbo2002ocupação")
    df.columns = ["cbo", "titulo"]
    df["cbo"] = pd.to_numeric(df["cbo"], errors="coerce")
    df = df.dropna(subset=["cbo"]).astype({"cbo": "int32"})
    df["titulo"] = df["titulo"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    df = df.drop_duplicates("cbo", keep="first")
    res = [classificar(c, t) for c, t in zip(df["cbo"], df["titulo"])]
    df["nivel"] = np.array([r[0] for r in res], dtype="int8")
    df["regra_nivel"] = [r[1] for r in res]
    df["grande_grupo"] = (df["cbo"] // 100000).astype("int8")
    df["familia"] = (df["cbo"] // 100).astype("int16")
    df["desativada"] = df["titulo"].str.contains("desativad", case=False)
    return df.reset_index(drop=True)


def vetor_nivel(dim: pd.DataFrame) -> np.ndarray:
    """Vetor de consulta rápida: posição = código CBO, valor = nível (1 se desconhecido)."""
    v = np.ones(1_000_000, dtype="int8")
    v[dim["cbo"].to_numpy()] = dim["nivel"].to_numpy()
    return v
