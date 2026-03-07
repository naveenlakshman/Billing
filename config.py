import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "billing.db")

SECRET_KEY = "change-this-to-a-secure-secret-key"