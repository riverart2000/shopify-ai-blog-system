"""
routes/setup.py — Setup UI, branching by session role.

Admin session (store_id == '__admin__'):
  GET  /setup                 — Admin home: stores list + admin password tab
  POST /setup/stores/save     — Create/update a store
  POST /setup/stores/delete   — Delete a store
  POST /setup/stores/password — Set/change a store's login password
  POST /setup/admin-password  — Change admin password

Store session (store_id != '__admin__'):
  GET  /setup                       — Store home: models, prompts, shopify-config, schedule, password tabs
  POST /setup/models/save           — Create/update an AI model
  POST /setup/models/delete         — Delete a model
  POST /setup/models/toggle         — Toggle model active state
  POST /setup/prompts/save          — Create/update a prompt
  POST /setup/prompts/delete        — Delete a prompt
  POST /setup/prompts/set-default   — Set default prompt
  POST /setup/shopify/save          — Update store Shopify credentials
  POST /setup/password              — Change store login password
"""

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlencode
from typing import Annotated

import tomllib

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import db
import providers
import state
from security import hash_password, verify_password
from services.keyword_service import fetch_keywords
from services.title_service import fetch_titles as fetch_blog_titles

router = APIRouter(prefix="/setup")
logger = logging.getLogger("ai_blog_server")

_ADMIN = "__admin__"
_REACT_APP_TOML = Path(__file__).resolve().parents[2] / "ai-blog-generator-app" / "shopify.app.toml"


def _is_admin(request: Request) -> bool:
    return request.session.get("store_id") == _ADMIN


def _normalized_shop_domain(value: str) -> str:
    return value.strip().replace("https://", "").replace("http://", "").rstrip("/")


def _shopify_react_app_base_url() -> str:
    env_url = os.environ.get("SHOPIFY_REACT_APP_URL", "").strip().rstrip("/")
    if env_url:
        return env_url

    try:
        with _REACT_APP_TOML.open("rb") as fh:
            data = tomllib.load(fh)
        return str(data.get("application_url", "")).strip().rstrip("/")
    except Exception:
        return ""


def _shopify_react_app_install_url(myshopify_domain: str) -> str:
    base_url = _shopify_react_app_base_url()
    shop = _normalized_shop_domain(myshopify_domain)
    if not base_url or not shop:
        return ""
    return f"{base_url}/auth/login?{urlencode({'shop': shop})}"


# ---------------------------------------------------------------------------
# GET /setup
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def setup_page(request: Request, saved: str = "", error: str = "", tab: str = ""):
    store_id = request.session.get("store_id", "")

    if _is_admin(request):
        stores = await db.get_stores()
        return state.templates.TemplateResponse(request, "setup.html", {
            "is_admin": True,
            "stores": stores,
            "saved": saved,
            "error": error,
            "active_tab": tab or "stores",
        })

    store = await db.get_store(store_id)
    if not store:
        return RedirectResponse("/logout", status_code=303)

    models = await db.get_models(store_id)
    prompts = await db.get_prompts(store_id)
    default_prompt_id = await db.get_store_setting(store_id, "default_prompt_id", "")
    logo_data = await db.get_store_setting(store_id, "logo_data", "")
    import json as _json
    try:
        cached_blogs = _json.loads(await db.get_store_setting(store_id, "cached_blogs", "[]"))
    except Exception:
        cached_blogs = []
    try:
        social_share_buttons = _json.loads(
            await db.get_store_setting(store_id, "social_share_buttons", '["x","facebook","linkedin"]')
        )
    except Exception:
        social_share_buttons = ["x", "facebook", "linkedin"]
    social_x_handle = await db.get_store_setting(store_id, "social_x_handle", "")
    social_facebook_url = await db.get_store_setting(store_id, "social_facebook_url", "")
    social_linkedin_url = await db.get_store_setting(store_id, "social_linkedin_url", "")
    prompt_ending = await db.get_store_setting(store_id, "prompt_ending", "")
    tavily_api_key = await db.get_store_setting(store_id, "tavily_api_key", "")
    exa_api_key = await db.get_store_setting(store_id, "exa_api_key", "")
    keyword_niche = await db.get_store_setting(store_id, "keyword_niche", "")
    keyword_max_pool = int(await db.get_store_setting(store_id, "keyword_max_pool", "100"))
    keyword_pool = await db.get_keyword_pool(store_id, limit=200)
    keyword_pool_count = await db.count_keyword_pool(store_id)
    title_gen_model_id = await db.get_store_setting(store_id, "title_gen_model_id", "")
    title_gen_prompt_id = await db.get_store_setting(store_id, "title_gen_prompt_id", "")
    title_pool = await db.get_title_pool(store_id, limit=200)
    title_pool_count = await db.count_title_pool(store_id)
    text_models = [m for m in models if m.get("model_type") == "text" and m.get("is_active")]

    return state.templates.TemplateResponse(request, "setup.html", {
        "is_admin": False,
        "store": store,
        "models": models,
        "prompts": prompts,
        "default_prompt_id": default_prompt_id,
        "logo_data": logo_data,
        "cached_blogs": cached_blogs,
        "social_share_buttons": social_share_buttons,
        "social_x_handle": social_x_handle,
        "social_facebook_url": social_facebook_url,
        "social_linkedin_url": social_linkedin_url,
        "prompt_ending": prompt_ending,
        "default_prompt_ending": providers.DEFAULT_PROMPT_ENDING,
        "shopify_react_app_install_url": _shopify_react_app_install_url(store.get("myshopify_domain", "")),
        "shopify_react_app_base_url": _shopify_react_app_base_url(),
        "tavily_api_key": tavily_api_key,
        "exa_api_key": exa_api_key,
        "keyword_niche": keyword_niche,
        "keyword_max_pool": keyword_max_pool,
        "keyword_pool": keyword_pool,
        "keyword_pool_count": keyword_pool_count,
        "title_gen_model_id": title_gen_model_id,
        "title_gen_prompt_id": title_gen_prompt_id,
        "title_pool": title_pool,
        "title_pool_count": title_pool_count,
        "text_models": text_models,
        "saved": saved,
        "error": error,
        "active_tab": tab or "models",
    })


# ---------------------------------------------------------------------------
# Admin: stores management
# ---------------------------------------------------------------------------

@router.post("/stores/save", response_class=HTMLResponse)
async def admin_save_store(
    request: Request,
    store_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    myshopify_domain: Annotated[str, Form()] = "",
    custom_domain: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
    default_blog_handle: Annotated[str, Form()] = "",
    default_author: Annotated[str, Form()] = "",
):
    if not _is_admin(request):
        return RedirectResponse("/setup", status_code=303)

    if not name.strip():
        return RedirectResponse("/setup?error=Store+name+is+required", status_code=303)

    sid = store_id.strip() or name.strip().lower().replace(" ", "_")
    existing_stores = await db.get_stores()
    existing = next((s for s in existing_stores if s["id"] == sid), None)
    max_order = max((s["sort_order"] for s in existing_stores), default=-1)

    await db.upsert_store({
        "id": sid,
        "name": name.strip(),
        "myshopify_domain": myshopify_domain.strip(),
        "custom_domain": custom_domain.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "default_blog_handle": default_blog_handle.strip() or "news",
        "default_author": default_author.strip() or f"{name.strip()} Team",
        "sort_order": existing["sort_order"] if existing else max_order + 1,
    })
    return RedirectResponse("/setup?saved=store&tab=stores", status_code=303)


@router.post("/stores/delete", response_class=HTMLResponse)
async def admin_delete_store(
    request: Request,
    store_id: Annotated[str, Form()],
):
    if not _is_admin(request):
        return RedirectResponse("/setup", status_code=303)
    await db.delete_store(store_id)
    return RedirectResponse("/setup?saved=store-deleted&tab=stores", status_code=303)


@router.post("/stores/password", response_class=HTMLResponse)
async def admin_set_store_password(
    request: Request,
    store_id: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
):
    if not _is_admin(request):
        return RedirectResponse("/setup", status_code=303)
    if not new_password.strip():
        return RedirectResponse("/setup?error=Password+cannot+be+empty&tab=stores", status_code=303)
    await db.set_store_password_hash(store_id, hash_password(new_password))
    logger.info("Admin set password for store %s", store_id)
    return RedirectResponse("/setup?saved=store-password&tab=stores", status_code=303)


@router.post("/admin-password", response_class=HTMLResponse)
async def admin_change_password(
    request: Request,
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
):
    if not _is_admin(request):
        return RedirectResponse("/setup", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/setup?error=Passwords+do+not+match&tab=admin-password", status_code=303)
    await db.set_admin_password_hash(hash_password(new_password))
    return RedirectResponse("/setup?saved=1&tab=admin-password", status_code=303)


# ---------------------------------------------------------------------------
# Store: AI models
# ---------------------------------------------------------------------------

@router.post("/models/save", response_class=HTMLResponse)
async def save_model(
    request: Request,
    model_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    provider: Annotated[str, Form()] = "",
    model_type: Annotated[str, Form()] = "",
    model_name: Annotated[str, Form()] = "",
    api_key: Annotated[str, Form()] = "",
    endpoint: Annotated[str, Form()] = "",
    extra_json: Annotated[str, Form()] = "{}",
    priority: Annotated[str, Form()] = "0",
    is_active: Annotated[str, Form()] = "1",
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)

    if not name.strip() or not provider.strip():
        return RedirectResponse("/setup?error=Name+and+provider+are+required&tab=models", status_code=303)

    # Validate extra_json
    try:
        json.loads(extra_json.strip() or "{}")
    except json.JSONDecodeError:
        return RedirectResponse("/setup?error=Extra+JSON+is+not+valid+JSON&tab=models", status_code=303)

    await db.upsert_model({
        "id": model_id.strip() or None,
        "store_id": store_id,
        "name": name.strip(),
        "provider": provider.strip(),
        "model_type": model_type.strip() or "text",
        "model_name": model_name.strip(),
        "api_key": api_key.strip(),
        "endpoint": endpoint.strip(),
        "extra_json": extra_json.strip() or "{}",
        "priority": int(priority) if priority.isdigit() else 0,
        "is_active": 1 if is_active in ("1", "on", "true", "yes") else 0,
    })
    return RedirectResponse("/setup?saved=model&tab=models", status_code=303)


@router.post("/models/delete", response_class=HTMLResponse)
async def delete_model(
    request: Request,
    model_id: Annotated[str, Form()],
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    # Verify model belongs to this store
    model = await db.get_model(model_id)
    if model and model["store_id"] == store_id:
        await db.delete_model(model_id)
    return RedirectResponse("/setup?saved=model-deleted&tab=models", status_code=303)


@router.post("/models/toggle", response_class=HTMLResponse)
async def toggle_model(
    request: Request,
    model_id: Annotated[str, Form()],
    is_active: Annotated[str, Form()],
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    model = await db.get_model(model_id)
    if model and model["store_id"] == store_id:
        await db.set_model_active(model_id, is_active in ("1", "on", "true", "yes"))
    return RedirectResponse("/setup?tab=models", status_code=303)


# ---------------------------------------------------------------------------
# Store: prompts
# ---------------------------------------------------------------------------

@router.post("/prompts/save", response_class=HTMLResponse)
async def save_prompt(
    request: Request,
    prompt_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    text: Annotated[str, Form()] = "",
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)

    if not name.strip() or not text.strip():
        return RedirectResponse(
            "/setup?error=Prompt+name+and+text+are+required&tab=prompts", status_code=303
        )

    pid = prompt_id.strip() or name.strip().lower().replace(" ", "_")
    prompts = await db.get_prompts(store_id)
    max_order = max((p["sort_order"] for p in prompts), default=-1)
    existing = next((p for p in prompts if p["id"] == pid), None)

    await db.upsert_prompt({
        "id": pid,
        "store_id": store_id,
        "name": name.strip(),
        "text": text.strip(),
        "sort_order": existing["sort_order"] if existing else max_order + 1,
    })
    return RedirectResponse("/setup?saved=prompt&tab=prompts", status_code=303)


@router.post("/prompts/delete", response_class=HTMLResponse)
async def delete_prompt(
    request: Request,
    prompt_id: Annotated[str, Form()],
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    await db.delete_prompt(prompt_id)
    return RedirectResponse("/setup?saved=prompt-deleted&tab=prompts", status_code=303)


@router.post("/prompts/set-default", response_class=HTMLResponse)
async def set_default_prompt(
    request: Request,
    prompt_id: Annotated[str, Form()],
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    await db.set_store_settings(store_id, {"default_prompt_id": prompt_id})
    return RedirectResponse("/setup?saved=1&tab=prompts", status_code=303)


@router.post("/prompts/ending", response_class=HTMLResponse)
async def save_prompt_ending(
    request: Request,
    prompt_ending: Annotated[str, Form()] = "",
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    await db.set_store_settings(store_id, {"prompt_ending": prompt_ending.strip()})
    return RedirectResponse("/setup?saved=1&tab=prompts", status_code=303)


# ---------------------------------------------------------------------------
# Store: Shopify config
# ---------------------------------------------------------------------------

@router.post("/shopify/save", response_class=HTMLResponse)
async def save_shopify_config(
    request: Request,
    myshopify_domain: Annotated[str, Form()] = "",
    custom_domain: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
    default_blog_handle: Annotated[str, Form()] = "",
    default_author: Annotated[str, Form()] = "",
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)

    store = await db.get_store(store_id)
    if not store:
        return RedirectResponse("/logout", status_code=303)

    await db.upsert_store({
        "id": store_id,
        "name": store["name"],
        "myshopify_domain": myshopify_domain.strip() or store["myshopify_domain"],
        "custom_domain": custom_domain.strip() if custom_domain.strip() else store.get("custom_domain", ""),
        "client_id": client_id.strip() or store["client_id"],
        "client_secret": client_secret.strip() or store["client_secret"],
        "default_blog_handle": default_blog_handle.strip() or store["default_blog_handle"],
        "default_author": default_author.strip() or store["default_author"],
        "sort_order": store.get("sort_order", 0),
    })
    return RedirectResponse("/setup?saved=shopify&tab=shopify", status_code=303)


@router.post("/sharing/save", response_class=HTMLResponse)
async def save_sharing_config(
    request: Request,
    share_x: Annotated[str, Form()] = "",
    share_facebook: Annotated[str, Form()] = "",
    share_linkedin: Annotated[str, Form()] = "",
    social_x_handle: Annotated[str, Form()] = "",
    social_facebook_url: Annotated[str, Form()] = "",
    social_linkedin_url: Annotated[str, Form()] = "",
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    enabled = []
    if share_x:
        enabled.append("x")
    if share_facebook:
        enabled.append("facebook")
    if share_linkedin:
        enabled.append("linkedin")
    # Strip leading @ from X handle if user included it
    x_handle = social_x_handle.strip().lstrip("@")
    await db.set_store_settings(store_id, {
        "social_share_buttons": json.dumps(enabled),
        "social_x_handle": x_handle,
        "social_facebook_url": social_facebook_url.strip(),
        "social_linkedin_url": social_linkedin_url.strip(),
    })
    logger.info("Social sharing config updated for store %s: buttons=%s", store_id, enabled)
    return RedirectResponse("/setup?saved=sharing&tab=shopify", status_code=303)


@router.post("/logo/upload", response_class=HTMLResponse)
async def upload_logo(
    request: Request,
    logo_file: UploadFile = File(...),
):
    import base64

    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)

    content_type = logo_file.content_type or "image/png"
    if not content_type.startswith("image/"):
        return RedirectResponse("/setup?error=Only+image+files+are+accepted&tab=shopify", status_code=303)

    data = await logo_file.read()
    if len(data) > 2 * 1024 * 1024:   # 2 MB guard
        return RedirectResponse("/setup?error=Logo+must+be+under+2+MB&tab=shopify", status_code=303)

    b64 = base64.b64encode(data).decode()
    logo_data_uri = f"data:{content_type};base64,{b64}"
    await db.set_store_settings(store_id, {"logo_data": logo_data_uri})
    logger.info("Logo uploaded for store %s (%d bytes)", store_id, len(data))
    return RedirectResponse("/setup?saved=logo&tab=shopify", status_code=303)

@router.post("/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
):
    store_id = request.session.get("store_id", "")
    if not store_id:
        return RedirectResponse("/logout", status_code=303)

    if new_password != confirm_password:
        return RedirectResponse("/setup?error=Passwords+do+not+match&tab=password", status_code=303)

    if _is_admin(request):
        await db.set_admin_password_hash(hash_password(new_password))
    else:
        await db.set_store_password_hash(store_id, hash_password(new_password))

    return RedirectResponse("/setup?saved=1&tab=password", status_code=303)


# ---------------------------------------------------------------------------
# Store: keyword pool management
# ---------------------------------------------------------------------------

@router.post("/keywords/save", response_class=HTMLResponse)
async def save_keyword_settings(
    request: Request,
    tavily_api_key: Annotated[str, Form()] = "",
    exa_api_key: Annotated[str, Form()] = "",
    keyword_niche: Annotated[str, Form()] = "",
    keyword_max_pool: Annotated[str, Form()] = "100",
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)

    try:
        max_pool = max(10, min(500, int(keyword_max_pool)))
    except ValueError:
        max_pool = 100

    settings: dict = {
        "keyword_niche": keyword_niche.strip(),
        "keyword_max_pool": str(max_pool),
    }
    # Only overwrite keys if the user submitted a non-blank value
    if tavily_api_key.strip():
        settings["tavily_api_key"] = tavily_api_key.strip()
    if exa_api_key.strip():
        settings["exa_api_key"] = exa_api_key.strip()

    await db.set_store_settings(store_id, settings)
    logger.info("Keyword settings saved for store %s (niche=%r, max_pool=%d)", store_id, keyword_niche.strip(), max_pool)
    return RedirectResponse("/setup?saved=keywords&tab=keywords", status_code=303)


@router.post("/keywords/fetch", response_class=JSONResponse)
async def fetch_keywords_now(request: Request):
    """AJAX — trigger an immediate keyword fetch and return JSON result."""
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return JSONResponse({"ok": False, "error": "Not authorised."}, status_code=403)

    niche = await db.get_store_setting(store_id, "keyword_niche", "")
    max_pool = int(await db.get_store_setting(store_id, "keyword_max_pool", "100"))

    result = await fetch_keywords(store_id, niche, max_pool=max_pool)
    return JSONResponse({"ok": result["error"] is None, **result})


@router.post("/keywords/delete", response_class=HTMLResponse)
async def delete_keyword(
    request: Request,
    keyword_id: Annotated[int, Form()],
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    await db.delete_keyword(keyword_id)
    return RedirectResponse("/setup?tab=keywords", status_code=303)


@router.post("/keywords/clear", response_class=HTMLResponse)
async def clear_keyword_pool(request: Request):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    count = await db.clear_keyword_pool(store_id)
    logger.info("Keyword pool cleared for store %s (%d rows)", store_id, count)
    return RedirectResponse("/setup?saved=keywords-cleared&tab=keywords", status_code=303)


# ---------------------------------------------------------------------------
# Store: blog title pool management
# ---------------------------------------------------------------------------

@router.post("/titles/save", response_class=HTMLResponse)
async def save_title_settings(
    request: Request,
    title_gen_model_id: Annotated[str, Form()] = "",
    title_gen_prompt_id: Annotated[str, Form()] = "",
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    await db.set_store_settings(store_id, {
        "title_gen_model_id": title_gen_model_id.strip(),
        "title_gen_prompt_id": title_gen_prompt_id.strip(),
    })
    return RedirectResponse("/setup?saved=title-settings&tab=keywords", status_code=303)


@router.post("/titles/generate", response_class=JSONResponse)
async def generate_titles_now(request: Request):
    """AJAX — trigger an immediate title batch generation and return JSON result."""
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return JSONResponse({"ok": False, "error": "Not authorised."}, status_code=403)

    result = await fetch_blog_titles(store_id)
    return JSONResponse({"ok": result["error"] is None, **result})


@router.post("/titles/seed", response_class=HTMLResponse)
async def seed_title_prompt(request: Request):
    """Insert the default Title Generator prompt into the store's prompts table."""
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)

    _default_text = (
        "Generate unique 25 long-tail blog keyword ideas for a Shopify health & wellness brand "
        "(Bio Luxe Lab). At-home solutions for stress, sleep, recovery, and daily wellbeing.\n\n"
        "Niches:\nPremium Home Wellness, Biohacking & Self-Optimisation\n"
        "Biohacking performance & anti-ageing\nMindfulness & Mental Wellbeing\n"
        "Stress Relief & Relaxation\nSleep Optimisation\nHome Fitness & Mobility\n"
        "Pain Relief & Recovery\n\nRequirements:\n"
        "Keywords must be 4-8 words long\n"
        "Focus on beginner-friendly, at-home solutions\n"
        "Include problem-solving intent (e.g. pain, stress, sleep issues)\n"
        "Use phrases like: \"how to\", \"best\", \"routine\", \"at home\", \"for beginners\"\n"
        "Avoid generic or high-competition terms\n\n"
        "Output format:\nKeyword | Search Intent (1 line) | Blog Title | meta-description (160 chars)\n\n"
        "Make them feel current, practical, and useful for SEO blog content.\n"
        "Prioritise keywords that reflect current trends in 2026 and real user problems "
        "people are actively trying to solve.\nOutput as JSON only"
    )

    pid = "title_generator"
    prompts = await db.get_prompts(store_id)
    existing = next((p for p in prompts if p["id"] == pid), None)
    if not existing:
        max_order = max((p["sort_order"] for p in prompts), default=-1)
        await db.upsert_prompt({
            "id": pid,
            "store_id": store_id,
            "name": "Title Generator",
            "text": _default_text,
            "sort_order": max_order + 1,
        })
        logger.info("Seeded 'Title Generator' prompt for store %s", store_id)

    await db.set_store_settings(store_id, {"title_gen_prompt_id": pid})
    return RedirectResponse("/setup?saved=title-prompt-seeded&tab=keywords", status_code=303)


@router.post("/titles/delete", response_class=HTMLResponse)
async def delete_title(
    request: Request,
    title_id: Annotated[int, Form()],
):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    await db.delete_title(title_id)
    return RedirectResponse("/setup?tab=keywords", status_code=303)


@router.post("/titles/clear", response_class=HTMLResponse)
async def clear_title_pool(request: Request):
    store_id = request.session.get("store_id", "")
    if _is_admin(request) or not store_id:
        return RedirectResponse("/setup", status_code=303)
    count = await db.clear_title_pool(store_id)
    logger.info("Title pool cleared for store %s (%d rows)", store_id, count)
    return RedirectResponse("/setup?saved=titles-cleared&tab=keywords", status_code=303)
