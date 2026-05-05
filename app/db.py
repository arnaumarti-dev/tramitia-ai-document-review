import os
import psycopg2


def get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", 5432),
        dbname=os.getenv("POSTGRES_DB", "tramitia"),
        user=os.getenv("POSTGRES_USER", "tramitia"),
        password=os.getenv("POSTGRES_PASSWORD", "tramitia"),
    )


# (opcional) por si en algún sitio usas el nombre viejo:
def get_connection():
    return get_conn()
