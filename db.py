import mysql.connector

def get_connection():
    return mysql.connector.connect(
        host="byrui10afnftlqj38wlc-mysql.services.clever-cloud.com",
        user="uro8vvynewtyknux",
        password="CmNVHboKXsz0YzcrNFFh",
        database="byrui10afnftlqj38wlc"
    )


