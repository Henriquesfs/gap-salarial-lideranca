"""Painel: gap salarial e funil de liderança por gênero e raça/cor (RAIS + Novo CAGED)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import consultas as q
import estilo as e
import metricas as m

st.set_page_config(page_title="Gap salarial e liderança", page_icon=":material/bar_chart:", layout="wide")

DIMS = q.dimensoes()
ANOS_RAIS = sorted(q.sql("SELECT DISTINCT ano FROM fato_rais_perfil")["ano"].astype(int).tolist(), reverse=True)
ANOS_CAGED = sorted(q.sql("SELECT DISTINCT ano FROM dim_competencia")["ano"].astype(int).tolist(), reverse=True)
MESES_CAGED = q.sql("SELECT ano, COUNT(*) AS meses FROM dim_competencia GROUP BY ano").set_index("ano")["meses"].to_dict()
NIVEIS = DIMS["dim_nivel"].sort_values("nivel")
NIVEL_NOME = NIVEIS.set_index("nivel")["nome"].to_dict()
ORDEM_NIVEL = list(NIVEL_NOME.values())


def opcoes(tab: str, chave: str) -> dict:
    d = DIMS[tab]
    return dict(zip(d["nome"], d[chave]))


def selecao(rotulo: str, mapa: dict, padrao=None, ajuda: str | None = None) -> tuple:
    """Multiselect em que vazio significa "todos". Devolve tupla vazia quando não há filtro."""
    nomes = list(mapa)
    escolha = st.multiselect(rotulo, nomes, default=padrao or [], help=ajuda, placeholder="Todos")
    if not escolha or len(escolha) == len(nomes):
        return ()
    return tuple(mapa[n] for n in escolha)


# ----------------------------------------------------------------------------- filtros
with st.sidebar:
    st.header("Filtros")
    st.caption("Campo vazio = sem filtro.")
    ano = st.selectbox("Ano (RAIS, estoque em 31/12)", ANOS_RAIS, index=0)
    ufs_df = DIMS["dim_uf"].sort_values("sigla")
    regioes = st.multiselect("Região", sorted(ufs_df["regiao_nome"].unique()), default=[], placeholder="Todas")
    ufs_disp = ufs_df[ufs_df["regiao_nome"].isin(regioes)] if regioes else ufs_df
    ufs_sel = st.multiselect("Estado (UF)", ufs_disp["sigla"].tolist(), default=[], placeholder="Todos",
                             help="Vazio = todas as UFs da(s) região(ões) escolhida(s).")
    if ufs_sel:
        ufs = tuple(ufs_disp[ufs_disp["sigla"].isin(ufs_sel)]["uf"].tolist())
    elif regioes:
        ufs = tuple(ufs_disp["uf"].tolist())
    else:
        ufs = ()
    setores = selecao("Setor", opcoes("dim_setor", "setor"), padrao=["Privado empresarial"],
                      ajuda="Natureza jurídica do empregador. O painel abre no setor privado (liderança corporativa). "
                            "No setor público, cargos de direção de escolas e unidades de saúde mudam bastante o retrato.")
    sexos = selecao("Sexo", opcoes("dim_sexo", "sexo"))
    racas = selecao("Raça/cor", opcoes("dim_raca", "raca"))
    faixas = selecao("Faixa etária", opcoes("dim_faixa_etaria", "faixa_etaria"))
    escolaridades = selecao("Escolaridade", opcoes("dim_escolaridade", "escolaridade"))
    st.divider()
    grupos_exibidos = st.multiselect(
        "Grupos exibidos nos gráficos", list(e.COR_GRUPO), default=e.GRUPOS_PRINCIPAIS, placeholder="Escolha os grupos",
        help="Cruzamento de sexo e raça/cor. Pessoas negras = pretas + pardas (critério do IBGE).")

F = q.Filtros(anos=(ano,), ufs=ufs, setores=setores, sexos=sexos, racas=racas,
              faixas=faixas, escolaridades=escolaridades)


def com(f: q.Filtros, **mudancas) -> q.Filtros:
    return q.Filtros(**{**f.__dict__, **mudancas})


def visiveis(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["grupo_interseccional"].isin(grupos_exibidos)]


# Estoque da RAIS comparável ao CAGED: sem o setor público (natureza jurídica 1xxx), cujos
# servidores estatutários não aparecem no CAGED. O CAGED não tem setor, então o filtro de
# setor do painel não se aplica aos indicadores de fluxo.
SETORES_CELETISTAS = (2, 3, 4)


def nota_filtros(*aplicados: str) -> None:
    st.caption("Filtros aplicados neste gráfico: " + ", ".join(aplicados) + ".")


def grafico(fig, titulo: str | None, altura: int = 400, legenda: bool = True, **kwargs):
    """Título em Markdown acima do gráfico (não disputa espaço com a legenda do Plotly)."""
    if titulo:
        st.markdown(f"**{titulo}**")
    kwargs.setdefault("config", {"scrollZoom": False, "displaylogo": False})
    return st.plotly_chart(e.layout(fig, None, altura, legenda), **kwargs)


def tabela(df: pd.DataFrame, nome: str) -> None:
    with st.expander(f"Ver tabela: {nome}"):
        st.dataframe(df, hide_index=True)


# ----------------------------------------------------------------------------- cabeçalho
st.title("Gap salarial e funil de liderança")
st.caption(
    f"RAIS {min(ANOS_RAIS)}–{max(ANOS_RAIS)} (vínculos ativos em 31/12) e Novo CAGED "
    f"{min(ANOS_CAGED)}–{max(ANOS_CAGED)} (movimentações mensais). Fonte: PDET/MTE. Valores nominais. "
    "Grupos com menos de 30 vínculos não exibem média.")

secoes = ["Visão geral", "Gap salarial", "Funil de liderança", "Drill-down", "Dinâmica do emprego", "Qualidade dos dados"]
secao = st.segmented_control("Seção", secoes, default=secoes[0], required=True, label_visibility="collapsed")


# ----------------------------------------------------------------------------- dados base
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def base_grupo_nivel(f: q.Filtros) -> pd.DataFrame:
    return m.rotular(q.agregar("fato_rais_perfil", ["sexo", "raca", "nivel"], f), DIMS)


def kpis_rais(df: pd.DataFrame) -> dict:
    """Indicadores-síntese a partir do cubo sexo x raça x nível."""
    out = {}
    s = df.groupby("sexo_nome")[["n_salario", "soma_salario_hora"]].sum()
    sh = (s["soma_salario_hora"] / s["n_salario"]).where(s["n_salario"] >= m.N_MINIMO)
    out["gap_sexo"] = m.gap(sh.get("Mulher", np.nan), sh.get("Homem", np.nan))
    g = df.groupby("grupo_interseccional")[["n_salario", "soma_salario_hora"]].sum()
    gh = (g["soma_salario_hora"] / g["n_salario"]).where(g["n_salario"] >= m.N_MINIMO)
    out["gap_mn_hb"] = m.gap(gh.get("Mulher negra", np.nan), gh.get("Homem branco", np.nan))
    tot = df["n_vinculos"].sum()
    por_nivel = df.groupby("nivel_nome")["n_vinculos"].sum()
    mulheres = df[df["sexo_nome"] == "Mulher"].groupby("nivel_nome")["n_vinculos"].sum()
    negros = df[df["grupo_racial"] == "Negra"].groupby("nivel_nome")["n_vinculos"].sum()
    mn = df[df["grupo_interseccional"] == "Mulher negra"].groupby("nivel_nome")["n_vinculos"].sum()
    lid = ["Gerência", "Direção"]
    out["mulheres_direcao"] = mulheres.get("Direção", 0) / por_nivel.get("Direção", np.nan)
    out["mulheres_base"] = mulheres.get("Base", 0) / por_nivel.get("Base", np.nan)
    out["negros_lideranca"] = negros.reindex(lid).sum() / por_nivel.reindex(lid).sum()
    out["negros_total"] = negros.sum() / tot if tot else np.nan
    out["mn_direcao"] = mn.get("Direção", 0) / por_nivel.get("Direção", np.nan)
    out["mn_total"] = mn.sum() / tot if tot else np.nan
    return out


# ============================================================================= VISÃO GERAL
if secao == "Visão geral":
    atual = base_grupo_nivel(F)
    if atual.empty:
        st.info("Nenhum vínculo com os filtros escolhidos.")
        st.stop()
    k = kpis_rais(atual)
    ant = base_grupo_nivel(com(F, anos=(ano - 1,))) if (ano - 1) in ANOS_RAIS else None
    ka = kpis_rais(ant) if ant is not None and not ant.empty else {}
    gc = q.gap_controlado("mulheres_x_homens", F)
    gc2 = q.gap_controlado("mulher_negra_x_homem_branco", F)

    def delta(chave):
        if chave in ka and np.isfinite(ka[chave]) and np.isfinite(k[chave]):
            return e.pp(k[chave] - ka[chave]) + f" vs {ano - 1}"
        return None

    st.subheader(f"Indicadores de {ano}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gap por hora: mulheres x homens", e.pct(k["gap_sexo"]), delta("gap_sexo"),
              delta_color="inverse",
              help="1 − (salário-hora médio das mulheres ÷ salário-hora médio dos homens). Positivo: mulheres ganham menos por hora.")
    c2.metric("Gap controlado: mulheres x homens", e.pct(gc["gap"]),
              help="Compara mulheres e homens da mesma UF, faixa etária, escolaridade, nível hierárquico, setor e raça/cor. "
                   f"Cobre {e.pct(gc['cobertura'], 0)} das mulheres (células com pelo menos 5 pessoas de cada sexo).")
    c3.metric("Gap: mulher negra x homem branco", e.pct(k["gap_mn_hb"]), delta("gap_mn_hb"), delta_color="inverse",
              help="Diferença bruta no salário-hora médio.")
    c4.metric("Controlado: mulher negra x homem branco", e.pct(gc2["gap"]),
              help="Mesma UF, faixa etária, escolaridade, nível hierárquico e setor. "
                   f"Cobre {e.pct(gc2['cobertura'], 0)} das mulheres negras.")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Mulheres na direção", e.pct(k["mulheres_direcao"]), delta("mulheres_direcao"),
              help=f"Na base da pirâmide elas são {e.pct(k['mulheres_base'])} dos vínculos.")
    afun = k["mulheres_direcao"] / k["mulheres_base"] if k["mulheres_base"] else np.nan
    c6.metric("Afunilamento feminino", f"{afun:.2f}".replace(".", ",") if np.isfinite(afun) else "—",
              help="Participação feminina na direção ÷ participação feminina na base. 1,00 = proporcional; abaixo de 1, elas somem ao subir.")
    c7.metric("Pessoas negras na liderança", e.pct(k["negros_lideranca"]), delta("negros_lideranca"),
              help=f"Gerência + direção. Entre todos os vínculos, pessoas negras são {e.pct(k['negros_total'])}.")
    c8.metric("Mulheres negras na direção", e.pct(k["mn_direcao"]), delta("mn_direcao"),
              help=f"Entre todos os vínculos, mulheres negras são {e.pct(k['mn_total'])}.")

    # Dois indicadores de fluxo (CAGED), em cargos de liderança.
    ano_c = max(a for a in ANOS_CAGED if MESES_CAGED.get(a, 0) == 12) if any(MESES_CAGED.get(a, 0) == 12 for a in ANOS_CAGED) else max(ANOS_CAGED)
    fc = com(F, anos=(ano_c,), niveis=(3, 4))
    cag = m.rotular(q.agregar("fato_caged_perfil", ["sexo", "raca", "tipo"], fc), DIMS)
    adm = cag[cag["tipo"] == 1].groupby("sexo_nome")[["n_salario", "soma_salario"]].sum()
    sal_adm = (adm["soma_salario"] / adm["n_salario"]).where(adm["n_salario"] >= m.N_MINIMO)
    estq = (q.agregar("fato_rais_perfil", ["sexo", "raca", "nivel"],
                      com(F, anos=(ano_c - 1,), niveis=(3, 4), setores=SETORES_CELETISTAS))
            if (ano_c - 1) in ANOS_RAIS else pd.DataFrame())
    st.subheader(f"Fluxo em cargos de gerência e direção ({ano_c}, Novo CAGED)")
    st.caption("O CAGED não informa o setor e não registra servidores estatutários. Estes indicadores "
               "valem para o emprego celetista, independentemente do filtro de setor.")
    d1, d2, d3, _ = st.columns(4)
    d1.metric("Gap no salário de admissão", e.pct(m.gap(sal_adm.get("Mulher", np.nan), sal_adm.get("Homem", np.nan))),
              help="Salário mensal médio declarado na admissão para gerência e direção: mulheres x homens.")
    if not estq.empty:
        rot = m.rotatividade(m.rotular(q.agregar("fato_caged_perfil", ["sexo", "raca", "nivel", "tipo"], fc), DIMS),
                             m.rotular(estq, DIMS), ["sexo_nome"]).set_index("sexo_nome")
        d2.metric("Rotatividade: mulheres", e.pct(rot["taxa_rotatividade"].get("Mulher", np.nan)),
                  help=f"min(admissões, desligamentos) em {ano_c} ÷ estoque em 31/12/{ano_c - 1} (RAIS).")
        d3.metric("Rotatividade: homens", e.pct(rot["taxa_rotatividade"].get("Homem", np.nan)))

    st.divider()
    g1, g2 = st.columns(2)
    with g1:
        part = m.participacao(atual, ["nivel", "nivel_nome"], "grupo_interseccional")
        part = visiveis(part).sort_values("nivel")
        fig = px.bar(part, y="nivel_nome", x="participacao", color="grupo_interseccional", orientation="h",
                     color_discrete_map=e.COR_GRUPO, category_orders={"nivel_nome": ORDEM_NIVEL[::-1],
                                                                       "grupo_interseccional": list(e.COR_GRUPO)},
                     labels={"participacao": "Participação no nível", "nivel_nome": "", "grupo_interseccional": "Grupo"},
                     custom_data=["n_vinculos"])
        fig.update_traces(hovertemplate="%{y}: %{x:.1%}<br>%{customdata[0]:,.0f} vínculos<extra>%{fullData.name}</extra>")
        fig.update_xaxes(tickformat=".0%")
        grafico(fig, "Quem ocupa cada nível do funil", 380)
    with g2:
        md = m.medias(atual, ["nivel", "nivel_nome", "grupo_interseccional"])
        md = visiveis(md).sort_values("nivel")
        fig = px.bar(md, x="nivel_nome", y="salario_hora", color="grupo_interseccional", barmode="group",
                     color_discrete_map=e.COR_GRUPO, category_orders={"nivel_nome": ORDEM_NIVEL, "grupo_interseccional": list(e.COR_GRUPO)},
                     labels={"salario_hora": "Salário-hora médio (R$)", "nivel_nome": "", "grupo_interseccional": "Grupo"},
                     custom_data=["n_salario"])
        fig.update_traces(hovertemplate="%{x}<br>R$ %{y:,.2f} por hora<br>%{customdata[0]:,.0f} vínculos<extra>%{fullData.name}</extra>")
        grafico(fig, "Salário-hora médio por nível hierárquico", 380)
    nota_filtros("ano", "UF", "setor", "sexo", "raça/cor", "faixa etária", "escolaridade")

# ============================================================================= GAP SALARIAL
elif secao == "Gap salarial":
    METRICAS = {
        "Salário-hora médio": ("media", "salario_hora"),
        "Remuneração mensal média": ("media", "remuneracao"),
        "Mediana do salário-hora": ("quantil", "salario_hora"),
        "Mediana da remuneração mensal": ("quantil", "remuneracao"),
        "Gap do salário-hora médio vs homens brancos": ("gap", "salario_hora"),
        "Gap da mediana do salário-hora vs homens brancos": ("gap_quantil", "salario_hora"),
    }
    EIXOS = {
        "Nível hierárquico": ("nivel", "nivel_nome"),
        "Região": ("regiao", "regiao_nome"),
        "UF": ("uf", "uf_sigla"),
        "Faixa etária": ("faixa_etaria", "faixa_nome"),
        "Escolaridade": ("escolaridade", "escolaridade_nome"),
        "Setor": ("setor", "setor_nome"),
        "Seção CNAE": ("cnae_secao", "secao_nome"),
        "Ano": ("ano", "ano"),
    }
    c1, c2 = st.columns(2)
    metrica = c1.selectbox("Métrica", list(METRICAS))
    eixo = c2.selectbox("Comparar por", list(EIXOS))
    tipo, var = METRICAS[metrica]
    col, rot = EIXOS[eixo]
    f = com(F, anos=()) if col == "ano" else F

    if tipo in ("media", "gap"):
        tabela_fonte = "fato_rais_setor" if col == "cnae_secao" else "fato_rais_perfil"
        dados = m.rotular(q.agregar(tabela_fonte, [col, "sexo", "raca"], f), DIMS)
        dados = m.medias(dados, [col, rot, "grupo_interseccional"])
        valor = var
        if tipo == "gap":
            dados = m.gap_por_grupo(dados, var, [col])
            valor = "gap"
        filtros_usados = ["UF", "setor", "sexo", "raça/cor"] + ([] if col == "cnae_secao" else ["faixa etária", "escolaridade"])
    else:
        # Mediana não se soma: o agrupamento por grupo interseccional é feito no banco.
        dados = m.rotular(q.quantis([col, "grupo"], f, var), DIMS)
        dados.loc[dados["n_amostra"] < m.N_MINIMO, ["p10", "p25", "p50", "p75", "p90"]] = np.nan
        valor = "p50"
        if tipo == "gap_quantil":
            dados = m.gap_por_grupo(dados, "p50", [col])
            valor = "gap"
        filtros_usados = ["UF", "setor", "sexo", "raça/cor", "faixa etária", "escolaridade"]
    filtros_usados = (["ano"] if col != "ano" else []) + filtros_usados

    dados = visiveis(dados)
    if valor == "gap":
        dados = dados[dados["grupo_interseccional"] != m.REFERENCIA]
    if dados.empty or dados[valor].isna().all():
        st.info("Sem dados suficientes para esta combinação de filtros (grupos com menos de 30 registros são omitidos).")
        st.stop()
    ordem_x = {"nivel_nome": ORDEM_NIVEL, "faixa_nome": list(DIMS["dim_faixa_etaria"]["nome"]),
               "escolaridade_nome": list(DIMS["dim_escolaridade"]["nome"])}.get(rot)
    if rot in ("uf_sigla", "secao_nome", "setor_nome", "regiao_nome"):
        ref_ordem = dados.groupby(rot)[valor].mean().sort_values(ascending=False)
        ordem_x = ref_ordem.index.tolist()
    dados[rot] = dados[rot].astype(str)
    fig = px.bar(dados, x=rot, y=valor, color="grupo_interseccional", barmode="group",
                 color_discrete_map=e.COR_GRUPO,
                 category_orders={rot: [str(x) for x in ordem_x] if ordem_x else None,
                                  "grupo_interseccional": list(e.COR_GRUPO)},
                 labels={rot: eixo, valor: metrica, "grupo_interseccional": "Grupo"})
    if valor == "gap":
        fig.update_yaxes(tickformat=".0%")
        fig.update_traces(hovertemplate="%{x}<br>gap de %{y:.1%}<extra>%{fullData.name}</extra>")
        fig.add_hline(y=0, line_color=e.TEXTO_SEC, line_width=1)
    elif var == "salario_hora":
        fig.update_traces(hovertemplate="%{x}<br>R$ %{y:,.2f} por hora<extra>%{fullData.name}</extra>")
    else:
        fig.update_traces(hovertemplate="%{x}<br>R$ %{y:,.2f} por mês<extra>%{fullData.name}</extra>")
    eixo_txt = eixo if eixo.split()[0].isupper() else eixo[0].lower() + eixo[1:]
    titulo = f"{metrica} por {eixo_txt}" + ("" if col == "ano" else f" ({ano})")
    grafico(fig, titulo, 460)
    nota_filtros(*filtros_usados)
    if valor == "gap":
        st.caption("Gap = 1 − (valor do grupo ÷ valor dos homens brancos no mesmo recorte). Positivo: o grupo recebe menos.")
    if tipo.startswith("quantil") or tipo == "gap_quantil":
        st.caption("Medianas calculadas sobre a amostra estratificada ponderada da RAIS (ver Qualidade dos dados).")
    tabela(dados.drop(columns=[c for c in dados.columns if c.startswith("soma_")], errors="ignore"), metrica)

    st.subheader("Distribuição do salário-hora")
    h = m.rotular(q.histograma(F), DIMS)
    h = visiveis(h).groupby(["grupo_interseccional", "faixa"], as_index=False)["peso"].sum()
    h["parcela"] = h["peso"] / h.groupby("grupo_interseccional")["peso"].transform("sum")
    fig = px.line(h.sort_values("faixa"), x="faixa", y="parcela", color="grupo_interseccional",
                  color_discrete_map=e.COR_GRUPO, line_shape="hvh",
                  category_orders={"grupo_interseccional": list(e.COR_GRUPO)},
                  labels={"faixa": "Salário-hora (R$, faixas de 2,50; último ponto = 150 ou mais)",
                          "parcela": "Parcela do grupo", "grupo_interseccional": "Grupo"})
    fig.update_traces(line_width=2, hovertemplate="R$ %{x:,.2f}: %{y:.1%}<extra>%{fullData.name}</extra>")
    fig.update_yaxes(tickformat=".0%")
    grafico(fig, f"Como o salário-hora se distribui em cada grupo ({ano})", 380)
    nota_filtros("ano", "UF", "setor", "sexo", "raça/cor", "faixa etária", "escolaridade")

# ============================================================================= FUNIL
elif secao == "Funil de liderança":
    atual = base_grupo_nivel(F)
    if atual.empty:
        st.info("Nenhum vínculo com os filtros escolhidos.")
        st.stop()
    part = m.participacao(atual, ["nivel", "nivel_nome"], "grupo_interseccional")
    idx = visiveis(m.indice_representacao(part, "grupo_interseccional")).sort_values("nivel")
    c1, c2 = st.columns(2)
    with c1:
        fig = px.line(idx, x="nivel_nome", y="indice", color="grupo_interseccional", markers=True,
                      color_discrete_map=e.COR_GRUPO,
                      category_orders={"nivel_nome": ORDEM_NIVEL, "grupo_interseccional": list(e.COR_GRUPO)},
                      labels={"nivel_nome": "", "indice": "Índice de representação", "grupo_interseccional": "Grupo"},
                      custom_data=["participacao", "part_base"])
        fig.update_traces(line_width=2, marker_size=9,
                          hovertemplate="%{x}: índice %{y:.2f}<br>%{customdata[0]:.1%} do nível (base: %{customdata[1]:.1%})<extra>%{fullData.name}</extra>")
        fig.add_hline(y=1, line_dash="dot", line_color=e.TEXTO_SEC, annotation_text="proporcional à base",
                      annotation_position="bottom right")
        grafico(fig, "Índice de representação ao longo do funil", 400)
        st.caption("Índice = participação do grupo no nível ÷ participação do grupo na base. Acima de 1, o grupo está sobrerrepresentado.")
    with c2:
        piramide = atual.groupby(["nivel", "nivel_nome", "sexo_nome"], as_index=False)["n_vinculos"].sum()
        piramide["parcela"] = piramide["n_vinculos"] / piramide.groupby("nivel")["n_vinculos"].transform("sum")
        fig = px.bar(piramide.sort_values("nivel"), x="parcela", y="nivel_nome", color="sexo_nome", orientation="h",
                     color_discrete_map=e.COR_SEXO,
                     category_orders={"nivel_nome": ORDEM_NIVEL[::-1], "sexo_nome": ["Homem", "Mulher"]},
                     labels={"parcela": "Participação no nível", "nivel_nome": "", "sexo_nome": "Sexo"},
                     custom_data=["n_vinculos"])
        fig.update_traces(hovertemplate="%{y}: %{x:.1%}<br>%{customdata[0]:,.0f} vínculos<extra>%{fullData.name}</extra>")
        fig.update_xaxes(tickformat=".0%")
        fig.add_vline(x=0.5, line_dash="dot", line_color=e.TEXTO_SEC)
        grafico(fig, "Participação de mulheres e homens por nível", 400)

    st.subheader("Participação feminina por UF e nível")
    uf_niv = m.rotular(q.agregar("fato_rais_perfil", ["uf", "sexo", "nivel"], F), DIMS)
    pv = uf_niv.groupby(["uf_sigla", "nivel_nome", "sexo_nome"])["n_vinculos"].sum().unstack("sexo_nome").fillna(0)
    if "Mulher" in pv and "Homem" in pv:
        pv["parcela"] = pv["Mulher"] / (pv["Mulher"] + pv["Homem"])
        pv.loc[(pv["Mulher"] + pv["Homem"]) < m.N_MINIMO, "parcela"] = np.nan
        mat = pv["parcela"].unstack("nivel_nome").reindex(columns=ORDEM_NIVEL)
        mat = mat.sort_values("Direção", ascending=False)
        validos = mat.values[np.isfinite(mat.values)]
        fig = go.Figure(go.Heatmap(z=mat.values, x=mat.columns, y=mat.index, colorscale=e.ESCALA_SEQ,
                                   zmin=float(validos.min()) if validos.size else 0,
                                   zmax=float(validos.max()) if validos.size else 1,
                                   xgap=2, ygap=2, colorbar=dict(tickformat=".0%", title=""),
                                   hovertemplate="%{y} · %{x}: %{z:.1%} mulheres<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        grafico(fig, "Percentual de mulheres em cada nível, por UF (ordenado pela direção)", 720, legenda=False)
    else:
        st.info("O mapa por UF precisa dos dois sexos selecionados.")

    st.subheader("Evolução da liderança")
    hist = m.rotular(q.agregar("fato_rais_perfil", ["ano", "sexo", "raca", "nivel"], com(F, anos=())), DIMS)
    nivel_ev = st.radio("Nível", ORDEM_NIVEL[1:], index=2, horizontal=True)
    ev = hist[hist["nivel_nome"] == nivel_ev]
    ev = m.participacao(ev, ["ano"], "grupo_interseccional")
    ev = visiveis(ev)
    fig = px.line(ev, x="ano", y="participacao", color="grupo_interseccional", markers=True,
                  color_discrete_map=e.COR_GRUPO, category_orders={"grupo_interseccional": list(e.COR_GRUPO)},
                  labels={"ano": "", "participacao": f"Participação na {nivel_ev.lower()}", "grupo_interseccional": "Grupo"})
    fig.update_traces(line_width=2, marker_size=9, hovertemplate="%{x}: %{y:.1%}<extra>%{fullData.name}</extra>")
    fig.update_xaxes(dtick=1)
    fig.update_yaxes(tickformat=".0%")
    grafico(fig, f"Quem ocupa a {nivel_ev.lower()}, ano a ano", 360)
    nota_filtros("UF", "setor", "sexo", "raça/cor", "faixa etária", "escolaridade")
    tabela(part, "participação por nível")

# ============================================================================= DRILL-DOWN
elif secao == "Drill-down":
    niveis_nome = st.multiselect("Níveis analisados", ORDEM_NIVEL, default=["Gerência", "Direção"])
    niveis = tuple(n for n, nome in NIVEL_NOME.items() if nome in niveis_nome) or tuple(NIVEL_NOME)
    fn = com(F, niveis=niveis if len(niveis) < 4 else ())

    st.subheader("Região → UF → setor de atividade")
    st.caption("Clique em um bloco para descer um nível; clique no topo para voltar. Tamanho = vínculos. "
               "Cor = % de mulheres: azul indica predominância masculina, cinza paridade, laranja predominância feminina.")
    arv = m.rotular(q.agregar("fato_rais_setor", ["uf", "cnae_secao", "sexo"], fn), DIMS)
    arv = arv.groupby(["regiao_nome", "uf_sigla", "secao_nome", "sexo_nome"], as_index=False)["n_vinculos"].sum()
    arv = arv.pivot_table(index=["regiao_nome", "uf_sigla", "secao_nome"], columns="sexo_nome", values="n_vinculos",
                          fill_value=0).reset_index()
    for s_ in ("Homem", "Mulher"):
        if s_ not in arv:
            arv[s_] = 0
    arv["total"] = arv["Homem"] + arv["Mulher"]
    arv = arv[arv["total"] > 0]
    arv["pct_mulheres"] = arv["Mulher"] / arv["total"]
    fig = px.treemap(arv, path=[px.Constant("Brasil"), "regiao_nome", "uf_sigla", "secao_nome"], values="total",
                     color="pct_mulheres", color_continuous_scale=e.ESCALA_PARIDADE, range_color=(0.2, 0.8),
                     color_continuous_midpoint=0.5, maxdepth=3)
    fig.update_traces(hovertemplate="<b>%{label}</b><br>%{value:,.0f} vínculos<br>%{color:.1%} mulheres<extra></extra>",
                      marker_line_width=2, marker_line_color="white")
    fig.update_coloraxes(colorbar=dict(tickformat=".0%", title="% mulheres"))
    grafico(fig, None, 560, legenda=False)
    nota_filtros("ano", "UF", "setor", "sexo", "raça/cor", "níveis (faixa etária e escolaridade não se aplicam)")

    st.subheader("Gap salarial por UF")
    por_uf = m.rotular(q.agregar("fato_rais_perfil", ["uf", "sexo", "raca"], fn), DIMS)
    mu = m.medias(por_uf, ["uf", "uf_sigla", "sexo_nome"]).pivot(index=["uf", "uf_sigla"], columns="sexo_nome", values="salario_hora")
    if {"Homem", "Mulher"} <= set(mu.columns):
        mu["gap"] = 1 - mu["Mulher"] / mu["Homem"]
        mu = mu.dropna(subset=["gap"]).reset_index().sort_values("gap", ascending=False)
        fig = px.bar(mu, x="uf_sigla", y="gap", labels={"uf_sigla": "", "gap": "Gap mulheres x homens"},
                     custom_data=["Mulher", "Homem"])
        fig.update_traces(marker_color=e.COR_SEXO["Mulher"],
                          hovertemplate="%{x}: gap de %{y:.1%}<br>mulheres R$ %{customdata[0]:,.2f}/h · homens R$ %{customdata[1]:,.2f}/h<extra></extra>")
        fig.update_yaxes(tickformat=".0%")
        fig.add_hline(y=0, line_color=e.TEXTO_SEC, line_width=1)
        ev_sel = grafico(fig, "Gap do salário-hora: mulheres x homens (clique em uma UF para detalhar)", 380,
                         legenda=False, on_select="rerun", selection_mode="points", key="gap_uf")
        pontos = (ev_sel or {}).get("selection", {}).get("points", []) if isinstance(ev_sel, dict) else getattr(getattr(ev_sel, "selection", None), "points", [])
        ufs_lista = mu["uf_sigla"].tolist()
        uf_click = pontos[0].get("x") if pontos else None
        # O clique no gráfico preenche a caixa de seleção; depois disso a caixa continua livre.
        if uf_click in ufs_lista and st.session_state.get("_ultimo_clique") != uf_click:
            st.session_state["uf_det"] = uf_click
            st.session_state["_ultimo_clique"] = uf_click
        if st.session_state.get("uf_det") not in ufs_lista:
            st.session_state["uf_det"] = ufs_lista[0]
        uf_det = st.selectbox("UF detalhada", ufs_lista, key="uf_det")
        cod_uf = int(DIMS["dim_uf"].set_index("sigla").loc[uf_det, "uf"])
        det = m.rotular(q.agregar("fato_rais_setor", ["cnae_secao", "sexo", "raca"], com(fn, ufs=(cod_uf,))), DIMS)
        dm = m.medias(det, ["secao_nome", "sexo_nome"])
        pv = dm.pivot(index="secao_nome", columns="sexo_nome", values=["salario_hora", "n_vinculos"])
        if ("salario_hora", "Mulher") in pv and ("salario_hora", "Homem") in pv:
            out = pd.DataFrame({
                "Seção CNAE": pv.index,
                "Vínculos": (pv[("n_vinculos", "Mulher")].fillna(0) + pv[("n_vinculos", "Homem")].fillna(0)).values,
                "% mulheres": (pv[("n_vinculos", "Mulher")] / (pv[("n_vinculos", "Mulher")] + pv[("n_vinculos", "Homem")])).values,
                "Salário-hora mulheres (R$)": pv[("salario_hora", "Mulher")].values,
                "Salário-hora homens (R$)": pv[("salario_hora", "Homem")].values,
            })
            out["Gap"] = 1 - out["Salário-hora mulheres (R$)"] / out["Salário-hora homens (R$)"]
            out = out.sort_values("Vínculos", ascending=False)
            st.markdown(f"**{uf_det}: setores de atividade nos níveis {', '.join(niveis_nome).lower()}**")
            st.dataframe(out, hide_index=True, column_config={
                "Vínculos": st.column_config.NumberColumn(format="%d"),
                "% mulheres": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                "Salário-hora mulheres (R$)": st.column_config.NumberColumn(format="%.2f"),
                "Salário-hora homens (R$)": st.column_config.NumberColumn(format="%.2f"),
                "Gap": st.column_config.NumberColumn(format="percent"),
            })

    st.subheader("Ocupações")
    oc = q.agregar("fato_rais_ocupacao", ["cbo", "sexo", "raca"], fn)
    oc = m.rotular(oc, DIMS).merge(q.ocupacoes()[["cbo", "titulo", "nivel"]], on="cbo", how="left")
    oc = oc[oc["nivel"].isin(niveis)]
    if oc.empty:
        st.info("Sem ocupações para os filtros escolhidos.")
    else:
        tot = oc.groupby(["cbo", "titulo"])["n_vinculos"].sum().rename("vinculos")
        mul = oc[oc["sexo_nome"] == "Mulher"].groupby(["cbo", "titulo"])["n_vinculos"].sum().rename("mulheres")
        neg = oc[oc["grupo_racial"] == "Negra"].groupby(["cbo", "titulo"])["n_vinculos"].sum().rename("negros")
        sh = m.medias(oc, ["cbo", "titulo", "sexo_nome"]).pivot(index=["cbo", "titulo"], columns="sexo_nome", values="salario_hora")
        t = pd.concat([tot, mul, neg, sh], axis=1).fillna({"mulheres": 0, "negros": 0}).reset_index()
        t["% mulheres"] = t["mulheres"] / t["vinculos"]
        t["% pessoas negras"] = t["negros"] / t["vinculos"]
        t["Gap"] = 1 - t.get("Mulher", np.nan) / t.get("Homem", np.nan)
        t = t.sort_values("vinculos", ascending=False).head(25)
        st.caption("As 25 ocupações com mais vínculos nos níveis escolhidos. Filtro geográfico aplicado por região.")
        st.dataframe(
            t[["titulo", "vinculos", "% mulheres", "% pessoas negras"] + [c for c in ("Mulher", "Homem") if c in t] + ["Gap"]]
            .rename(columns={"titulo": "Ocupação (CBO)", "vinculos": "Vínculos", "Mulher": "Salário-hora mulheres (R$)",
                             "Homem": "Salário-hora homens (R$)"}),
            hide_index=True, column_config={
                "Vínculos": st.column_config.NumberColumn(format="%d"),
                "% mulheres": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                "% pessoas negras": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                "Salário-hora mulheres (R$)": st.column_config.NumberColumn(format="%.2f"),
                "Salário-hora homens (R$)": st.column_config.NumberColumn(format="%.2f"),
                "Gap": st.column_config.NumberColumn(format="percent"),
            })

# ============================================================================= CAGED
elif secao == "Dinâmica do emprego":
    c1, c2, c3 = st.columns([1, 2, 2])
    ano_c = c1.selectbox("Ano", ANOS_CAGED, index=ANOS_CAGED.index(max(a for a in ANOS_CAGED if MESES_CAGED.get(a) == 12)) if 12 in MESES_CAGED.values() else 0)
    METRICAS_C = ["Saldo (admissões − desligamentos)", "Admissões", "Desligamentos",
                  "Salário médio de admissão", "Gap no salário de admissão vs homens brancos",
                  "Taxa de rotatividade mensal"]
    metrica = c2.selectbox("Métrica", METRICAS_C)
    niveis_nome = c3.multiselect("Níveis", ORDEM_NIVEL, default=["Gerência", "Direção"])
    niveis = tuple(n for n, nome in NIVEL_NOME.items() if nome in niveis_nome)
    fc = com(F, anos=(ano_c,), niveis=niveis if 0 < len(niveis) < 4 else ())
    if MESES_CAGED.get(ano_c, 12) < 12:
        st.caption(f"{ano_c} tem {MESES_CAGED[ano_c]} meses publicados até agora.")

    serie = m.rotular(q.agregar("fato_caged_perfil", ["competencia", "sexo", "raca", "tipo"], fc), DIMS)
    serie["data"] = pd.to_datetime(serie["competencia"].astype(int).astype(str) + "01", format="%Y%m%d")
    g = serie.groupby(["data", "grupo_interseccional", "tipo"], as_index=False)[["n_mov", "n_salario", "soma_salario"]].sum()
    adm = g[g["tipo"] == 1].set_index(["data", "grupo_interseccional"])
    des = g[g["tipo"] == -1].set_index(["data", "grupo_interseccional"])
    s = pd.DataFrame({"Admissões": adm["n_mov"], "Desligamentos": des["n_mov"]}).fillna(0)
    s["Saldo (admissões − desligamentos)"] = s["Admissões"] - s["Desligamentos"]
    s["Salário médio de admissão"] = (adm["soma_salario"] / adm["n_salario"]).where(adm["n_salario"] >= m.N_MINIMO)
    s = s.reset_index()
    if metrica.startswith("Gap"):
        ref = s[s["grupo_interseccional"] == m.REFERENCIA][["data", "Salário médio de admissão"]].rename(
            columns={"Salário médio de admissão": "ref"})
        s = s.merge(ref, on="data", how="left")
        s[metrica] = 1 - s["Salário médio de admissão"] / s["ref"]
        s = s[s["grupo_interseccional"] != m.REFERENCIA]
    elif metrica.startswith("Taxa"):
        # R7 aplicada mês a mês: min(admissões, desligamentos) do mês ÷ estoque de 31/12 do ano anterior.
        if (ano_c - 1) not in ANOS_RAIS:
            st.info(f"Não há RAIS de {ano_c - 1} para servir de estoque inicial.")
            st.stop()
        estq = m.rotular(q.agregar("fato_rais_perfil", ["sexo", "raca"],
                                   com(fc, anos=(ano_c - 1,), setores=SETORES_CELETISTAS, escolaridades=())), DIMS)
        estq = estq.groupby("grupo_interseccional")["n_vinculos"].sum()
        s["estoque"] = s["grupo_interseccional"].map(estq)
        s[metrica] = (np.minimum(s["Admissões"], s["Desligamentos"]) / s["estoque"]).where(s["estoque"] >= m.N_MINIMO)
    s = visiveis(s)
    fig = px.line(s, x="data", y=metrica, color="grupo_interseccional", markers=True,
                  color_discrete_map=e.COR_GRUPO, category_orders={"grupo_interseccional": list(e.COR_GRUPO)},
                  labels={"data": "", "grupo_interseccional": "Grupo"})
    fig.update_traces(line_width=2, marker_size=8)
    if metrica.startswith("Gap"):
        fig.update_yaxes(tickformat=".0%")
        fig.update_traces(hovertemplate="%{x|%b/%Y}: %{y:.1%}<extra>%{fullData.name}</extra>")
    elif metrica.startswith("Taxa"):
        fig.update_yaxes(tickformat=".1%")
        fig.update_traces(hovertemplate="%{x|%b/%Y}: %{y:.2%}<extra>%{fullData.name}</extra>")
    elif metrica.startswith("Salário"):
        fig.update_traces(hovertemplate="%{x|%b/%Y}: R$ %{y:,.2f}<extra>%{fullData.name}</extra>")
    else:
        fig.update_traces(hovertemplate="%{x|%b/%Y}: %{y:,.0f}<extra>%{fullData.name}</extra>")
    if metrica.startswith("Saldo"):
        fig.add_hline(y=0, line_color=e.TEXTO_SEC, line_width=1)
    fig.update_xaxes(dtick="M1", tickformat="%b\n%Y")
    grafico(fig, f"{metrica} por mês ({', '.join(niveis_nome) or 'todos os níveis'})", 420)
    nota_filtros("ano", "UF", "sexo", "raça/cor", "faixa etária", "níveis (setor e escolaridade não existem no CAGED agregado)")

    c1, c2 = st.columns(2)
    with c1:
        if (ano_c - 1) in ANOS_RAIS:
            fluxo = m.rotular(q.agregar("fato_caged_perfil", ["sexo", "raca", "nivel", "tipo"], com(fc, niveis=())), DIMS)
            # Escolaridade não existe no CAGED agregado; o estoque também ignora esse filtro.
            estq = m.rotular(q.agregar("fato_rais_perfil", ["sexo", "raca", "nivel"],
                                       com(F, anos=(ano_c - 1,), setores=SETORES_CELETISTAS, escolaridades=())), DIMS)
            rot = m.rotatividade(fluxo, estq, ["nivel_nome", "grupo_interseccional"])
            rot = visiveis(rot)
            fig = px.bar(rot, x="nivel_nome", y="taxa_rotatividade", color="grupo_interseccional", barmode="group",
                         color_discrete_map=e.COR_GRUPO,
                         category_orders={"nivel_nome": ORDEM_NIVEL, "grupo_interseccional": list(e.COR_GRUPO)},
                         labels={"nivel_nome": "", "taxa_rotatividade": "Taxa de rotatividade", "grupo_interseccional": "Grupo"},
                         custom_data=["admissoes", "desligamentos", "estoque"])
            fig.update_traces(hovertemplate="%{x}: %{y:.1%}<br>adm. %{customdata[0]:,.0f} · desl. %{customdata[1]:,.0f} · estoque %{customdata[2]:,.0f}<extra>%{fullData.name}</extra>")
            fig.update_yaxes(tickformat=".0%")
            grafico(fig, f"Rotatividade por nível ({ano_c}, estoque de 31/12/{ano_c - 1})", 400)
            st.caption("Taxa = min(admissões, desligamentos) ÷ estoque inicial da RAIS, sem o setor público "
                       "(estatutários não aparecem no CAGED). Une as duas bases pelo mesmo recorte de UF, sexo, "
                       "raça, faixa etária e nível.")
        else:
            st.info(f"Não há RAIS de {ano_c - 1} para servir de estoque inicial.")
    with c2:
        mot = m.rotular(q.agregar("fato_caged_motivo", ["sexo", "nivel", "motivo"], com(fc, niveis=niveis)), DIMS)
        mot["motivo_nome"] = mot["motivo"].map(DIMS["dim_motivo"].set_index("motivo")["nome"])
        mot = mot[mot["motivo"] != 1]
        mot = mot.groupby(["sexo_nome", "motivo_nome"], as_index=False)["n_mov"].sum()
        mot["parcela"] = mot["n_mov"] / mot.groupby("sexo_nome")["n_mov"].transform("sum")
        ordem_mot = [x for x in DIMS["dim_motivo"]["nome"] if x != "Admissão"]
        cores_mot = dict(zip(ordem_mot, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]))
        fig = px.bar(mot, x="parcela", y="sexo_nome", color="motivo_nome", orientation="h",
                     color_discrete_map=cores_mot, category_orders={"motivo_nome": ordem_mot},
                     labels={"parcela": "Parcela dos desligamentos", "sexo_nome": "", "motivo_nome": "Motivo"},
                     custom_data=["n_mov"])
        fig.update_traces(hovertemplate="%{y}: %{x:.1%}<br>%{customdata[0]:,.0f} desligamentos<extra>%{fullData.name}</extra>")
        fig.update_xaxes(tickformat=".0%")
        grafico(fig, f"Por que os vínculos terminam ({', '.join(niveis_nome) or 'todos os níveis'})", 400)
        st.caption("Filtro geográfico aplicado por região.")

# ============================================================================= QUALIDADE
elif secao == "Qualidade dos dados":
    rel = q.qualidade()
    linhas = []
    for r in rel.itertuples():
        d = json.loads(r.relatorio)
        if d["fonte"].startswith("RAIS"):
            c = d["contagens"]
            linhas.append({"Fonte": "RAIS", "Arquivo": f"{d['ano']} · {d['arquivo']}", "Linhas lidas": c["linhas_lidas"],
                           "Ativos em 31/12 válidos": c["vinculos_ativos_validos"],
                           "Com salário válido": c["vinculos_com_salario_valido"],
                           "Sem remuneração": c.get("sal_excl_sem_remuneracao", 0),
                           "Raça não informada": c.get("raca_nao_informada", 0),
                           "Linhas na amostra": d.get("linhas_amostra", 0),
                           "Codificação": d.get("codificacao"), "Segundos": d.get("segundos")})
        else:
            for tipo, c in d["arquivos"].items():
                linhas.append({"Fonte": f"CAGED {tipo}", "Arquivo": c["arquivo"], "Linhas lidas": c["linhas_lidas"],
                               "Ativos em 31/12 válidos": None, "Com salário válido": None,
                               "Sem remuneração": c.get("sal_excl_sem_salario", 0),
                               "Raça não informada": c.get("raca_nao_informada", 0), "Linhas na amostra": None,
                               "Codificação": c.get("codificacao"), "Segundos": None})
    t = pd.DataFrame(linhas)
    for c in ("Linhas lidas", "Ativos em 31/12 válidos", "Com salário válido", "Sem remuneração",
              "Raça não informada", "Linhas na amostra"):
        t[c] = pd.to_numeric(t[c], errors="coerce").astype("Int64")
    rais = t[t["Fonte"] == "RAIS"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Linhas lidas", e.milhoes(t["Linhas lidas"].sum()), help="RAIS + Novo CAGED (MOV, FOR e EXC).")
    c2.metric("Vínculos ativos válidos", e.milhoes(rais["Ativos em 31/12 válidos"].sum()), help="RAIS, soma dos três anos.")
    c3.metric("Com salário válido", e.pct(rais["Com salário válido"].sum() / rais["Ativos em 31/12 válidos"].sum()))
    c4.metric("Amostra estratificada", e.milhoes(rais["Linhas na amostra"].sum()), help="Linhas usadas para medianas e distribuições.")
    st.dataframe(t, hide_index=True)

    st.markdown("**Dados coletados por API**")
    st.caption("IBGE (Localidades) alimenta a dimensão de UFs. O Banco Central (SGS) fornece o salário "
               "mínimo, usado nos limites do salário-hora, e o IPCA mensal. As respostas ficam guardadas "
               "em JSON na tabela api_coleta.")
    a1, a2 = st.columns([3, 1])
    coletas = q.coletas_api()
    coletas["coletado"] = pd.to_datetime(coletas["coletado"]).dt.strftime("%d/%m/%Y %H:%M")
    a1.dataframe(coletas.rename(columns={"url": "Endereço", "coletado": "Coletado em (UTC)", "status": "HTTP",
                                         "registros": "Registros"}), hide_index=True)
    sm = q.salario_minimo()
    sm["valor"] = sm["valor"].map(e.brl)
    a2.dataframe(sm.rename(columns={"ano": "Ano", "valor": "Salário mínimo"}), hide_index=True)

    st.markdown(
        """
**Regras aplicadas no ETL**

Na RAIS entram só vínculos ativos em 31/12, com sexo informado e UF válida. Todos contam
no funil. Para as métricas de salário, ficam de fora os vínculos sem remuneração, com
jornada fora de 1 a 60 horas semanais, admitidos em dezembro (mês incompleto), aprendizes,
intermitentes e os que têm salário-hora abaixo de 0,5 ou acima de 300 salários mínimos-hora.

O salário-hora é a remuneração de dezembro (ou a média do ano, se dezembro veio zerado)
dividida pelas horas mensais contratadas (horas semanais × 4,345).

No Novo CAGED, as declarações fora do prazo (FOR) entram na competência original e as
exclusões (EXC) são subtraídas, como na metodologia do MTE.

Medianas e distribuições vêm de uma amostra estratificada por nível hierárquico
(0,4% da base, 3% da supervisão, 4% da gerência e 15% da direção), com peso igual ao
inverso da taxa. Médias, contagens e gaps usam a base completa.
"""
    )
