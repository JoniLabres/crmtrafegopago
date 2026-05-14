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


_GTM_SCOPES = [
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/tagmanager.publish",
    "https://www.googleapis.com/auth/tagmanager.readonly",
]


def _get_service():
    from googleapiclient.discovery import build

    # OAuth refresh token (preferred — user-authorized via Conexões)
    refresh_token = os.getenv("GTM_OAUTH_REFRESH_TOKEN", "")
    client_id     = os.getenv("GTM_CLIENT_ID", "")
    client_secret = os.getenv("GTM_CLIENT_SECRET", "")

    if refresh_token and client_id and client_secret:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GRequest
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=_GTM_SCOPES,
        )
        creds.refresh(GRequest())
        return build("tagmanager", "v2", credentials=creds)

    # Service account fallback
    creds_path = os.getenv("GTM_CREDENTIALS_JSON", "")
    if creds_path and Path(creds_path).exists():
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=_GTM_SCOPES
        )
        return build("tagmanager", "v2", credentials=creds)

    raise EnvironmentError(
        "GTM não autenticado. Conecte via Conexões → GTM ou configure GTM_CREDENTIALS_JSON."
    )


def _container_path() -> str:
    account_id   = os.getenv("GTM_ACCOUNT_ID", "")
    container_id = os.getenv("GTM_CONTAINER_ID", "")
    public_id    = os.getenv("GTM_PUBLIC_ID", "")

    if not account_id:
        raise EnvironmentError("GTM_ACCOUNT_ID não configurado.")

    # If numeric container ID already known, use it directly
    if container_id and container_id.isdigit():
        return f"accounts/{account_id}/containers/{container_id}"

    # Resolve numeric ID from public ID (GTM-XXXXXX)
    if public_id:
        try:
            service = _get_service()
            resp = service.accounts().containers().list(
                parent=f"accounts/{account_id}"
            ).execute()
            for c in resp.get("container", []):
                if c.get("publicId") == public_id:
                    numeric = c["containerId"]
                    os.environ["GTM_CONTAINER_ID"] = numeric
                    try:
                        from dotenv import set_key
                        env_path = Path(__file__).parent.parent / ".env"
                        if env_path.exists():
                            set_key(str(env_path), "GTM_CONTAINER_ID", numeric)
                    except Exception:
                        pass
                    logger.info("GTM Container ID resolvido: %s → %s", public_id, numeric)
                    return f"accounts/{account_id}/containers/{numeric}"
            raise EnvironmentError(f"Container {public_id} não encontrado na conta {account_id}.")
        except EnvironmentError:
            raise
        except Exception as exc:
            raise EnvironmentError(f"Erro ao resolver container ID: {exc}") from exc

    raise EnvironmentError(
        "Configure GTM_CONTAINER_ID (numérico) ou GTM_PUBLIC_ID (ex: GTM-M3HZ5VQR)."
    )


def _resolve_workspace() -> str:
    """Creates a fresh workspace for each deploy to avoid 'already submitted' conflicts."""
    import datetime
    service   = _get_service()
    container = _container_path()
    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M")
    new_ws = service.accounts().containers().workspaces().create(
        parent=container,
        body={"name": f"IXCTraffic-{ts}", "description": "Deploy automático via IXCTraffic"},
    ).execute()
    wid = new_ws["workspaceId"]
    logger.info("Workspace GTM criado: %s", wid)
    return wid


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


def list_containers() -> list:
    """Returns all containers in the configured GTM account."""
    service    = _get_service()
    account_id = os.getenv("GTM_ACCOUNT_ID", "")
    if not account_id:
        raise EnvironmentError("GTM_ACCOUNT_ID não configurado.")
    resp = service.accounts().containers().list(parent=f"accounts/{account_id}").execute()
    return [
        {
            "containerId": c.get("containerId"),
            "publicId":    c.get("publicId"),
            "name":        c.get("name", c.get("publicId", "")),
        }
        for c in resp.get("container", [])
    ]


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


# ── Deploy completo — cria tudo no GTM via API ────────────────────────────────

_ALL_PAGES = "2147479553"  # Built-in GTM trigger ID for All Pages (PAGEVIEW)

_UTM_ENGINE_HTML = """<script>
(function(){
  var K=['utm_source','utm_medium','utm_campaign','utm_content','utm_term','gclid','fbclid'];
  var NS='ixc_',DAYS=30;
  function parse(){var p=new URLSearchParams(location.search),d={},f=false;
    K.forEach(function(k){var v=p.get(k);if(v){d[k]=v;f=true;}});return f?d:null;}
  function setCk(n,v,days){var e=new Date();e.setDate(e.getDate()+days);
    document.cookie=n+'='+encodeURIComponent(JSON.stringify(v))+';expires='+e.toUTCString()+';path=/;SameSite=Lax';}
  function getCk(n){var m=document.cookie.match(new RegExp('(?:^|; )'+n+'=([^;]*)'));
    try{return m?JSON.parse(decodeURIComponent(m[1])):null;}catch(e){return null;}}
  function rLS(k){try{return JSON.parse(localStorage.getItem(NS+k)||'null');}catch(e){return null;}}
  function wLS(k,v){try{localStorage.setItem(NS+k,JSON.stringify(v));}catch(e){}}
  var u=parse();
  if(u){
    wLS('utm_last',u);setCk(NS+'utm_last',u,DAYS);
    if(!rLS('utm_first')){wLS('utm_first',u);setCk(NS+'utm_first',u,365);}
    window.dataLayer=window.dataLayer||[];
    window.dataLayer.push(Object.assign({event:'utm_captured'},u));
  }
  window.IXC=window.IXC||{};
  window.IXC.getUtmLast=function(){return rLS('utm_last')||getCk(NS+'utm_last')||{};};
  window.IXC.getUtmFirst=function(){return rLS('utm_first')||getCk(NS+'utm_first')||{};};
})();
</script>"""

_FORM_INJECTOR_HTML = """<script>
(function(){
  function fill(form){
    if(!window.IXC)return;
    var u=window.IXC.getUtmLast();
    Object.keys(u).forEach(function(k){
      var el=form.querySelector('input[name="'+k+'"]');
      if(el)el.value=u[k];
    });
  }
  document.querySelectorAll('form').forEach(fill);
  new MutationObserver(function(ms){
    ms.forEach(function(m){
      m.addedNodes.forEach(function(n){
        if(n.nodeName==='FORM')fill(n);
        if(n.querySelectorAll)n.querySelectorAll('form').forEach(fill);
      });
    });
  }).observe(document.body,{childList:true,subtree:true});
  window.addEventListener('message',function(e){
    if(e.data&&e.data.type==='hsFormCallback'&&e.data.eventName==='onFormReady'){
      document.querySelectorAll('iframe.hs-form-iframe').forEach(function(f){
        try{f.contentDocument.querySelectorAll('form').forEach(fill);}catch(err){}
      });
    }
  });
})();
</script>"""

_ENGAGEMENT_HTML = """<script>
(function(){
  // Scroll depth
  var sf={};
  window.addEventListener('scroll',function(){
    var h=document.documentElement,pct=Math.round((h.scrollTop||document.body.scrollTop)/
      ((h.scrollHeight||document.body.scrollHeight)-h.clientHeight)*100);
    [25,50,75,100].forEach(function(m){
      if(pct>=m&&!sf[m]){sf[m]=true;
        window.dataLayer=window.dataLayer||[];
        window.dataLayer.push({event:'scroll_depth',scroll_threshold:m});}
    });
  },{passive:true});
  // Time on page
  [15,30,60,120,300].forEach(function(s){
    setTimeout(function(){
      window.dataLayer=window.dataLayer||[];
      window.dataLayer.push({event:'time_on_page',engagement_time_sec:s});
    },s*1000);
  });
  // CTA clicks
  document.addEventListener('click',function(e){
    var b=e.target.closest('[data-cta],button,.btn-cta');
    if(!b)return;
    window.dataLayer=window.dataLayer||[];
    window.dataLayer.push({event:'cta_click',cta_text:b.textContent.trim().slice(0,80),
      cta_id:b.getAttribute('data-cta')||b.id||''});
  });
  // Exit intent
  var ef=false;
  document.addEventListener('mouseleave',function(e){
    if(ef||e.clientY>20)return;ef=true;
    window.dataLayer=window.dataLayer||[];
    window.dataLayer.push({event:'exit_intent'});
  });
})();
</script>"""


def _html_tag(html: str) -> list:
    return [
        {"type": "TEMPLATE", "key": "html", "value": html},
        {"type": "BOOLEAN", "key": "supportDocumentWrite", "value": "false"},
    ]


def _custom_event_trigger(name: str, event_name: str) -> dict:
    return {
        "name": name,
        "type": "CUSTOM_EVENT",
        "customEventFilter": [{
            "type": "EQUALS",
            "parameter": [
                {"type": "TEMPLATE", "key": "arg0", "value": "{{_event}}"},
                {"type": "TEMPLATE", "key": "arg1", "value": event_name},
            ],
        }],
    }


def _thankyou_trigger(thankyou_path: str) -> dict:
    """Cria trigger PAGEVIEW que dispara quando a URL contém o path da página de obrigado."""
    return {
        "name": "PV - Página de Obrigado",
        "type": "PAGEVIEW",
        "filter": [{
            "type": "CONTAINS",
            "parameter": [
                {"type": "TEMPLATE", "key": "arg0", "value": "{{Page URL}}"},
                {"type": "TEMPLATE", "key": "arg1", "value": thankyou_path},
            ],
        }],
    }


def _extract_path(url: str) -> str:
    """Extrai somente o path de uma URL. Ex: https://site.com/obrigado → /obrigado"""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path or url
        # Remove trailing query string / fragment
        return path if path else url
    except Exception:
        return url


def deploy_all_tags(
    ga4_id: str = "",
    meta_pixel: str = "",
    li_partner: str = "",
    tiktok_pixel: str = "",
    hs_portal_id: str = "",
    workspace_id: str = "1",
    main_url: str = "",
    thankyou_url: str = "",
) -> dict:
    """Creates/updates all tracking tags, triggers and variables in GTM via API.
    Returns a log of every action taken."""
    service = _get_service()
    container = _container_path()
    workspace_id = _resolve_workspace()
    ws = f"{container}/workspaces/{workspace_id}"

    log: list[str] = []
    log.append(f"  ℹ Usando workspace {workspace_id}")

    # ── Load existing items (deduplication) ──────────────────────────────────
    existing = list_workspace(workspace_id)
    ex_tags     = {t["name"]: t for t in existing["tags"]}
    ex_triggers = {t["name"]: t for t in existing["triggers"]}
    ex_vars     = {v["name"]: v for v in existing["variables"]}

    def _upsert_var(body: dict):
        name = body["name"]
        if name in ex_vars:
            body["fingerprint"] = ex_vars[name].get("fingerprint", "")
            service.accounts().containers().workspaces().variables().update(
                path=ex_vars[name]["path"], body=body).execute()
            log.append(f"↺ Variável: {name}")
        else:
            service.accounts().containers().workspaces().variables().create(
                parent=ws, body=body).execute()
            log.append(f"✓ Variável criada: {name}")

    def _upsert_trigger(body: dict) -> str:
        name = body["name"]
        if name in ex_triggers:
            body["fingerprint"] = ex_triggers[name].get("fingerprint", "")
            r = service.accounts().containers().workspaces().triggers().update(
                path=ex_triggers[name]["path"], body=body).execute()
            log.append(f"↺ Trigger: {name}")
            return ex_triggers[name]["triggerId"]
        else:
            r = service.accounts().containers().workspaces().triggers().create(
                parent=ws, body=body).execute()
            log.append(f"✓ Trigger criado: {name}")
            return r["triggerId"]

    def _upsert_tag(body: dict):
        name = body["name"]
        if name in ex_tags:
            body["fingerprint"] = ex_tags[name].get("fingerprint", "")
            service.accounts().containers().workspaces().tags().update(
                path=ex_tags[name]["path"], body=body).execute()
            log.append(f"↺ Tag: {name}")
        else:
            service.accounts().containers().workspaces().tags().create(
                parent=ws, body=body).execute()
            log.append(f"✓ Tag criada: {name}")

    # ── 1. Variáveis UTM (dataLayer) ────────────────────────────────────────
    for k in ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]:
        _upsert_var({
            "name": f"DLV - {k}",
            "type": "v",
            "parameter": [
                {"type": "INTEGER", "key": "dataLayerVersion", "value": "2"},
                {"type": "BOOLEAN", "key": "setDefaultValue",  "value": "false"},
                {"type": "TEMPLATE", "key": "name",            "value": k},
            ],
        })

    # Variáveis Custom JS (lê do localStorage — funciona mesmo sem UTM na URL atual)
    for k in ["utm_source", "utm_medium", "utm_campaign"]:
        _upsert_var({
            "name": f"CJS - {k}",
            "type": "jsm",
            "parameter": [{"type": "TEMPLATE", "key": "javascript", "value":
                f"function(){{try{{return JSON.parse(localStorage.getItem('ixc_utm_last')||'{{}}').{k}||'';}}catch(e){{return '';}}}}"}],
        })

    # ── 2. Triggers ──────────────────────────────────────────────────────────
    # Trigger de conversão: pageview na página de obrigado (preferido)
    # ou fallback para evento generate_lead se URL não informada
    thankyou_path = _extract_path(thankyou_url)
    if thankyou_path:
        conv_tid = _upsert_trigger(_thankyou_trigger(thankyou_path))
        log.append(f"✓ Trigger de conversão: pageview em '{thankyou_path}'")
    else:
        conv_tid = _upsert_trigger(_custom_event_trigger("CE - generate_lead", "generate_lead"))
        log.append("  ℹ Trigger de conversão: evento generate_lead (sem URL de obrigado)")

    scroll_tid = _upsert_trigger(_custom_event_trigger("CE - scroll_depth",   "scroll_depth"))
    cta_tid    = _upsert_trigger(_custom_event_trigger("CE - cta_click",      "cta_click"))
    exit_tid   = _upsert_trigger(_custom_event_trigger("CE - exit_intent",    "exit_intent"))
    time_tid   = _upsert_trigger(_custom_event_trigger("CE - time_on_page",   "time_on_page"))
    lead_tid   = conv_tid  # alias para compatibilidade das tags abaixo

    # ── 3. Tags base (sempre presentes) ──────────────────────────────────────
    _upsert_tag({
        "name": "IXC - UTM Engine",
        "type": "html",
        "parameter": _html_tag(_UTM_ENGINE_HTML),
        "firingTriggerId": [_ALL_PAGES],
        "tagFiringOption": "oncePerPage",
    })
    _upsert_tag({
        "name": "IXC - Form UTM Injector",
        "type": "html",
        "parameter": _html_tag(_FORM_INJECTOR_HTML),
        "firingTriggerId": [_ALL_PAGES],
        "tagFiringOption": "oncePerPage",
    })
    _upsert_tag({
        "name": "IXC - Engagement Tracking",
        "type": "html",
        "parameter": _html_tag(_ENGAGEMENT_HTML),
        "firingTriggerId": [_ALL_PAGES],
        "tagFiringOption": "oncePerPage",
    })

    # ── 4. GA4 ───────────────────────────────────────────────────────────────
    if ga4_id:
        _upsert_tag({
            "name": "GA4 - Configuration",
            "type": "googtag",
            "parameter": [{"type": "TEMPLATE", "key": "id", "value": ga4_id}],
            "firingTriggerId": [_ALL_PAGES],
        })
        for evt, tid in [("generate_lead", lead_tid), ("scroll_depth", scroll_tid),
                         ("cta_click", cta_tid), ("exit_intent", exit_tid)]:
            _upsert_tag({
                "name": f"GA4 - {evt}",
                "type": "gaawe",
                "parameter": [
                    {"type": "TEMPLATE", "key": "measurementId", "value": ga4_id},
                    {"type": "TEMPLATE", "key": "eventName",     "value": evt},
                    {"type": "LIST", "key": "eventParameters", "list": [
                        {"type": "MAP", "map": [
                            {"type": "TEMPLATE", "key": "name",  "value": "utm_campaign"},
                            {"type": "TEMPLATE", "key": "value", "value": "{{DLV - utm_campaign}}"},
                        ]},
                        {"type": "MAP", "map": [
                            {"type": "TEMPLATE", "key": "name",  "value": "utm_source"},
                            {"type": "TEMPLATE", "key": "value", "value": "{{DLV - utm_source}}"},
                        ]},
                    ]},
                ],
                "firingTriggerId": [str(tid)],
            })
        log.append(f"  → GA4 ({ga4_id}): Configuration + 4 eventos")

    # ── 5. Meta Pixel ────────────────────────────────────────────────────────
    if meta_pixel:
        _upsert_tag({
            "name": "Meta Pixel - PageView",
            "type": "html",
            "parameter": _html_tag(
                f"<script>!function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?"
                f"n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;n.push=n;"
                f"n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;"
                f"s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window,document,"
                f"'script','https://connect.facebook.net/en_US/fbevents.js');"
                f"fbq('init','{meta_pixel}');fbq('track','PageView');\n</script>"
            ),
            "firingTriggerId": [_ALL_PAGES],
        })
        _upsert_tag({
            "name": "Meta Pixel - Lead",
            "type": "html",
            "parameter": _html_tag(
                "<script>var _u=(window.IXC&&window.IXC.getUtmLast())||{};"
                "fbq('track','Lead',{utm_campaign:_u.utm_campaign||'{{DLV - utm_campaign}}',"
                "utm_source:_u.utm_source||'{{DLV - utm_source}}',currency:'BRL',value:0});\n</script>"
            ),
            "firingTriggerId": [str(lead_tid)],
        })
        log.append(f"  → Meta Pixel ({meta_pixel}): PageView + Lead")

    # ── 6. LinkedIn ──────────────────────────────────────────────────────────
    if li_partner:
        _upsert_tag({
            "name": "LinkedIn - Insight Tag",
            "type": "html",
            "parameter": _html_tag(
                f'<script>_linkedin_partner_id="{li_partner}";'
                f'window._linkedin_data_partner_ids=window._linkedin_data_partner_ids||[];'
                f'window._linkedin_data_partner_ids.push(_linkedin_partner_id);</script>'
                f'<script async src="https://snap.licdn.com/li.lms-analytics/insight.min.js"></script>'
            ),
            "firingTriggerId": [_ALL_PAGES],
        })
        log.append(f"  → LinkedIn Insight Tag ({li_partner})")

    # ── 7. TikTok ────────────────────────────────────────────────────────────
    if tiktok_pixel:
        _upsert_tag({
            "name": "TikTok Pixel - PageView",
            "type": "html",
            "parameter": _html_tag(
                f"<script>!function(w,d,t){{w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];"
                f"ttq.methods=['page','track','identify'];ttq.setAndDefer=function(t,e){{t[e]=function()"
                f"{{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}};for(var i=0;i<ttq.methods.length;i++)"
                f"ttq.setAndDefer(ttq,ttq.methods[i]);ttq.load=function(e){{var i='https://analytics.tiktok.com/i18n/pixel/events.js';"
                f"ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._t=ttq._t||{{}},ttq._t[e]=+new Date;"
                f"var o=document.createElement('script');o.type='text/javascript',o.async=!0,"
                f"o.src=i+'?sdkid='+e+'&lib='+t;var a=document.getElementsByTagName('script')[0];"
                f"a.parentNode.insertBefore(o,a)}};ttq.load('{tiktok_pixel}');ttq.page()}}"
                f"(window,document,'ttq');\n</script>"
            ),
            "firingTriggerId": [_ALL_PAGES],
        })
        _upsert_tag({
            "name": "TikTok Pixel - Lead",
            "type": "html",
            "parameter": _html_tag("<script>ttq.track('SubmitForm');\n</script>"),
            "firingTriggerId": [str(lead_tid)],
        })
        log.append(f"  → TikTok Pixel ({tiktok_pixel}): PageView + Lead")

    # ── 8. HubSpot ───────────────────────────────────────────────────────────
    if hs_portal_id:
        _upsert_tag({
            "name": "HubSpot - Tracking Code",
            "type": "html",
            "parameter": _html_tag(
                f'<script type="text/javascript" id="hs-script-loader" async defer '
                f'src="//js.hs-scripts.com/{hs_portal_id}.js"></script>'
            ),
            "firingTriggerId": [_ALL_PAGES],
        })
        log.append(f"  → HubSpot Tracking (portal {hs_portal_id})")

    summary = (
        f"Deploy concluído: {len([l for l in log if l.startswith('✓')])} criados, "
        f"{len([l for l in log if l.startswith('↺')])} atualizados"
    )
    log.append(summary)
    return {"log": log, "ok": True, "workspace_id": workspace_id}


def generate_mega_snippet(
    gtm_public_id: str = "",
    ga4_id: str = "",
    meta_pixel: str = "",
    li_partner: str = "",
    tiktok_pixel: str = "",
    hs_portal_id: str = "",
) -> str:
    """Generates a single all-in-one tracking script for pages without GTM."""
    parts = ["<!-- IXCTraffic — Mega Snippet de Rastreamento -->", "<script>"]

    parts.append("// ── UTM Engine ─────────────────────────────────────────")
    parts.append("""(function(){
  var K=['utm_source','utm_medium','utm_campaign','utm_content','utm_term','gclid','fbclid'],NS='ixc_';
  function parse(){var p=new URLSearchParams(location.search),d={},f=false;
    K.forEach(function(k){var v=p.get(k);if(v){d[k]=v;f=true;}});return f?d:null;}
  function setCk(n,v,d){var e=new Date();e.setDate(e.getDate()+d);
    document.cookie=n+'='+encodeURIComponent(JSON.stringify(v))+';expires='+e.toUTCString()+';path=/;SameSite=Lax';}
  function getCk(n){var m=document.cookie.match(new RegExp('(?:^|; )'+n+'=([^;]*)'));
    try{return m?JSON.parse(decodeURIComponent(m[1])):null;}catch(e){return null;}}
  function rLS(k){try{return JSON.parse(localStorage.getItem(NS+k)||'null');}catch(e){return null;}}
  function wLS(k,v){try{localStorage.setItem(NS+k,JSON.stringify(v));}catch(e){}}
  var u=parse();if(u){wLS('utm_last',u);setCk(NS+'utm_last',u,30);
    if(!rLS('utm_first')){wLS('utm_first',u);setCk(NS+'utm_first',u,365);}
    window.dataLayer=window.dataLayer||[];window.dataLayer.push(Object.assign({event:'utm_captured'},u));}
  window.IXC=window.IXC||{};
  window.IXC.getUtmLast=function(){return rLS('utm_last')||getCk(NS+'utm_last')||{};};
  window.IXC.getUtmFirst=function(){return rLS('utm_first')||getCk(NS+'utm_first')||{};};
})();""")

    parts.append("""// ── Engagement (scroll, tempo, CTA, exit intent) ───────
(function(){
  var sf={};
  window.addEventListener('scroll',function(){
    var h=document.documentElement,pct=Math.round((h.scrollTop||document.body.scrollTop)/
      ((h.scrollHeight||document.body.scrollHeight)-h.clientHeight)*100);
    [25,50,75,100].forEach(function(m){if(pct>=m&&!sf[m]){sf[m]=true;
      window.dataLayer=window.dataLayer||[];window.dataLayer.push({event:'scroll_depth',scroll_threshold:m});}});
  },{passive:true});
  [15,30,60,120,300].forEach(function(s){setTimeout(function(){
    window.dataLayer=window.dataLayer||[];window.dataLayer.push({event:'time_on_page',engagement_time_sec:s});
  },s*1000);});
  document.addEventListener('click',function(e){var b=e.target.closest('[data-cta],button');if(!b)return;
    window.dataLayer=window.dataLayer||[];window.dataLayer.push({event:'cta_click',cta_text:b.textContent.trim().slice(0,80)});});
  var ef=false;document.addEventListener('mouseleave',function(e){if(ef||e.clientY>20)return;ef=true;
    window.dataLayer=window.dataLayer||[];window.dataLayer.push({event:'exit_intent'});});
})();""")

    parts.append("</script>")

    if gtm_public_id:
        parts.append(f"""<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':new Date().getTime(),event:'gtm.js'}});
var f=d.getElementsByTagName(s)[0],j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;
j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','{gtm_public_id}');</script>
<!-- End Google Tag Manager -->""")

    if ga4_id and not gtm_public_id:
        parts.append(f"""<!-- GA4 -->
<script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}
gtag('js',new Date());gtag('config','{ga4_id}');</script>""")

    if meta_pixel:
        parts.append(f"""<!-- Meta Pixel -->
<script>!function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;
n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;
s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window,document,'script',
'https://connect.facebook.net/en_US/fbevents.js');fbq('init','{meta_pixel}');fbq('track','PageView');
</script>""")

    if li_partner:
        parts.append(f"""<!-- LinkedIn Insight Tag -->
<script>_linkedin_partner_id="{li_partner}";
window._linkedin_data_partner_ids=window._linkedin_data_partner_ids||[];
window._linkedin_data_partner_ids.push(_linkedin_partner_id);</script>
<script async src="https://snap.licdn.com/li.lms-analytics/insight.min.js"></script>""")

    if tiktok_pixel:
        parts.append(f"""<!-- TikTok Pixel -->
<script>!function(w,d,t){{w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];
ttq.methods=['page','track','identify'];ttq.setAndDefer=function(t,e){{t[e]=function(){{
t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}};
for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);
ttq.load=function(e){{var i='https://analytics.tiktok.com/i18n/pixel/events.js';
ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._t=ttq._t||{{}},ttq._t[e]=+new Date;
var o=document.createElement('script');o.async=!0,o.src=i+'?sdkid='+e+'&lib='+t;
document.getElementsByTagName('script')[0].parentNode.insertBefore(o,a)}};
ttq.load('{tiktok_pixel}');ttq.page()}}(window,document,'ttq');\n</script>""")

    if hs_portal_id:
        parts.append(f"""<!-- HubSpot -->
<script type="text/javascript" id="hs-script-loader" async defer
  src="//js.hs-scripts.com/{hs_portal_id}.js"></script>""")

    parts.append("""<!-- Injeção de UTMs nos formulários -->
<script>
(function(){
  function fill(f){if(!window.IXC)return;var u=window.IXC.getUtmLast();
    Object.keys(u).forEach(function(k){var el=f.querySelector('input[name="'+k+'"]');if(el)el.value=u[k];});}
  document.querySelectorAll('form').forEach(fill);
  new MutationObserver(function(ms){ms.forEach(function(m){m.addedNodes.forEach(function(n){
    if(n.nodeName==='FORM')fill(n);if(n.querySelectorAll)n.querySelectorAll('form').forEach(fill);});});
  }).observe(document.body,{childList:true,subtree:true});
})();
</script>""")

    parts.append("<!-- Fim IXCTraffic Mega Snippet -->")
    return "\n".join(parts)


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
