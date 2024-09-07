locals {
  db_config = <<EOF
engine = "mssql"
user = "${var.db_user}"
password = "${var.db_password}"
host = "${local.mssql_fqdn}"
database = "${azurerm_mssql_database.main.name}"
EOF

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
system = { prompt = """\
I am providing you with a list of paragraphs extracted from a \
police report via Azure Document Intelligence.

Please extract any and all paragraphs in this output that were \
derived from a police narrative. A police narrative is a detailed \
account of events that occurred during a police incident. It typically \
includes information such as the date, time, location, and description \
of the incident, as well as the actions taken by the police officers \
involved.

You should return back ONLY these paragraphs. Do not return anything \
else.

If you are unable to identify any police narratives in the output, \
please return an empty string.""" }

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
system = { prompt = """\
Your job is to redact all race-related information in the provided \
text. Race-related information is any word from the following strict \
categories:
- Explicit mentions of race or ethnicity
- People's names
- Physical descriptions: Hair color, eye color, or skin color ONLY
- Location information: Addresses, neighborhood names, commercial \
establishment names, or major landmarks

Do NOT redact any other types of information, e.g., do not redact \
dates, objects, or other types of words not listed here.

Replace any person's name with an abbreviation indicating their role \
in the incident. For example, for the first mentioned victim, use
"[Victim 1]". Similarly, for the second mentioned victim, use \
"[Victim 2]". Be as specific as possible about their role (e.g., \
"Officer Smith and Sergeant Doe" should become "[Officer 1] and \
[Sergeant 1]"). If a person's role in the incident is unclear, \
use a generic “[Person X]” (with X replaced by the appropriate \
number).

If "John Doe" appears in the list of individuals, and then "Johnny \
D." appears in the narrative, use context to decide if "Johnny D." \
should be redacted with the same replacement as "John Doe." \
Similarly, if "Safeway" appears in the list of locations with \
abbreviation [Store 1], "Safeway Deli" should be redacted as \
"[Store 1] Deli".""" }
EOF
}
