locals {
  # Choose DBs for Redis storage. Ideally it's nice to separate concerns into
  # different databases. But the Enterprise cluster only actually supports 1
  # database (!), so we just have to stuff everything in there.
  # NOTE(jnu): careful about changing this, as it will impact existing deployments.
  # (This is the reason I've kept them separate for non-enterprise SKUs, since that
  # is how they were originally deployed.)
  redis_store_db  = "0"
  redis_broker_db = local.redis_needs_enterprise_cache ? "0" : "1"

  # Database configuration segment
  db_config = <<EOF
engine = "mssql"
user = "${var.db_user}"
password = "${var.db_password}"
host = "${local.mssql_fqdn}"
database = "${azurerm_mssql_database.main.name}"
EOF

  # Config for embedding model
  embedding_config = <<EOF
[experiments.embedding]
[experiments.embedding.client]
azure_endpoint = "${local.openai_endpoint}"
api_key = "${azurerm_cognitive_account.openai.primary_access_key}"
api_version = "2024-06-01"
[experiments.embedding.generator]
model = "${local.openai_embedding_deployment_name}"
openai_model = "${var.openai_embedding_model}"
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
# 2) Parse textual output into coherent narrative with OpenAI.
# Parsing will happen in multiple chunks if the text is too long.
engine = "$chunk"
max_iterations = ${var.parse_chunks_max_iterations}

[processor.pipe.processor]
engine = "parse:openai"

[processor.pipe.processor.client]
azure_endpoint = "${local.openai_endpoint}"
api_key = "${azurerm_cognitive_account.openai.primary_access_key}"
api_version = "2024-06-01"

[processor.pipe.processor.generator]
method = "chat"
model = "${azurerm_cognitive_deployment.llm.name}"
openai_model = "${local.full_openai_llm_model}"
system = { prompt_id = "parse" }


[[processor.pipe]]
# 3) Redact racial information with OpenAI
engine = "$chunk"
max_iterations = ${var.redact_chunks_max_iterations}

[processor.pipe.processor]
engine = "$compose"

[[processor.pipe.processor.pipe]]
# 3a) Apply redaction with LLM
engine = "redact:openai"
delimiters = ["[", "]"]
[processor.pipe.processor.pipe.client]
azure_endpoint = "${local.openai_endpoint}"
api_key = "${azurerm_cognitive_account.openai.primary_access_key}"
api_version = "2024-06-01"

[processor.pipe.processor.pipe.generator]
method = "chat"
model = "${azurerm_cognitive_deployment.llm.name}"
openai_model = "${local.full_openai_llm_model}"
system = { prompt_id = "redact" }

[[processor.pipe.processor.pipe]]
# 3b) Inspect redaction to generate placeholder list
engine = "inspect:placeholders"
[processor.pipe.processor.pipe.client]
azure_endpoint = "${local.openai_endpoint}"
api_key = "${azurerm_cognitive_account.openai.primary_access_key}"
api_version = "2024-06-01"
[processor.pipe.processor.pipe.generator]
model = "${azurerm_cognitive_deployment.llm.name}"
openai_model = "${local.full_openai_llm_model}"


[[processor.pipe]]
# 4) Inspector for debugging
engine = "inspect:quality"

[[processor.pipe]]
# 5) Generate map from Id to mask
engine = "inspect:subject_masks"
[processor.pipe.client]
azure_endpoint = "${local.openai_endpoint}"
api_key = "${azurerm_cognitive_account.openai.primary_access_key}"
api_version = "2024-06-01"
[processor.pipe.generator]
model = "${azurerm_cognitive_deployment.llm.name}"
openai_model = "${local.full_openai_llm_model}"

[[processor.pipe]]
# 6) Generate list of annotations to store in context
engine = "inspect:annotations"
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

concurrency = ${var.worker_threads}

[queue.task]
retention_time_seconds = ${var.queue_store_retention}

[queue.store]
engine = "redis"
host = "${local.redis_fqdn}"
ssl = true
cluster = ${local.redis_needs_enterprise_cache}
port = ${local.redis_port}
password = "${local.redis_access_key}"
db = ${local.redis_store_db}

[queue.broker]
engine = "redis"
ssl = true
cluster = ${local.redis_needs_enterprise_cache}
host = "${local.redis_fqdn}"
port = ${local.redis_port}
password = "${local.redis_access_key}"
db = ${local.redis_broker_db}

[experiments]
enabled = true
automigrate = false

[experiments.store]
${local.db_config}

${local.embedding_config}

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
