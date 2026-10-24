"""Social Engine data access (SQLite, same database file as the rest of the app).

Import submodules directly, e.g. `from db.social_engine import posts, jobs`.
Schema lives in `schema.py` and is applied by `db.init_db()`.
"""
from . import config_store, jobs, observability, posts, products, schema

__all__ = ["config_store", "jobs", "observability", "posts", "products", "schema"]
