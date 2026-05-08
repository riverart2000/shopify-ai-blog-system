"""db — Database access layer. Import everything from here."""

from .base import init_db, set_db_path, get_db_path, get_admin_password_hash, set_admin_password_hash
from .stores import (
    get_stores, get_store, upsert_store, delete_store,
    get_store_password_hash, set_store_password_hash,
    get_store_setting, set_store_settings, get_all_store_settings,
    get_cached_token, save_token,
)
from .models import (
    get_models, get_active_text_models, get_active_image_models,
    get_model, upsert_model, delete_model, set_model_active,
)
from .prompts import get_prompts, upsert_prompt, delete_prompt
from .generations import (
    log_generation, get_recent_generations, log_model_error, get_recent_errors,
    get_recent_runs_for_job,
)
from .scheduled_jobs import (
    get_scheduled_jobs, get_all_active_jobs, get_due_jobs,
    upsert_job, delete_job, update_job_run_times,
)
from .keyword_pool import (
    add_keywords, get_keyword_pool, count_keyword_pool,
    peek_keyword, pop_keyword, delete_keyword, clear_keyword_pool,
)
from .title_pool import (
    add_titles, get_title_pool, count_title_pool,
    pop_title, delete_title, clear_title_pool, mark_title_published,
)

__all__ = [
    # base
    "init_db", "set_db_path", "get_db_path",
    "get_admin_password_hash", "set_admin_password_hash",
    # stores
    "get_stores", "get_store", "upsert_store", "delete_store",
    "get_store_password_hash", "set_store_password_hash",
    "get_store_setting", "set_store_settings", "get_all_store_settings",
    "get_cached_token", "save_token",
    # models
    "get_models", "get_active_text_models", "get_active_image_models",
    "get_model", "upsert_model", "delete_model", "set_model_active",
    # prompts
    "get_prompts", "upsert_prompt", "delete_prompt",
    # generations
    "log_generation", "get_recent_generations", "log_model_error", "get_recent_errors",
    "get_recent_runs_for_job",
    # scheduled jobs
    "get_scheduled_jobs", "get_all_active_jobs", "get_due_jobs",
    "upsert_job", "delete_job", "update_job_run_times",
    # keyword pool
    "add_keywords", "get_keyword_pool", "count_keyword_pool",
    "peek_keyword", "pop_keyword", "delete_keyword", "clear_keyword_pool",
    # title pool
    "add_titles", "get_title_pool", "count_title_pool",
    "pop_title", "delete_title", "clear_title_pool", "mark_title_published",
]
