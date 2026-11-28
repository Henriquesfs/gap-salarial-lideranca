"""Tratamento da RAIS Vínculos: limpeza, regras de negócio e agregação por lotes.

Cada arquivo regional é lido em streaming, em blocos de ~64 MB. Para cada bloco:
padroniza nomes de colunas, converte tipos, aplica as regras de validade, calcula o
salário-hora e acumula três cubos agregados, além de sortear a amostra estratificada.
Ao fim do arquivo, os agregados vão para parquet em data/staging e o .7z é apagado.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from . import baixar, cbo, config, dominios as d, extrair

log = logging.getLogger(__name__)

# Nome original (normalizado) -> nome padronizado no projeto.
COLUNAS = {
    "ind vinculo ativo 31/12 - codigo": "ativo",
    "cbo 2002 ocupacao - codigo": "cbo",
    "cnae 2.0 classe - codigo": "cnae_classe",
    "escolaridade apos 2005 - codigo": "escolaridade_cod",
    "qtd hora contr": "horas",
    "idade": "idade",
    "mes admissao - codigo": "mes_admissao",
    "municipio - codigo": "municipio",
    "natureza juridica - codigo": "natureza",
    "raca cor - codigo": "raca_cod",
    "vl rem dezembro nom": "rem_dez",
    "vl rem media nom": "rem_media",
    "sexo - codigo": "sexo_cod",
    "tipo vinculo - codigo": "tipo_vinculo",
    "ind trabalho intermitente - codigo": "intermitente",
}

CHAVES_PERFIL = ["uf", "sexo", "raca", "faixa_etaria", "escolaridade", "nivel", "setor"]
CHAVES_SETOR = ["uf", "sexo", "raca", "nivel", "cnae_secao", "setor"]
CHAVES_OCUPACAO = ["regiao", "sexo", "raca", "setor", "cbo"]
MEDIDAS = ["n_vinculos", "n_salario", "soma_remuneracao", "soma_salario_hora", "soma_salario_hora2", "soma_horas"]
COLUNAS_AMOSTRA = ["uf", "sexo", "raca", "faixa_etaria", "escolaridade", "nivel", "cnae_secao",
                   "setor", "cbo", "horas", "remuneracao", "salario_hora", "peso"]


def resolver_colunas(arquivo: Path, esperadas: dict[str, str]) -> tuple[dict[str, str], str, str]:
    """Lê o cabeçalho, detecta separador e codificação e casa os nomes originais com os
    nomes padronizados do projeto (comparação sem acentos e sem diferença de caixa)."""
    linha, encoding = extrair.cabecalho_7z(arquivo)
    linha = linha.strip()
    sep = ";" if linha.count(";") > linha.count(",") else ","
    originais = next(csv.reader(io.StringIO(linha), delimiter=sep))
    mapa = {}
    for orig in originais:
        chave = cbo.normalizar(orig)
        if chave in esperadas:
            mapa[orig] = esperadas[chave]
    faltando = set(esperadas.values()) - set(mapa.values())
    if faltando:
        raise ValueError(f"{arquivo.name}: colunas não encontradas: {sorted(faltando)}")
    return mapa, sep, encoding


def _num(s: pd.Series) -> pd.Series:
    """Texto -> número. Aceita vírgula decimal e espaços. Valor inválido vira NaN."""
    s = s.str.strip()
    if s.str.contains(",", regex=False).any():
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def tratar_lote(df: pd.DataFrame, ano: int, nivel_cbo: np.ndarray, q: Counter) -> pd.DataFrame:
    """Aplica limpeza e regras de negócio. Retorna só vínculos ativos válidos, já tipados."""
    q["linhas_lidas"] += len(df)

    ativo = _num(df["ativo"])
    df = df[ativo == 1]
    q["descartado_inativo_31_12"] += int((ativo != 1).sum())

    sexo = d.mapear(_num(df["sexo_cod"]), d.SEXO_RAIS, 0)
    q["descartado_sexo_invalido"] += int((sexo == 0).sum())
    uf = (_num(df["municipio"]).fillna(0) // 10000).astype("int16").to_numpy()
    uf_ok = np.isin(uf, d.UFS_VALIDAS)
    q["descartado_uf_invalida"] += int(((sexo != 0) & ~uf_ok).sum())
    manter = (sexo != 0) & uf_ok
    df, sexo, uf = df[manter], sexo[manter], uf[manter]

    out = pd.DataFrame(index=df.index)
    out["uf"] = uf
    out["regiao"] = (uf // 10).astype("int8")
    out["sexo"] = sexo
    out["raca"] = d.mapear(_num(df["raca_cod"]), d.RACA_RAIS, 9)
    out["escolaridade"] = d.mapear(_num(df["escolaridade_cod"]), d.ESCOLARIDADE_GRUPO, 9)
    out["faixa_etaria"] = d.faixa_etaria(_num(df["idade"]))
    cod_cbo = _num(df["cbo"]).fillna(-1).astype("int64").to_numpy()
    cbo_ok = (cod_cbo > 0) & (cod_cbo < 1_000_000)
    out["cbo"] = np.where(cbo_ok, cod_cbo, -1).astype("int32")
    out["nivel"] = np.where(cbo_ok, nivel_cbo[np.where(cbo_ok, cod_cbo, 0)], 1).astype("int8")
    divisao = _num(df["cnae_classe"].str.strip().str[:2]).fillna(0).astype("int64").clip(0, 99).to_numpy()
    out["cnae_secao"] = d.DIVISAO_SECAO[divisao]
    out["setor"] = d.setor_por_natureza(_num(df["natureza"]))

    q["raca_nao_informada"] += int((out["raca"] == 9).sum())
    q["escolaridade_nao_informada"] += int((out["escolaridade"] == 9).sum())
    q["idade_invalida"] += int((out["faixa_etaria"] == 9).sum())
    q["cbo_invalida"] += int((~cbo_ok).sum())
    q["cnae_invalida"] += int((out["cnae_secao"] == "Z").sum())

    # Remuneração de referência: dezembro; se zerada (afastamento), média do ano.
    rem_dez = _num(df["rem_dez"]).fillna(0).to_numpy(dtype="float64")
    rem_media = _num(df["rem_media"]).fillna(0).to_numpy(dtype="float64")
    rem = np.where(rem_dez > 0, rem_dez, rem_media)
    horas = _num(df["horas"]).fillna(0).to_numpy(dtype="float64")
    sal_hora = np.divide(rem, horas * config.SEMANAS_POR_MES, out=np.zeros_like(rem), where=horas > 0)

    sm_hora = config.SALARIO_MINIMO[ano] / 220
    mes_adm = _num(df["mes_admissao"]).fillna(0).to_numpy()
    tipo_vinc = _num(df["tipo_vinculo"]).fillna(0).to_numpy()
    intermit = _num(df["intermitente"]).fillna(0).to_numpy()

    # Regras de exclusão das métricas salariais, avaliadas em ordem. O vínculo continua
    # contado no funil (headcount), só não entra nas médias de salário.
    motivos = [
        ("sal_excl_sem_remuneracao", rem <= 0),
        ("sal_excl_horas_fora_1_60", (horas < config.HORAS_MIN) | (horas > config.HORAS_MAX)),
        ("sal_excl_admitido_dezembro", mes_adm == 12),
        ("sal_excl_aprendiz", tipo_vinc == 55),
        ("sal_excl_intermitente", intermit == 1),
        ("sal_excl_salario_hora_baixo", sal_hora < config.SAL_HORA_MIN_SM * sm_hora),
        ("sal_excl_salario_hora_outlier", sal_hora > config.SAL_HORA_MAX_SM * sm_hora),
    ]
    invalido = np.zeros(len(out), dtype=bool)
    for nome, cond in motivos:
        novo = cond & ~invalido
        q[nome] += int(novo.sum())
        invalido |= cond
    valido = ~invalido

    out["valido"] = valido
    out["horas"] = horas.astype("float32")
    out["remuneracao"] = rem
    out["salario_hora"] = sal_hora
    q["vinculos_ativos_validos"] += len(out)
    q["vinculos_com_salario_valido"] += int(valido.sum())
    return out


def _medidas(df: pd.DataFrame) -> pd.DataFrame:
    v = df["valido"].to_numpy()
    m = pd.DataFrame(index=df.index)
    m["n_vinculos"] = np.ones(len(df), dtype="int64")
    m["n_salario"] = v.astype("int64")
    m["soma_remuneracao"] = np.where(v, df["remuneracao"], 0.0)
    m["soma_salario_hora"] = np.where(v, df["salario_hora"], 0.0)
    m["soma_salario_hora2"] = np.where(v, df["salario_hora"] ** 2, 0.0)
    m["soma_horas"] = np.where(v, df["horas"], 0.0)
    return m


def _agregar(df: pd.DataFrame, med: pd.DataFrame, chaves: list[str]) -> pd.DataFrame:
    return pd.concat([df[chaves], med], axis=1).groupby(chaves, as_index=False, sort=False)[MEDIDAS].sum()


def _compactar(partes: list[pd.DataFrame], chaves: list[str]) -> list[pd.DataFrame]:
    return [pd.concat(partes, ignore_index=True).groupby(chaves, as_index=False, sort=False)[MEDIDAS].sum()]


def processar_arquivo(arquivo: Path, ano: int, nivel_cbo: np.ndarray, indice: int) -> dict:
    t0 = time.time()
    mapa, sep, encoding = resolver_colunas(arquivo, COLUNAS)
    q: Counter = Counter()
    cubos = {"perfil": [], "setor": [], "ocupacao": []}
    chaves = {"perfil": CHAVES_PERFIL, "setor": CHAVES_SETOR, "ocupacao": CHAVES_OCUPACAO}
    amostras = []
    taxa = np.zeros(10)
    for k, v in config.TAXA_AMOSTRA.items():
        taxa[k] = v

    for i, lote in enumerate(extrair.ler_7z_em_blocos(arquivo, list(mapa), sep, encoding)):
        bruto = lote.to_pandas().rename(columns=mapa)
        df = tratar_lote(bruto, ano, nivel_cbo, q)
        med = _medidas(df)
        for nome in cubos:
            cubos[nome].append(_agregar(df, med, chaves[nome]))
            if len(cubos[nome]) >= 25:
                cubos[nome] = _compactar(cubos[nome], chaves[nome])

        rng = np.random.default_rng([config.SEMENTE, ano, indice, i])
        nivel = df["nivel"].to_numpy()
        sel = df["valido"].to_numpy() & (rng.random(len(df)) < taxa[nivel])
        am = df.loc[sel, [c for c in COLUNAS_AMOSTRA if c != "peso"]].copy()
        am["peso"] = (1.0 / taxa[nivel[sel]]).astype("float32")
        amostras.append(am)
        if i % 10 == 0:
            log.info("%s %d: lote %d, %s linhas lidas", arquivo.name, ano, i, f"{q['linhas_lidas']:,}")

    destino = config.DIR_STAGING / "rais" / str(ano)
    destino.mkdir(parents=True, exist_ok=True)
    base = arquivo.stem.replace("RAIS_VINC_PUB_", "").lower()
    for nome, partes in cubos.items():
        cubo = _compactar(partes, chaves[nome])[0]
        cubo.insert(0, "ano", np.int16(ano))
        cubo.to_parquet(destino / f"{base}_{nome}.parquet", index=False)
    am = pd.concat(amostras, ignore_index=True)
    am.insert(0, "ano", np.int16(ano))
    am["remuneracao"] = am["remuneracao"].round(2)
    am["salario_hora"] = am["salario_hora"].astype("float32")
    am.to_parquet(destino / f"{base}_amostra.parquet", index=False)

    relatorio = {
        "fonte": "RAIS Vínculos",
        "arquivo": arquivo.name,
        "ano": ano,
        "separador": sep,
        "codificacao": encoding,
        "colunas_lidas": mapa,
        "contagens": dict(q),
        "linhas_amostra": len(am),
        "segundos": round(time.time() - t0, 1),
    }
    config.DIR_QUALIDADE.mkdir(parents=True, exist_ok=True)
    (config.DIR_QUALIDADE / f"rais_{ano}_{base}.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("%s %d concluído: %s lidas, %s válidos, %.0f s", arquivo.name, ano,
             f"{q['linhas_lidas']:,}", f"{q['vinculos_ativos_validos']:,}", time.time() - t0)
    return relatorio


def processar_todos(anos: list[int], apagar_brutos: bool = True, esperar: bool = True) -> None:
    """Processa os arquivos na ordem do download, esperando cada um terminar de baixar."""
    dim = cbo.construir_dim_ocupacao()
    nivel_cbo = cbo.vetor_nivel(dim)
    tarefas = baixar.tarefas_rais(anos)
    for indice, (remoto, arquivo) in enumerate(tarefas):
        marca = baixar.marcador(arquivo)
        if marca.exists():
            continue
        while esperar and not arquivo.exists():
            time.sleep(20)
        ano = int(arquivo.parent.name)
        processar_arquivo(arquivo, ano, nivel_cbo, indice)
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
        if apagar_brutos:
            arquivo.unlink()
