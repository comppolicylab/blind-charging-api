locals {
  # Database configuration segment
  db_config = <<EOF
engine = "mssql"
user = "${var.db_user}"
password = "${var.db_password}"
host = "${local.mssql_fqdn}"
database = "${azurerm_mssql_database.main.name}"
EOF

  # Processing pipeline configuration segment
  # Configure the processing pipeline. Just a no-op pipeline on the toy version.
  app_pipeline_toy_toml = <<EOF
pipe = [
    { engine = "extract:tesseract" },
    { engine = "redact:noop", delimiters = ["[", "]"] },
]
EOF

  # This is the full pipeline on the production version.
  app_pipeline_prod_toml = <<EOF
[[processor.pipe]]
# 1) Extract / OCR with Azure DI
engine = "extract:azuredi"
endpoint = "${local.fr_endpoint}"
api_key = "${azurerm_cognitive_account.fr.primary_access_key}"
extract_labeled_text = false

[[processor.pipe]]
# 2) Parse textual output into coherent narrative with OpenAI
engine = "parse:openai"
[processor.pipe.client]
azure_endpoint = "${local.openai_endpoint}"
api_key = "${azurerm_cognitive_account.openai.primary_access_key}"
api_version = "2024-06-01"

[processor.pipe.generator]
method = "chat"
model = "${azurerm_cognitive_deployment.llm.name}"
system = { prompt_id = "parse" }

[[processor.pipe]]
# 3) Redact racial information with OpenAI
engine = "redact:openai"
delimiters = ["[", "]"]
[processor.pipe.client]
azure_endpoint = "${local.openai_endpoint}"
api_key = "${azurerm_cognitive_account.openai.primary_access_key}"
api_version = "2024-06-01"

[processor.pipe.generator]
method = "chat"
model = "${azurerm_cognitive_deployment.llm.name}"
system = { prompt_id = "redact" }
EOF

  # Full application config file
  app_config_toml = <<EOF
debug = ${var.debug}

${var.app_auth != "none" ? "[authentication]\nmethod = \"${var.app_auth}\"\n" : ""}
${(var.app_auth == "client_credentials" || var.app_auth == "preshared") ? "secret = \"${var.app_auth_secret}\"\n" : ""}
${var.app_auth == "client_credentials" ? "[authentication.store]\n${local.db_config}\n" : ""}

[metrics]
engine = "azure"
connection_string = "${azurerm_application_insights.main.connection_string}"

[queue]

[queue.store]
engine = "redis"
host = "${local.redis_fqdn}"
ssl = true
port = 6380
password = "${azurerm_redis_cache.main.primary_access_key}"
db = 0

[queue.broker]
engine = "redis"
ssl = true
host = "${local.redis_fqdn}"
port = 6380
password = "${azurerm_redis_cache.main.primary_access_key}"
db = 1

[experiments]
enabled = true
automigrate = false

[experiments.store]
${local.db_config}

[processor]
# Configure the processing pipeline.
${var.toy_mode ? local.app_pipeline_toy_toml : local.app_pipeline_prod_toml}
EOF

  # Research environment configuration file.
  # This always includes the production pipeline for debugging purposes.
  research_env_toml = <<EOF
debug = true

[experiments]
enabled = true
automigrate = false

[experiments.store]
${local.db_config}

[processor]
${local.app_pipeline_prod_toml}
EOF
}
