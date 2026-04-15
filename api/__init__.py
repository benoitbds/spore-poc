"""SPORE monetisation API.

FastAPI module exposing magic-link auth, Stripe checkout, brief delivery
and custom collision requests. Shares storage/database.py with the rest
of the project (single SQLite file at data/spore.db).
"""
