-- Regras de negócio expressas em SQL. O painel calcula as mesmas métricas de forma
-- dinâmica; estas views deixam as regras documentadas no próprio banco e servem para
-- ferramentas de BI (Metabase, Grafana) ou consultas diretas.

-- R3. Agregação por grupo interseccional (sexo x grupo racial; negra = preta + parda).
CREATE OR REPLACE VIEW vw_rais_grupo AS
SELECT f.ano,
       u.sigla                 AS uf,
       u.regiao_nome           AS regiao,
       f.nivel                 AS nivel_ordem,
       n.nome                  AS nivel,
       g.grupo_racial,
       g.grupo_interseccional,
       sum(f.n_vinculos)       AS vinculos,
       sum(f.n_salario)        AS vinculos_com_salario,
       sum(f.soma_salario_hora) AS soma_salario_hora,
       sum(f.soma_salario_hora) / nullif(sum(f.n_salario), 0) AS salario_hora_medio,
       sum(f.soma_remuneracao)  / nullif(sum(f.n_salario), 0) AS remuneracao_media
FROM fato_rais_perfil f
JOIN dim_uf u    USING (uf)
JOIN dim_nivel n USING (nivel)
JOIN dim_grupo g USING (sexo, raca)
GROUP BY 1, 2, 3, 4, 5, 6, 7;

-- R6. Funil de liderança: participação de cada grupo em cada nível hierárquico (Brasil).
CREATE OR REPLACE VIEW vw_funil_lideranca AS
WITH base AS (
    SELECT ano, nivel_ordem, nivel, grupo_interseccional, sum(vinculos) AS vinculos
    FROM vw_rais_grupo GROUP BY 1, 2, 3, 4
)
SELECT ano, nivel_ordem, nivel, grupo_interseccional, vinculos,
       vinculos::numeric / sum(vinculos) OVER (PARTITION BY ano, nivel_ordem) AS participacao
FROM base;

-- R4. Gap salarial por hora de cada grupo em relação a homens brancos, por ano, UF e nível.
--     gap = 1 - (salário-hora médio do grupo / salário-hora médio de homens brancos)
--     Só entram células com pelo menos 30 vínculos com salário válido em cada lado.
CREATE OR REPLACE VIEW vw_gap_interseccional AS
WITH g AS (
    SELECT ano, uf, nivel_ordem, nivel, grupo_interseccional,
           sum(soma_salario_hora) / nullif(sum(vinculos_com_salario), 0) AS sh,
           sum(vinculos_com_salario) AS n
    FROM vw_rais_grupo GROUP BY 1, 2, 3, 4, 5
)
SELECT g.ano, g.uf, g.nivel, g.grupo_interseccional, g.n,
       g.sh   AS salario_hora_medio,
       ref.sh AS salario_hora_homem_branco,
       1 - g.sh / nullif(ref.sh, 0) AS gap_vs_homem_branco
FROM g
JOIN g ref ON ref.ano = g.ano AND ref.uf = g.uf AND ref.nivel_ordem = g.nivel_ordem
          AND ref.grupo_interseccional = 'Homem branco'
WHERE g.n >= 30 AND ref.n >= 30;

-- R7. Rotatividade: min(admissões, desligamentos) no período / estoque RAIS em 31/12 do
--     ano anterior, por UF, grupo e nível. O estoque exclui o setor público (setor = 1),
--     porque servidores estatutários não são registrados no CAGED.
CREATE OR REPLACE VIEW vw_rotatividade AS
WITH fluxo AS (
    SELECT c.competencia / 100 AS ano, c.uf, c.sexo, c.raca, c.nivel,
           sum(c.n_mov) FILTER (WHERE c.tipo = 1)  AS admissoes,
           sum(c.n_mov) FILTER (WHERE c.tipo = -1) AS desligamentos,
           count(DISTINCT c.competencia)           AS meses
    FROM fato_caged_perfil c GROUP BY 1, 2, 3, 4, 5
), estoque AS (
    SELECT ano + 1 AS ano, uf, sexo, raca, nivel, sum(n_vinculos) AS estoque_inicial
    FROM fato_rais_perfil WHERE setor <> 1 GROUP BY 1, 2, 3, 4, 5
)
SELECT f.ano, u.sigla AS uf, g.grupo_interseccional, n.nome AS nivel, f.meses,
       f.admissoes, f.desligamentos, e.estoque_inicial,
       least(f.admissoes, f.desligamentos)::numeric / nullif(e.estoque_inicial, 0) AS taxa_rotatividade
FROM fluxo f
JOIN estoque e   USING (ano, uf, sexo, raca, nivel)
JOIN dim_uf u    USING (uf)
JOIN dim_grupo g USING (sexo, raca)
JOIN dim_nivel n USING (nivel);
