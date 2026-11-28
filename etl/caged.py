"""Tratamento do Novo CAGED (movimentações mensais de admissão e desligamento).

Cada mês publicado tem três arquivos:
- MOV: movimentações declaradas no prazo;
- FOR: declarações fora do prazo, que pertencem a competências anteriores;
- EXC: exclusões de declarações já publicadas (registros errados ou duplicados).

Seguindo a metodologia do MTE, as linhas de MOV e FOR entram com peso +1 e as de EXC com
peso -1, todas na competência da movimentação. É assim que as duplicatas e erros de
declaração são removidos da base.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from . import baixar, cbo, config, dominios as d, extrair
from .rais import _num, resolver_colunas

log = logging.getLogger(__name__)

COLUNAS = {
    "competenciamov": "competencia",
    "uf": "uf",
    "secao": "cnae_secao",
    "saldomovimentacao": "saldo",
    "cbo2002ocupacao": "cbo",
    "graudeinstrucao": "escolaridade_cod",
    "idade": "idade",
    "horascontratuais": "horas",
    "racacor": "raca_cod",
    "sexo": "sexo_cod",
    "tipomovimentacao": "tipo_mov",
    "indtrabintermitente": "intermitente",
    "salario": "salario",
    "indicadoraprendiz": "aprendiz",
}

CHAVES_PERFIL = ["competencia", "uf", "sexo", "raca", "faixa_etaria", "nivel", "tipo"]
CHAVES_MOTIVO = ["competencia", "regiao", "sexo", "raca", "nivel", "motivo"]
CHAVES_SETOR = ["competencia", "uf", "sexo", "raca", "nivel", "cnae_secao", "tipo"]
MEDIDAS = ["n_mov", "n_salario", "soma_salario", "soma_salario_hora"]


def _tabela_sm() -> np.ndarray:
    """Salário mínimo por ano (posição = ano - 2000). Anos sem valor usam o mais recente."""
    t = np.full(100, config.SALARIO_MINIMO[max(config.SALARIO_MINIMO)], dtype="float64")
    for ano, valor in config.SALARIO_MINIMO.items():
        t[ano - 2000] = valor
    return t


def tratar_lote(df: pd.DataFrame, peso: int, nivel_cbo: np.ndarray, inicio: int, fim: int,
                q: Counter) -> pd.DataFrame:
    q["linhas_lidas"] += len(df)
    comp = _num(df["competencia"]).fillna(0).astype("int64").to_numpy()
    no_periodo = (comp >= inicio) & (comp <= fim)
    q["descartado_fora_do_periodo"] += int((~no_periodo).sum())
    df, comp = df[no_periodo], comp[no_periodo]

    sexo = d.mapear(_num(df["sexo_cod"]), d.SEXO_CAGED, 0)
    uf = _num(df["uf"]).fillna(0).astype("int16").to_numpy()
    uf_ok = np.isin(uf, d.UFS_VALIDAS)
    q["descartado_sexo_nao_identificado"] += int((sexo == 0).sum())
    q["descartado_uf_invalida"] += int(((sexo != 0) & ~uf_ok).sum())
    manter = (sexo != 0) & uf_ok
    df, comp, sexo, uf = df[manter], comp[manter], sexo[manter], uf[manter]

    out = pd.DataFrame(index=df.index)
    out["competencia"] = comp.astype("int32")
    out["uf"] = uf
    out["regiao"] = (uf // 10).astype("int8")
    out["sexo"] = sexo
    out["raca"] = d.mapear(_num(df["raca_cod"]), d.RACA_CAGED, 9)
    out["faixa_etaria"] = d.faixa_etaria(_num(df["idade"]))
    cod_cbo = _num(df["cbo"]).fillna(-1).astype("int64").to_numpy()
    cbo_ok = (cod_cbo > 0) & (cod_cbo < 1_000_000)
    out["nivel"] = np.where(cbo_ok, nivel_cbo[np.where(cbo_ok, cod_cbo, 0)], 1).astype("int8")
    secao = df["cnae_secao"].fillna("Z").str.strip().str.upper()
    out["cnae_secao"] = np.where(secao.isin(list(d.SECAO_NOME)), secao, "Z")
    saldo = _num(df["saldo"]).fillna(0)
    out["tipo"] = np.where(saldo.to_numpy() > 0, 1, -1).astype("int8")   # 1 admissão, -1 desligamento
    out["motivo"] = d.motivo_caged(_num(df["tipo_mov"]), saldo)
    q["raca_nao_informada"] += int((out["raca"] == 9).sum())

    salario = _num(df["salario"]).fillna(0).to_numpy(dtype="float64")
    horas = _num(df["horas"]).fillna(0).to_numpy(dtype="float64")
    sal_hora = np.divide(salario, horas * config.SEMANAS_POR_MES, out=np.zeros_like(salario), where=horas > 0)
    sm_hora = _tabela_sm()[np.clip(comp // 100 - 2000, 0, 99)] / 220
    motivos = [
        ("sal_excl_sem_salario", salario <= 0),
        ("sal_excl_horas_fora_1_60", (horas < config.HORAS_MIN) | (horas > config.HORAS_MAX)),
        ("sal_excl_aprendiz", _num(df["aprendiz"]).fillna(0).to_numpy() == 1),
        ("sal_excl_intermitente", _num(df["intermitente"]).fillna(0).to_numpy() == 1),
        ("sal_excl_salario_hora_baixo", sal_hora < config.SAL_HORA_MIN_SM * sm_hora),
        ("sal_excl_salario_hora_outlier", sal_hora > config.SAL_HORA_MAX_SM * sm_hora),
    ]
    invalido = np.zeros(len(out), dtype=bool)
    for nome, cond in motivos:
        q[nome] += int((cond & ~invalido).sum())
        invalido |= cond
    valido = ~invalido

    out["n_mov"] = np.full(len(out), peso, dtype="int64")
    out["n_salario"] = np.where(valido, peso, 0).astype("int64")
    out["soma_salario"] = np.where(valido, salario * peso, 0.0)
    out["soma_salario_hora"] = np.where(valido, sal_hora * peso, 0.0)
    q["movimentacoes_validas"] += len(out)
    return out


def processar_competencia(comp_publicacao: int, nivel_cbo: np.ndarray) -> dict:
    t0 = time.time()
    q_total: dict[str, dict] = {}
    perfil, motivo, setor = [], [], []
    for tipo, peso in (("MOV", 1), ("FOR", 1), ("EXC", -1)):
        arquivo = config.DIR_BRUTOS / "caged" / f"CAGED{tipo}{comp_publicacao}.7z"
        if not arquivo.exists():
            log.warning("arquivo ausente: %s", arquivo.name)
            continue
        mapa, sep, encoding = resolver_colunas(arquivo, COLUNAS)
        q: Counter = Counter()
        for lote in extrair.ler_7z_em_blocos(arquivo, list(mapa), sep, encoding):
            df = tratar_lote(lote.to_pandas().rename(columns=mapa), peso, nivel_cbo,
                             config.CAGED_INICIO, config.CAGED_FIM, q)
            perfil.append(df.groupby(CHAVES_PERFIL, as_index=False, sort=False)[MEDIDAS].sum())
            motivo.append(df.groupby(CHAVES_MOTIVO, as_index=False, sort=False)[["n_mov"]].sum())
            setor.append(df.groupby(CHAVES_SETOR, as_index=False, sort=False)[["n_mov"]].sum())
        q_total[tipo] = {"arquivo": arquivo.name, "codificacao": encoding, "separador": sep, **dict(q)}

    destino = config.DIR_STAGING / "caged"
    destino.mkdir(parents=True, exist_ok=True)
    for nome, partes, chaves, med in (("perfil", perfil, CHAVES_PERFIL, MEDIDAS),
                                      ("motivo", motivo, CHAVES_MOTIVO, ["n_mov"]),
                                      ("setor", setor, CHAVES_SETOR, ["n_mov"])):
        if partes:
            pd.concat(partes, ignore_index=True).groupby(chaves, as_index=False)[med].sum() \
              .to_parquet(destino / f"{comp_publicacao}_{nome}.parquet", index=False)

    relatorio = {"fonte": "Novo CAGED", "competencia_publicacao": comp_publicacao,
                 "arquivos": q_total, "segundos": round(time.time() - t0, 1)}
    config.DIR_QUALIDADE.mkdir(parents=True, exist_ok=True)
    (config.DIR_QUALIDADE / f"caged_{comp_publicacao}.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("CAGED %d concluído em %.0f s", comp_publicacao, time.time() - t0)
    return relatorio


def processar_todos(inicio: int, fim: int, apagar_brutos: bool = True) -> None:
    nivel_cbo = cbo.vetor_nivel(cbo.construir_dim_ocupacao())
    for comp in baixar.competencias(inicio, fim):
        mov = config.DIR_BRUTOS / "caged" / f"CAGEDMOV{comp}.7z"
        marca = baixar.marcador(mov)
        if marca.exists():
            continue
        for _ in range(60):  # espera o download terminar (até 20 min)
            if mov.exists():
                break
            time.sleep(20)
        processar_competencia(comp, nivel_cbo)
        marca.parent.mkdir(parents=True, exist_ok=True)
        for tipo in ("MOV", "FOR", "EXC"):
            arq = config.DIR_BRUTOS / "caged" / f"CAGED{tipo}{comp}.7z"
            baixar.marcador(arq).write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
            if apagar_brutos:
                arq.unlink(missing_ok=True)
