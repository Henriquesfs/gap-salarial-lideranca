"""Consolida os agregados parciais do staging nas tabelas finais (dimensões e fatos).

Saída: arquivos parquet em data/final, um por tabela do banco. A carga no PostgreSQL
(carregar.py) só copia esses arquivos.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from . import apis, caged, cbo, config, dominios as d, rais

log = logging.getLogger(__name__)


def _consolidar(padrao: str, chaves: list[str], medidas: list[str]) -> pd.DataFrame:
    arquivos = sorted(config.DIR_STAGING.glob(padrao))
    if not arquivos:
        raise FileNotFoundError(f"nenhum arquivo em staging para {padrao}")
    df = pd.concat([pd.read_parquet(a) for a in arquivos], ignore_index=True)
    df = df.groupby(chaves, as_index=False, sort=True)[medidas].sum()
    # No CAGED, exclusões podem zerar uma combinação inteira. Linhas zeradas saem.
    return df[(df[medidas] != 0).any(axis=1)].reset_index(drop=True)


def _tipar(df: pd.DataFrame) -> pd.DataFrame:
    tipos = {"ano": "int16", "uf": "int16", "regiao": "int8", "sexo": "int8", "raca": "int8",
             "faixa_etaria": "int8", "escolaridade": "int8", "nivel": "int8", "setor": "int8",
             "cbo": "int32", "competencia": "int32", "tipo": "int8", "motivo": "int8"}
    for c, t in tipos.items():
        if c in df:
            df[c] = df[c].astype(t)
    for c in df.columns:
        if c.startswith("soma_"):
            df[c] = df[c].round(4)
    return df


def dimensoes(dim_cbo: pd.DataFrame) -> dict[str, pd.DataFrame]:
    dims = {
        "dim_uf": apis.ufs_ibge(),  # API de Localidades do IBGE
        "dim_sexo": pd.DataFrame({"sexo": list(d.SEXO_NOME), "nome": list(d.SEXO_NOME.values())}),
        "dim_raca": pd.DataFrame({"raca": list(d.RACA_NOME), "nome": list(d.RACA_NOME.values()),
                                  "grupo": [d.RACA_GRUPO[r] for r in d.RACA_NOME]}),
        "dim_grupo": pd.DataFrame(
            [{"sexo": s, "raca": r, "grupo_racial": d.RACA_GRUPO[r],
              "grupo_interseccional": d.grupo_interseccional(s, r)}
             for s in d.SEXO_NOME for r in d.RACA_NOME]),
        "dim_faixa_etaria": pd.DataFrame({"faixa_etaria": list(d.FAIXA_NOME), "nome": list(d.FAIXA_NOME.values())}),
        "dim_escolaridade": pd.DataFrame({"escolaridade": list(d.ESCOLARIDADE_NOME),
                                          "nome": list(d.ESCOLARIDADE_NOME.values())}),
        "dim_nivel": pd.DataFrame({"nivel": list(d.NIVEL_NOME), "nome": list(d.NIVEL_NOME.values()),
                                   "lideranca": [n >= 3 for n in d.NIVEL_NOME]}),
        "dim_secao_cnae": pd.DataFrame({"cnae_secao": list(d.SECAO_NOME), "nome": list(d.SECAO_NOME.values())}),
        "dim_setor": pd.DataFrame({"setor": list(d.SETOR_NOME), "nome": list(d.SETOR_NOME.values())}),
        "dim_motivo": pd.DataFrame({"motivo": list(d.MOTIVO_NOME), "nome": list(d.MOTIVO_NOME.values())}),
        "dim_ocupacao": dim_cbo[["cbo", "titulo", "familia", "grande_grupo", "nivel", "regra_nivel", "desativada"]],
    }
    comps = caged_competencias()
    dims["dim_competencia"] = pd.DataFrame({
        "competencia": comps,
        "ano": [c // 100 for c in comps],
        "mes": [c % 100 for c in comps],
        "trimestre": [(c % 100 - 1) // 3 + 1 for c in comps],
        "data": pd.to_datetime([f"{c // 100}-{c % 100:02d}-01" for c in comps]).date,
    })
    return dims


def caged_competencias() -> list[int]:
    from .baixar import competencias
    return competencias(config.CAGED_INICIO, config.CAGED_FIM)


def completar_ocupacoes(dim_cbo: pd.DataFrame, codigos: np.ndarray) -> pd.DataFrame:
    """Garante integridade referencial: CBOs presentes nos fatos e ausentes no catálogo
    entram na dimensão como "não catalogada" no nível base."""
    faltam = sorted(set(codigos.tolist()) - set(dim_cbo["cbo"].tolist()))
    if faltam:
        log.info("%d códigos CBO fora do catálogo", len(faltam))
        extra = pd.DataFrame({
            "cbo": faltam,
            "titulo": ["Não informada" if c == -1 else "Ocupação não catalogada" for c in faltam],
            "familia": [max(c, 0) // 100 for c in faltam],
            "grande_grupo": [max(c, 0) // 100000 for c in faltam],
            "nivel": 1,
            "regra_nivel": "código fora do catálogo",
            "desativada": False,
        })
        dim_cbo = pd.concat([dim_cbo, extra], ignore_index=True)
    return dim_cbo.astype({"cbo": "int32", "familia": "int16", "grande_grupo": "int8", "nivel": "int8"})


def executar() -> dict[str, pd.DataFrame]:
    config.DIR_FINAL.mkdir(parents=True, exist_ok=True)
    tabelas: dict[str, pd.DataFrame] = {}

    tabelas["fato_rais_perfil"] = _consolidar("rais/*/*_perfil.parquet", ["ano"] + rais.CHAVES_PERFIL, rais.MEDIDAS)
    tabelas["fato_rais_setor"] = _consolidar("rais/*/*_setor.parquet", ["ano"] + rais.CHAVES_SETOR, rais.MEDIDAS)
    tabelas["fato_rais_ocupacao"] = _consolidar("rais/*/*_ocupacao.parquet", ["ano"] + rais.CHAVES_OCUPACAO, rais.MEDIDAS)
    amostra = pd.concat([pd.read_parquet(a) for a in sorted(config.DIR_STAGING.glob("rais/*/*_amostra.parquet"))],
                        ignore_index=True)
    tabelas["amostra_rais"] = amostra

    tabelas["fato_caged_perfil"] = _consolidar("caged/*_perfil.parquet", caged.CHAVES_PERFIL, caged.MEDIDAS)
    tabelas["fato_caged_motivo"] = _consolidar("caged/*_motivo.parquet", caged.CHAVES_MOTIVO, ["n_mov"])
    tabelas["fato_caged_setor"] = _consolidar("caged/*_setor.parquet", caged.CHAVES_SETOR, ["n_mov"])

    dim_cbo = cbo.construir_dim_ocupacao()
    codigos = np.concatenate([tabelas["fato_rais_ocupacao"]["cbo"].unique(), amostra["cbo"].unique()])
    dim_cbo = completar_ocupacoes(dim_cbo, codigos)
    tabelas.update(dimensoes(dim_cbo))

    for nome, df in tabelas.items():
        df = _tipar(df)
        tabelas[nome] = df
        df.to_parquet(config.DIR_FINAL / f"{nome}.parquet", index=False)
        log.info("%-22s %10s linhas", nome, f"{len(df):,}")

    ref = apis.series_referencia()  # salário mínimo e IPCA (Banco Central, SGS)
    ref.to_parquet(config.DIR_FINAL / "ref_serie_mensal.parquet", index=False)
    tabelas["ref_serie_mensal"] = ref
    log.info("%-22s %10s linhas", "ref_serie_mensal", f"{len(ref):,}")
    (config.DIR_FINAL / "api_coleta.json").write_text(json.dumps(apis.COLETAS, ensure_ascii=False), encoding="utf-8")

    relatorios = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(config.DIR_QUALIDADE.glob("*.json"))]
    (config.DIR_FINAL / "etl_qualidade.json").write_text(json.dumps(relatorios, ensure_ascii=False), encoding="utf-8")
    return tabelas
