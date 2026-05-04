"""
Google Tag Manager API integration.

Connects to the GTM API to:
  - List workspace tags, triggers, and variables
  - Publish/sync the local gtm_config.json to a GTM container
  - Verify that required UTM variables and conversion tags are present

Required env vars:
  GTM_ACCOUNT_ID     — GTM account ID (numeric)
  GTM_CONTAINER_ID   — GTM container ID (numeric)
  GTM_CREDENTIALS_JSON — path to service account JSON with GTM Edit access

Install: pip install google-api-python-client google-auth
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

GTM_CONFIG_PATH = Path(__file__).parent / "gtm_config.json"
REQUIRED_VARIABLES = [
    "URL - utm_source",
    "URL - utm_medium",
    "URL - utm_campaign",
    "URL - utm_content",
    "URL - utm_term",
]
REQUIRED_TAGS = [
    "GA4 - Configuration",
    "Meta Pixel - PageView",
]


def _get_service():
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    creds_path = os.getenv("GTM_CREDENTIALS_JSON", "")
    if not creds_path or not Path(creds_path).exists():
        raise EnvironmentError(
            "GTM_CREDENTIALS_JSON não configurado ou arquivo não encontrado"
        )

    scopes = ["https://www.googleapis.com/auth/tagmanager.edit.containers"]
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
    return build("tagmanager", "v2", credentials=creds)


def _container_path() -> str:
    account_id = os.getenv("GTM_ACCOUNT_ID", "")
    container_id = os.getenv("GTM_CONTAINER_ID", "")
    if not account_id or not container_id:
        raise EnvironmentError("GTM_ACCOUNT_ID e GTM_CONTAINER_ID devem ser configurados")
    return f"accounts/{account_id}/containers/{container_id}"


def list_workspace(workspace_id: str = "1") -> dict:
    """Returns all tags, triggers, and variables in a workspace."""
    service = _get_service()
    container = _container_path()
    workspace = f"{container}/workspaces/{workspace_id}"

    tags = service.accounts().containers().workspaces().tags().list(parent=workspace).execute()
    triggers = service.accounts().containers().workspaces().triggers().list(parent=workspace).execute()
    variables = service.accounts().containers().workspaces().variables().list(parent=workspace).execute()

    return {
        "tags": tags.get("tag", []),
        "triggers": triggers.get("trigger", []),
        "variables": variables.get("variable", []),
    }


def verify_setup(workspace_id: str = "1") -> dict:
    """
    Checks that all required UTM variables and conversion tags are present.
    Returns a dict with status and any missing items.
    """
    workspace = list_workspace(workspace_id)
    existing_vars = {v.get("name") for v in workspace["variables"]}
    existing_tags = {t.get("name") for t in workspace["tags"]}

    missing_vars = [v for v in REQUIRED_VARIABLES if v not in existing_vars]
    missing_tags = [t for t in REQUIRED_TAGS if t not in existing_tags]

    status = "ok" if not missing_vars and not missing_tags else "incompleto"
    result = {
        "status": status,
        "variaveis_presentes": len(existing_vars),
        "tags_presentes": len(existing_tags),
        "variaveis_faltando": missing_vars,
        "tags_faltando": missing_tags,
    }

    if status == "ok":
        logger.info("GTM setup verificado: tudo OK (%d vars, %d tags)", len(existing_vars), len(existing_tags))
    else:
        logger.warning("GTM setup incompleto: %s vars e %s tags faltando", missing_vars, missing_tags)

    return result


def get_container_info() -> dict:
    """Returns GTM container metadata."""
    service = _get_service()
    container = _container_path()
    info = service.accounts().containers().get(path=container).execute()
    return {
        "name": info.get("name"),
        "container_id": info.get("containerId"),
        "public_id": info.get("publicId"),
        "usage_context": info.get("usageContext", []),
        "domain_name": info.get("domainName", []),
        "fingerprint": info.get("fingerprint"),
    }


def publish_workspace(workspace_id: str = "1", note: str = "Publicado via IXCTraffic") -> dict:
    """Creates a version from the workspace and publishes it."""
    service = _get_service()
    container = _container_path()
    workspace = f"{container}/workspaces/{workspace_id}"

    logger.info("Criando versão GTM do workspace %s", workspace_id)
    version_body = {"name": note, "notes": note}
    version = (
        service.accounts()
        .containers()
        .workspaces()
        .create_version(path=workspace, body=version_body)
        .execute()
    )

    container_version = version.get("containerVersion", {})
    version_path = container_version.get("path", "")
    if not version_path:
        raise RuntimeError("Versão GTM criada mas path não retornado")

    logger.info("Publicando versão GTM: %s", version_path)
    published = service.accounts().containers().versions().publish(path=version_path).execute()
    return {
        "version_id": container_version.get("containerVersionId"),
        "status": "publicado",
        "details": published,
    }


def get_snippet() -> str:
    """Returns the GTM container snippet (for embedding in HTML)."""
    account_id = os.getenv("GTM_ACCOUNT_ID", "")
    container_id = os.getenv("GTM_CONTAINER_ID", "")
    if not account_id or not container_id:
        return ""

    try:
        service = _get_service()
        container = _container_path()
        info = service.accounts().containers().get(path=container).execute()
        public_id = info.get("publicId", f"GTM-{container_id}")
    except Exception:
        public_id = os.getenv("GTM_PUBLIC_ID", f"GTM-XXXXXX")

    head_snippet = f"""<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','{public_id}');</script>
<!-- End Google Tag Manager -->"""

    return head_snippet


def push_datalayer_event(event_name: str, data: dict) -> str:
    """Returns the JavaScript snippet to push an event to the GTM dataLayer."""
    payload = json.dumps({"event": event_name, **data}, ensure_ascii=False)
    return f"window.dataLayer = window.dataLayer || []; window.dataLayer.push({payload});"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    try:
        info = get_container_info()
        print(f"\nContainer: {info['name']} ({info['public_id']})")
        result = verify_setup()
        print(f"Status: {result['status']}")
        if result["variaveis_faltando"]:
            print(f"Variáveis faltando: {result['variaveis_faltando']}")
        if result["tags_faltando"]:
            print(f"Tags faltando: {result['tags_faltando']}")
    except EnvironmentError as e:
        print(f"Erro de configuração: {e}")
        sys.exit(1)
