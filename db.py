import mysql.connector

def get_connection():
    return mysql.connector.connect(
        host="sql103.infinityfree.com",
        user="if0_41000592",
        password="tgda0G3JgXG",
        database="if0_41000592_inventario"
    )

