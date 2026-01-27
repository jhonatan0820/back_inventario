import mysql.connector
import os
from urllib.parse import urlparse

def get_connection():
    database_url = os.environ.get("back-inventario")

    if not database_url:
        raise Exception("DATABASE_URL no está definida")

    parsed = urlparse(database_url)

    return mysql.connector.connect(
        host=parsed.hostname,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/")
    )




