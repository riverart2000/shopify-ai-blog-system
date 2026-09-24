"""db — Database access layer. Import everything from here."""

from .base import init_db, set_db_path, get_db_path, get_admin_password_hash, set_admin_password_hash
from .stores import (
    get_stores, get_store, get_store_by_domain, upsert_store, delete_store,
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
    log_generation, get_recent_generations, get_generation_title_index, log_model_error, get_recent_errors,
    get_recent_runs_for_job,
)
from .scheduled_jobs import (
    get_scheduled_jobs, get_all_active_jobs, get_due_jobs,
    upsert_job, delete_job, update_job_run_times,
)
from .social_posts import log_social_post, get_recent_social_posts, update_social_post_job_status
from .keyword_pool import (
    add_keywords, get_keyword_pool, count_keyword_pool,
    peek_keyword, pop_keyword, delete_keyword, clear_keyword_pool,
)
from .title_pool import (
    add_titles, get_title_pool, count_title_pool,
    pop_title, delete_title, clear_title_pool, mark_title_published, reserve_title,
)
from .intelligence import (
    create_intelligence_run, complete_intelligence_run, fail_intelligence_run,
    get_latest_intelligence_run, get_intelligence_runs, get_run_recommendations,
    dismiss_recommendation, get_stores_due_for_intelligence,
)
from .wellness_quiz import (
    replace_wellness_quiz_products, get_wellness_quiz_products,
    record_wellness_quiz_event, get_wellness_quiz_summary,
)
from .reviews import (
    create_review, get_review, rate_limit_count, duplicate_count,
    external_review_duplicate_count, list_reviews,
    get_review_summary, get_admin_summary, moderate_review, delete_review,
    export_reviews_csv,
)
from .seo_growth import (
    create_seo_growth_run, update_seo_growth_run, complete_seo_growth_run,
    fail_seo_growth_run, get_latest_seo_growth_run, get_active_seo_growth_run,
    get_seo_growth_runs, get_seo_growth_opportunities, count_seo_growth_opportunities,
    set_seo_opportunity_status,
    upsert_backlink_prospect, list_backlink_prospects, delete_backlink_prospect,
    get_stores_due_for_seo_growth, fail_interrupted_seo_growth_runs,
)
from .seo_repairs import (
    create_seo_repair_job, get_latest_seo_repair_job, get_seo_repair_job,
    update_seo_repair_job, begin_seo_repair_phase,
    replace_seo_repair_items, list_seo_repair_items,
    set_seo_repair_item_result, set_seo_repair_item_applied, fail_seo_repair_job,
    fail_interrupted_seo_repair_jobs,
)

__all__ = [
    # base
    "init_db", "set_db_path", "get_db_path",
    "get_admin_password_hash", "set_admin_password_hash",
    # stores
    "get_stores", "get_store", "get_store_by_domain", "upsert_store", "delete_store",
    "get_store_password_hash", "set_store_password_hash",
    "get_store_setting", "set_store_settings", "get_all_store_settings",
    "get_cached_token", "save_token",
    # models
    "get_models", "get_active_text_models", "get_active_image_models",
    "get_model", "upsert_model", "delete_model", "set_model_active",
    # prompts
    "get_prompts", "upsert_prompt", "delete_prompt",
    # generations
    "log_generation", "get_recent_generations", "get_generation_title_index", "log_model_error", "get_recent_errors",
    "get_recent_runs_for_job",
    # scheduled jobs
    "get_scheduled_jobs", "get_all_active_jobs", "get_due_jobs",
    "upsert_job", "delete_job", "update_job_run_times",
    # social posts
    "log_social_post", "get_recent_social_posts", "update_social_post_job_status",
    # keyword pool
    "add_keywords", "get_keyword_pool", "count_keyword_pool",
    "peek_keyword", "pop_keyword", "delete_keyword", "clear_keyword_pool",
    # title pool
    "add_titles", "get_title_pool", "count_title_pool",
    "pop_title", "delete_title", "clear_title_pool", "mark_title_published", "reserve_title",
    # customer intelligence
    "create_intelligence_run", "complete_intelligence_run", "fail_intelligence_run",
    "get_latest_intelligence_run", "get_intelligence_runs", "get_run_recommendations",
    "dismiss_recommendation", "get_stores_due_for_intelligence",
    # wellness quiz
    "replace_wellness_quiz_products", "get_wellness_quiz_products",
    "record_wellness_quiz_event", "get_wellness_quiz_summary",
    # reviews
    "create_review", "get_review", "rate_limit_count", "duplicate_count",
    "external_review_duplicate_count",
    "list_reviews", "get_review_summary", "get_admin_summary",
    "moderate_review", "delete_review", "export_reviews_csv",
    # SEO Growth
    "create_seo_growth_run", "update_seo_growth_run", "complete_seo_growth_run",
    "fail_seo_growth_run", "get_latest_seo_growth_run", "get_active_seo_growth_run",
    "get_seo_growth_runs", "get_seo_growth_opportunities", "count_seo_growth_opportunities",
    "set_seo_opportunity_status",
    "upsert_backlink_prospect", "list_backlink_prospects", "delete_backlink_prospect",
    "get_stores_due_for_seo_growth", "fail_interrupted_seo_growth_runs",
    # SEO repairs
    "create_seo_repair_job", "get_latest_seo_repair_job", "get_seo_repair_job",
    "update_seo_repair_job", "begin_seo_repair_phase",
    "replace_seo_repair_items", "list_seo_repair_items",
    "set_seo_repair_item_result", "set_seo_repair_item_applied", "fail_seo_repair_job",
    "fail_interrupted_seo_repair_jobs",
]
