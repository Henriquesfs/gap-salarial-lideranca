"""Regras de negócio dos indicadores interseccionais.

Todas as funções recebem tabelas agregadas (somas) e devolvem médias e razões. Médias
nunca são calculadas como média de médias: sempre soma / contagem no grão pedido.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

N_MINIMO = 30          # abaixo disso a média não é exibida (regra de supressão)
N_MINIMO_CELULA = 5    # para o gap controlado, cada célula precisa de 5 vínculos de cada lado
REFERENCIA = "Homem branco"
GRUPO_CODIGO = {
    11: "Homem branco", 12: "Homem negro", 14: "Homem amarelo/indígena", 19: "Homem (raça não informada)",
    21: "Mulher branca", 22: "Mulher negra", 24: "Mulher amarela/indígena", 29: "Mulher (raça não informada)",
}


def rotular(df: pd.DataFrame, dims: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Acrescenta rótulos legíveis às colunas de código presentes."""
    df = df.copy()
    if {"sexo", "raca"} <= set(df.columns):
        g = dims["dim_grupo"][["sexo", "raca", "grupo_interseccional", "grupo_racial"]]
        df = df.merge(g, on=["sexo", "raca"], how="left")
    mapas = {
        "sexo": ("dim_sexo", "sexo", "sexo_nome"),
        "nivel": ("dim_nivel", "nivel", "nivel_nome"),
        "faixa_etaria": ("dim_faixa_etaria", "faixa_etaria", "faixa_nome"),
        "escolaridade": ("dim_escolaridade", "escolaridade", "escolaridade_nome"),
        "setor": ("dim_setor", "setor", "setor_nome"),
        "cnae_secao": ("dim_secao_cnae", "cnae_secao", "secao_nome"),
    }
    for col, (tab, chave, novo) in mapas.items():
        if col in df.columns:
            m = dims[tab].set_index(chave)["nome"]
            df[novo] = df[col].map(m)
    if "grupo" in df.columns:
        df["grupo_interseccional"] = df["grupo"].map(GRUPO_CODIGO)
    if "uf" in df.columns:
        u = dims["dim_uf"].set_index("uf")
        df["uf_sigla"] = df["uf"].map(u["sigla"])
        df["regiao_nome"] = df["uf"].map(u["regiao_nome"])
    elif "regiao" in df.columns:
        r = dims["dim_uf"].drop_duplicates("regiao").set_index("regiao")["regiao_nome"]
        df["regiao_nome"] = df["regiao"].map(r)
    return df


def medias(df: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    """Salário-hora e remuneração médios no grão `por`, com supressão de grupos pequenos."""
    g = df.groupby(por, as_index=False, dropna=False)[
        ["n_vinculos", "n_salario", "soma_remuneracao", "soma_salario_hora", "soma_salario_hora2"]].sum()
    ok = g["n_salario"] >= N_MINIMO
    g["salario_hora"] = np.where(ok, g["soma_salario_hora"] / g["n_salario"].clip(lower=1), np.nan)
    g["remuneracao"] = np.where(ok, g["soma_remuneracao"] / g["n_salario"].clip(lower=1), np.nan)
    var = g["soma_salario_hora2"] / g["n_salario"].clip(lower=1) - g["salario_hora"] ** 2
    g["desvio_salario_hora"] = np.sqrt(var.clip(lower=0))
    return g


def gap(alvo: float, referencia: float) -> float:
    """Gap = 1 - alvo / referência. Positivo: o grupo-alvo ganha menos que a referência."""
    if referencia is None or alvo is None or not np.isfinite(referencia) or not np.isfinite(alvo) or referencia == 0:
        return np.nan
    return 1 - alvo / referencia


def gap_por_grupo(m: pd.DataFrame, valor: str, por: list[str]) -> pd.DataFrame:
    """Gap de cada grupo interseccional contra homens brancos, dentro de cada recorte `por`."""
    ref = m[m["grupo_interseccional"] == REFERENCIA][por + [valor]].rename(columns={valor: "ref"})
    out = m.merge(ref, on=por, how="left") if por else m.assign(ref=ref[valor].iloc[0] if len(ref) else np.nan)
    out["gap"] = 1 - out[valor] / out["ref"]
    return out


# O gap controlado (ajustado pela composição) é calculado no banco, em
# consultas.gap_controlado, porque precisa percorrer todas as células do cubo.


def participacao(df: pd.DataFrame, por: list[str], grupo: str, medida: str = "n_vinculos") -> pd.DataFrame:
    """Participação de cada `grupo` dentro de cada recorte `por` (soma 100% por recorte)."""
    g = df.groupby(por + [grupo], as_index=False)[medida].sum()
    g["participacao"] = g[medida] / g.groupby(por)[medida].transform("sum")
    return g


def indice_representacao(part: pd.DataFrame, grupo: str, nivel_base: str = "Base") -> pd.DataFrame:
    """Participação no nível / participação na base. 1 = representação proporcional."""
    base = part[part["nivel_nome"] == nivel_base][[grupo, "participacao"]].rename(columns={"participacao": "part_base"})
    out = part.merge(base, on=grupo, how="left")
    out["indice"] = out["participacao"] / out["part_base"]
    return out


def rotatividade(caged: pd.DataFrame, estoque: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    """Taxa de rotatividade = min(admissões, desligamentos) / estoque inicial (RAIS de 31/12
    do ano anterior). Desligamentos chegam negativos no CAGED (tipo = -1, n_mov > 0)."""
    adm = caged[caged["tipo"] == 1].groupby(por)["n_mov"].sum().rename("admissoes")
    des = caged[caged["tipo"] == -1].groupby(por)["n_mov"].sum().rename("desligamentos")
    est = estoque.groupby(por)["n_vinculos"].sum().rename("estoque")
    t = pd.concat([adm, des, est], axis=1).fillna(0).reset_index()
    ok = t["estoque"] >= N_MINIMO
    t["taxa_rotatividade"] = np.where(ok, np.minimum(t["admissoes"], t["desligamentos"]) / t["estoque"].clip(lower=1), np.nan)
    t["saldo"] = t["admissoes"] - t["desligamentos"]
    return t
