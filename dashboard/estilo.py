"""Cores, formatação de números em pt-BR e layout padrão dos gráficos."""
from __future__ import annotations

import math

import plotly.graph_objects as go

# Paleta categórica validada para daltonismo, em ordem fixa. A cor segue o grupo,
# nunca a posição no gráfico: um filtro que remove grupos não repinta os demais.
COR_GRUPO = {
    "Homem branco": "#2a78d6",
    "Mulher branca": "#eb6834",
    "Homem negro": "#1baf7a",
    "Mulher negra": "#eda100",
    "Homem amarelo/indígena": "#e87ba4",
    "Mulher amarela/indígena": "#008300",
    "Homem (raça não informada)": "#6250d6",
    "Mulher (raça não informada)": "#e34948",
}
GRUPOS_PRINCIPAIS = ["Homem branco", "Mulher branca", "Homem negro", "Mulher negra"]
COR_SEXO = {"Homem": "#2a78d6", "Mulher": "#eb6834"}

# Rampa ordinal (azul) para os níveis do funil, do mais claro (base) ao mais escuro.
COR_NIVEL = {"Base": "#86b6ef", "Supervisão": "#5598e7", "Gerência": "#256abf", "Direção": "#104281"}

# Divergente azul <-> vermelho com meio cinza: gap a favor dos homens em vermelho.
ESCALA_GAP = [[0.0, "#1c5cab"], [0.5, "#f0efec"], [1.0, "#c4312f"]]
ESCALA_SEQ = [[0.0, "#cde2fb"], [0.5, "#3987e5"], [1.0, "#0d366b"]]
# Paridade de gênero: azul (mais homens) <-> cinza (50%) <-> laranja (mais mulheres).
ESCALA_PARIDADE = [[0.0, "#1c5cab"], [0.5, "#f0efec"], [1.0, "#c4521f"]]

TEXTO = "#0b0b0b"
TEXTO_SEC = "#52514e"
GRADE = "#e4e3df"


def layout(fig: go.Figure, titulo: str | None = None, altura: int = 420, legenda: bool = True) -> go.Figure:
    fig.update_layout(
        title=dict(text=titulo, font=dict(size=15, color=TEXTO), x=0, xanchor="left") if titulo else None,
        height=altura,
        margin=dict(l=8, r=8, t=56 if titulo else 16, b=8),
        font=dict(family="Inter, Segoe UI, sans-serif", size=12, color=TEXTO_SEC),
        separators=",.",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        bargap=0.25,
        bargroupgap=0.06,
        hoverlabel=dict(bgcolor="white", font_size=12, font_color=TEXTO),
        showlegend=legenda,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title=None),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRADE, ticks="", title_font=dict(size=12))
    fig.update_yaxes(gridcolor=GRADE, zeroline=True, zerolinecolor=GRADE, title_font=dict(size=12))
    if fig.layout.barmode == "group":
        fig.update_traces(selector=dict(type="bar"), marker_line_width=0)
    else:
        # Barras empilhadas: 2 px da cor do fundo separam os segmentos.
        fig.update_traces(selector=dict(type="bar"), marker_line_width=2, marker_line_color="#fcfcfb")
    return fig


def _vazio(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def pct(x, casas: int = 1, sinal: bool = False) -> str:
    if _vazio(x):
        return "—"
    s = f"{x * 100:+.{casas}f}" if sinal else f"{x * 100:.{casas}f}"
    return s.replace(".", ",") + "%"


def pp(x, casas: int = 1) -> str:
    """Diferença em pontos percentuais."""
    if _vazio(x):
        return "—"
    return f"{x * 100:+.{casas}f}".replace(".", ",") + " p.p."


def brl(x, casas: int = 2) -> str:
    if _vazio(x):
        return "—"
    s = f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def milhoes(x) -> str:
    if _vazio(x):
        return "—"
    return f"{x / 1e6:.1f}".replace(".", ",") + " mi"


def inteiro(x) -> str:
    if _vazio(x):
        return "—"
    return f"{int(round(x)):,}".replace(",", ".")
