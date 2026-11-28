output "pg_host" {
  value = aiven_pg.analytics.service_host
}

output "pg_port" {
  value = aiven_pg.analytics.service_port
}

output "pg_database" {
  value = aiven_pg_database.gap.database_name
}

output "pg_estado" {
  value = aiven_pg.analytics.state
}

# Saídas sensíveis: leia com `terraform output -raw <nome>` e copie para o .env local
# ou para os Secrets. Elas nunca aparecem no log do `terraform apply`.
output "database_url_admin" {
  description = "Conexão do ETL (usuário administrador)."
  value = format("postgresql://%s:%s@%s:%s/%s?sslmode=require",
    aiven_pg.analytics.service_username, aiven_pg.analytics.service_password,
    aiven_pg.analytics.service_host, aiven_pg.analytics.service_port,
  aiven_pg_database.gap.database_name)
  sensitive = true
}

output "database_url_dashboard" {
  description = "Conexão do painel (usuário somente leitura)."
  value = format("postgresql://%s:%s@%s:%s/%s?sslmode=require",
    aiven_pg_user.dashboard.username, aiven_pg_user.dashboard.password,
    aiven_pg.analytics.service_host, aiven_pg.analytics.service_port,
  aiven_pg_database.gap.database_name)
  sensitive = true
}

output "opensearch_uri" {
  value     = var.opensearch_service_name == "" ? "" : data.aiven_opensearch.busca[0].service_uri
  sensitive = true
}
