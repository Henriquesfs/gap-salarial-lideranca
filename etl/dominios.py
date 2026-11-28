"""Tabelas de domínio e harmonização de códigos entre RAIS e Novo CAGED.

As duas bases usam códigos diferentes para a mesma informação (por exemplo, mulher é 2 na
RAIS e 3 no CAGED). Tudo é convertido para um padrão único do projeto antes da agregação.
"""
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- sexo
# Padrão do projeto: 1 = Homem, 2 = Mulher. Demais valores viram 0 (descartado).
SEXO_RAIS = {1: 1, 2: 2}
SEXO_CAGED = {1: 1, 3: 2}
SEXO_NOME = {1: "Homem", 2: "Mulher"}

# ---------------------------------------------------------------- raça/cor
# Padrão do projeto (ordem do IBGE): 1 Branca, 2 Preta, 3 Parda, 4 Amarela, 5 Indígena,
# 9 Não informada.
RACA_RAIS = {2: 1, 4: 2, 8: 3, 6: 4, 1: 5}        # 9 e -1 -> 9
RACA_CAGED = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}        # 6 e 9 -> 9
RACA_NOME = {1: "Branca", 2: "Preta", 3: "Parda", 4: "Amarela", 5: "Indígena", 9: "Não informada"}
# Grupo racial usado nos indicadores interseccionais. Negra = preta + parda (IBGE e
# Estatuto da Igualdade Racial, Lei 12.288/2010).
RACA_GRUPO = {1: "Branca", 2: "Negra", 3: "Negra", 4: "Amarela/Indígena", 5: "Amarela/Indígena", 9: "Não informada"}
_GRUPO_ROTULO = {
    ("Branca", 1): "Homem branco", ("Branca", 2): "Mulher branca",
    ("Negra", 1): "Homem negro", ("Negra", 2): "Mulher negra",
    ("Amarela/Indígena", 1): "Homem amarelo/indígena", ("Amarela/Indígena", 2): "Mulher amarela/indígena",
    ("Não informada", 1): "Homem (raça não informada)", ("Não informada", 2): "Mulher (raça não informada)",
}


def grupo_interseccional(sexo: int, raca: int) -> str:
    """Rótulo do cruzamento sexo x grupo racial, usado em todos os indicadores."""
    return _GRUPO_ROTULO[(RACA_GRUPO[raca], sexo)]

# ---------------------------------------------------------------- escolaridade
# Os códigos 1 a 11 são iguais nas duas bases. O CAGED tem também 80 (pós-graduação).
ESCOLARIDADE_GRUPO = {
    1: 1, 2: 1, 3: 1, 4: 1,      # fundamental incompleto
    5: 2, 6: 2,                  # fundamental completo / médio incompleto
    7: 3, 8: 3,                  # médio completo / superior incompleto
    9: 4,                        # superior completo
    10: 5, 11: 5, 80: 5,         # pós-graduação
}
ESCOLARIDADE_NOME = {
    1: "Fundamental incompleto",
    2: "Fundamental completo",
    3: "Médio completo",
    4: "Superior completo",
    5: "Pós-graduação",
    9: "Não informada",
}

# ---------------------------------------------------------------- faixa etária
FAIXA_LIMITES = [14, 18, 25, 30, 40, 50, 60, 100]
FAIXA_NOME = {
    1: "14 a 17", 2: "18 a 24", 3: "25 a 29", 4: "30 a 39",
    5: "40 a 49", 6: "50 a 59", 7: "60 ou mais", 9: "Não informada",
}


def faixa_etaria(idade: pd.Series) -> np.ndarray:
    """Idade em anos -> código de faixa (1 a 7). Fora de 14..99 vira 9."""
    idx = np.searchsorted(FAIXA_LIMITES, idade.fillna(-1).to_numpy(), side="right")
    return np.where((idx >= 1) & (idx <= 7), idx, 9).astype("int8")


# ---------------------------------------------------------------- localidade
UF_SIGLA = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
    21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL", 28: "SE", 29: "BA",
    31: "MG", 32: "ES", 33: "RJ", 35: "SP",
    41: "PR", 42: "SC", 43: "RS",
    50: "MS", 51: "MT", 52: "GO", 53: "DF",
}
UF_NOME = {
    11: "Rondônia", 12: "Acre", 13: "Amazonas", 14: "Roraima", 15: "Pará", 16: "Amapá",
    17: "Tocantins", 21: "Maranhão", 22: "Piauí", 23: "Ceará", 24: "Rio Grande do Norte",
    25: "Paraíba", 26: "Pernambuco", 27: "Alagoas", 28: "Sergipe", 29: "Bahia",
    31: "Minas Gerais", 32: "Espírito Santo", 33: "Rio de Janeiro", 35: "São Paulo",
    41: "Paraná", 42: "Santa Catarina", 43: "Rio Grande do Sul", 50: "Mato Grosso do Sul",
    51: "Mato Grosso", 52: "Goiás", 53: "Distrito Federal",
}
REGIAO_NOME = {1: "Norte", 2: "Nordeste", 3: "Sudeste", 4: "Sul", 5: "Centro-Oeste"}
UFS_VALIDAS = np.array(sorted(UF_SIGLA), dtype="int16")

# ---------------------------------------------------------------- CNAE 2.0
# Divisão (2 primeiros dígitos da classe) -> seção. Tabela da CONCLA/IBGE.
_SECOES = [
    ("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39), ("F", 41, 43),
    ("G", 45, 47), ("H", 49, 53), ("I", 55, 56), ("J", 58, 63), ("K", 64, 66), ("L", 68, 68),
    ("M", 69, 75), ("N", 77, 82), ("O", 84, 84), ("P", 85, 85), ("Q", 86, 88), ("R", 90, 93),
    ("S", 94, 96), ("T", 97, 97), ("U", 99, 99),
]
DIVISAO_SECAO = np.full(100, "Z", dtype="<U1")
for _letra, _ini, _fim in _SECOES:
    DIVISAO_SECAO[_ini:_fim + 1] = _letra
SECAO_NOME = {
    "A": "Agropecuária, produção florestal e pesca",
    "B": "Indústrias extrativas",
    "C": "Indústrias de transformação",
    "D": "Eletricidade e gás",
    "E": "Água, esgoto e resíduos",
    "F": "Construção",
    "G": "Comércio e reparação de veículos",
    "H": "Transporte, armazenagem e correio",
    "I": "Alojamento e alimentação",
    "J": "Informação e comunicação",
    "K": "Atividades financeiras e seguros",
    "L": "Atividades imobiliárias",
    "M": "Atividades profissionais, científicas e técnicas",
    "N": "Atividades administrativas e serviços complementares",
    "O": "Administração pública, defesa e seguridade social",
    "P": "Educação",
    "Q": "Saúde humana e serviços sociais",
    "R": "Artes, cultura, esporte e recreação",
    "S": "Outras atividades de serviços",
    "T": "Serviços domésticos",
    "U": "Organismos internacionais",
    "Z": "Não informada",
}

# ---------------------------------------------------------------- natureza jurídica
# Primeiro dígito da natureza jurídica (CONCLA 2002).
SETOR_NOME = {1: "Público", 2: "Privado empresarial", 3: "Sem fins lucrativos", 4: "Pessoa física e outros", 9: "Não informado"}


def setor_por_natureza(natureza: pd.Series) -> np.ndarray:
    d = (natureza.fillna(0).to_numpy() // 1000).astype("int16")
    return np.select([d == 1, d == 2, d == 3, (d == 4) | (d == 5)], [1, 2, 3, 4], 9).astype("int8")


# ---------------------------------------------------------------- nível hierárquico
NIVEL_NOME = {1: "Base", 2: "Supervisão", 3: "Gerência", 4: "Direção"}

# ---------------------------------------------------------------- CAGED: motivo da movimentação
MOTIVO_NOME = {
    1: "Admissão",
    2: "Desligamento sem justa causa",
    3: "Desligamento a pedido",
    4: "Término de contrato",
    5: "Acordo entre as partes",
    6: "Outros desligamentos",
}


def motivo_caged(tipo: pd.Series, saldo: pd.Series) -> np.ndarray:
    t = tipo.fillna(0).to_numpy()
    s = saldo.to_numpy()
    return np.select(
        [s > 0, t == 31, t == 40, np.isin(t, [43, 45]), t == 90],
        [1, 2, 3, 4, 5],
        6,
    ).astype("int8")


def mapear(serie: pd.Series, mapa: dict, padrao: int) -> np.ndarray:
    """Converte códigos com dicionário, usando `padrao` para qualquer valor fora dele."""
    tabela = np.full(1000, padrao, dtype="int16")
    for k, v in mapa.items():
        tabela[k] = v
    x = serie.fillna(-1).to_numpy().astype("int64")
    ok = (x >= 0) & (x < 1000)
    out = np.full(len(x), padrao, dtype="int16")
    out[ok] = tabela[x[ok]]
    return out.astype("int8")
