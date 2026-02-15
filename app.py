import mysql.connector
import uuid
import os
from urllib.parse import urlparse
from flask import Flask, jsonify, request, session
from flask_cors import CORS
from flask_mail import Mail, Message
from flask_bcrypt import Bcrypt
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix



app = Flask(__name__)

# ============================================
# CONFIGURACIÓN CORS MEJORADA PARA SAFARI
# ============================================
CORS(
    app,
    supports_credentials=True,
    resources={
        r"/*": {
            "origins": [
                "https://dotacioneszambrano.com",
                "https://frontinventario-production.up.railway.app",
                "http://127.0.0.1:5500",
                "http://localhost:5500"
            ],
            "allow_headers": ["Content-Type", "Authorization"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
        }
    }
)


# ============================================
# CONFIGURACIÓN DE SESIÓN OPTIMIZADA
# ============================================
app.secret_key = os.environ.get("SECRET_KEY", "DotacionesZambrano")

app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_PATH="/",
    SESSION_REFRESH_EACH_REQUEST=True
)


app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)
# ============================================
# CONFIGURACIÓN DE MAIL
# ============================================
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = os.environ.get("MAIL_USER")
app.config['MAIL_PASSWORD'] = os.environ.get("MAIL_PASS")
app.config['MAIL_DEFAULT_SENDER'] = ('Inventario', 'jhonizam2023@gmail.com')

mail = Mail(app)
bcrypt = Bcrypt(app)
serializer = URLSafeTimedSerializer(app.secret_key)

# ============================================
# RESEND API
# ============================================
RESEND_API_KEY = os.getenv("RESEND_API_KEY")


def get_connection():
    """Conexión a base de datos con variable corregida"""
    database_url = os.environ.get("MYSQL_URL")

    if not database_url:
        raise Exception("back-inventario no está definida en las variables de entorno")

    parsed = urlparse(database_url)

    return mysql.connector.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/")
    )

# ============================================
# UTILIDADES
# ============================================
import requests

def enviar_correo(email, token):
    url = "https://api.resend.com/emails"

    payload = {
        "from": "Inventario <onboarding@resend.dev>",
        "to": [email],  # ← CORREGIDO: debe ir el email del usuario
        "subject": "Recuperar contraseña Inventario Dotaciones Zambrano",
        "html": f"""
        <h2>Recuperación de contraseña</h2>
        <p>Haz clic en el siguiente enlace para cambiar su contraseña:</p>
        <a href="https://dotacioneszambrano.com/reset.html?token={token}">
            Recuperar contraseña
        </a>
        <p>Este enlace expira en 5 minutos.</p>
        """
    }

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers, timeout=5)

    if response.status_code not in (200, 201):
        raise Exception(f"Error enviando correo: {response.text}")


# ============================================
# RUTAS - AUTENTICACIÓN
# ============================================

@app.route("/Login", methods=["POST"])
def login():

    data = request.json
    usuario = data.get("usuario")
    password = data.get("password")

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT idUsuario, usuario, password
        FROM usuarios
        WHERE usuario = %s AND id_estado = 1
    """, (usuario,))

    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user:
        return jsonify({"ok": False, "error": "El usuario no está registrado"}), 401

    if not bcrypt.check_password_hash(user["password"], password):
        return jsonify({"ok": False, "error": "Contraseña incorrecta"}), 401

    # Guardar sesión
    session.permanent = True
    session["idUsuario"] = user["idUsuario"]
    session["usuario"] = user["usuario"]
    
    return jsonify({
        "ok": True,
        "usuario": user["usuario"]
    })


@app.route("/CheckSession", methods=["GET"])
def check_session():
    
    if "idUsuario" not in session:
        return jsonify({"ok": False}), 401

    return jsonify({"ok": True, "usuario": session.get("usuario")}), 200


@app.route("/Logout", methods=["POST"])
def logout():
    
    session.clear()
    return jsonify({"ok": True})


@app.route("/RecuperarPassword", methods=["POST"])
def recuperar_password():

    conn = cursor = None

    try:
        data = request.get_json()
        email = data.get("email", "").strip()

        if not email:
            return jsonify({"ok": False, "error": "Email requerido"}), 400

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT idUsuario FROM usuarios WHERE email=%s AND id_estado=1",
            (email,)
        )
        user = cursor.fetchone()

        # No revelar si existe
        if not user:
            return jsonify({"ok": True})

        token = str(uuid.uuid4())
        expiracion = datetime.utcnow() + timedelta(minutes=5)

        cursor.execute("""
            INSERT INTO password_resets (idUsuario, token, expira, id_estado)
            VALUES (%s, %s, %s, 4)
            ON DUPLICATE KEY UPDATE
                token = VALUES(token),
                expira = VALUES(expira),
                id_estado = 4
        """, (user["idUsuario"], token, expiracion))

        conn.commit()

        try:
            enviar_correo(email, token)
        except Exception as mail_error:
            print("ERROR enviando correo:", mail_error)

        return jsonify({"ok": True})

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"ok": False, "error": "Error interno"}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/activador", methods=["GET"])
def activador():
    return jsonify({"ok": True, "status": "running"}), 200


@app.route("/ResetPassword", methods=["POST"])
def reset_password():

    conn = cursor = None

    try:
        data = request.get_json()
        token = data.get("token")
        password = data.get("password")

        if not token or not password:
            return jsonify({"ok": False, "error": "Datos incompletos"}), 400

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT * FROM password_resets
            WHERE token=%s AND id_estado=4 AND expira > NOW()
        """, (token,))
        reset = cursor.fetchone()

        if not reset:
            return jsonify({"ok": False, "error": "Token inválido o expirado"}), 400

        hashed = bcrypt.generate_password_hash(password).decode("utf-8")

        cursor.execute(
            "UPDATE usuarios SET password=%s WHERE idUsuario=%s",
            (hashed, reset["idUsuario"])
        )

        cursor.execute(
            "UPDATE password_resets SET id_estado=3 WHERE idPasswordResets=%s",
            (reset["idPasswordResets"],)
        )

        conn.commit()

        return jsonify({"ok": True})

    except Exception as e:
        if conn: 
            conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        if cursor: 
            cursor.close()
        if conn: 
            conn.close()


# ============================================
# RUTAS - PRODUCTOS
# ============================================


@app.route("/GetProductos", methods=["GET"])
def get_productos():
        
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            p.id_producto AS id_producto,
            p.nombre AS nomproducto,
            cat.nombre AS categoria,
            v.id_variante AS id_variante,
            m.nombre AS marca,
            e.nombre AS estilo,
            c.nombre AS color,
            t.valor AS talla,
            v.precio AS precio,
            v.stock AS stock
        FROM variantes v
        JOIN productos p ON v.id_producto = p.id_producto
        LEFT JOIN marcas m ON p.id_marca = m.id_marca
        JOIN categorias cat ON p.id_categoria = cat.id_categoria
        LEFT JOIN estilos e ON p.id_estilo = e.id_estilo
        LEFT JOIN colores c ON v.id_color = c.id_color
        LEFT JOIN tallas t ON v.id_talla = t.id_talla
        WHERE p.id_estado = 1 and v.id_estado = 1
    """)

    data = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(data)



@app.route("/AddProducto", methods=["POST"])
def add_producto():
        
    conn = None
    cursor = None
    try:
        data = request.get_json(force=True)
        
        id_genero = data.get("id_genero")
        id_categoria = data.get("id_categoria")
        nombre = data.get("nombre", "").strip()
        marca = data.get("marca")
        estilo = data.get("estilo")
        id_color = int(data.get("id_color", 0))
        variantes = data.get("variantes")

        # Validaciones
        if not id_genero:
            return jsonify({"ok": False, "error": "Género requerido"}), 400
        
        if not id_categoria:
            return jsonify({"ok": False, "error": "Categoría requerida"}), 400

        if not nombre:
            return jsonify({"ok": False, "error": "Nombre requerido"}), 400

        if not variantes or len(variantes) == 0:
            return jsonify({"ok": False, "error": "Debe tener variantes"}), 400

        if marca:
            marca = marca.strip()
            if marca == "":
                marca = None

        if estilo:
            estilo = estilo.strip()
            if estilo == "":
                estilo = None

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # MARCA
        id_marca = None
        if marca:
            cursor.execute(
                "SELECT id_marca FROM marcas WHERE nombre = %s and id_estado = 1",
                (marca,)
            )
            row = cursor.fetchone()

            if row:
                id_marca = row["id_marca"]
            else:
                cursor.execute(
                    "INSERT INTO marcas (nombre, id_categoria, id_estado) VALUES (%s, %s, %s)",
                    (marca, id_categoria, 1)
                )
                id_marca = cursor.lastrowid

        # ESTILO
        id_estilo = None
        if estilo:
            cursor.execute(
                "SELECT id_estilo FROM estilos WHERE nombre = %s",
                (estilo,)
            )
            row = cursor.fetchone()

            if row:
                id_estilo = row["id_estilo"]
            else:
                cursor.execute(
                    "INSERT INTO estilos (nombre, id_marca) VALUES (%s, %s)",
                    (estilo, id_marca)
                )
                id_estilo = cursor.lastrowid

        # PRODUCTO
        cursor.execute(
            """
            INSERT INTO productos
            (nombre, id_marca, id_estilo, id_categoria, id_genero, id_estado)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (nombre, id_marca, id_estilo, id_categoria, id_genero, 1)
        )
        id_producto = cursor.lastrowid

        # VARIANTES
        for v in variantes:
            talla = v["talla"]
            precio = v["precio"]
            stock = v["stock"]

            cursor.execute(
                """
                SELECT id_talla
                FROM tallas
                WHERE valor = %s AND id_categoria = %s AND id_genero = %s AND id_estado = 1
                """,
                (talla, id_categoria, id_genero)
            )
            row = cursor.fetchone()

            if row:
                id_talla = row["id_talla"]
            else:
                cursor.execute(
                    """
                    INSERT INTO tallas (valor, id_categoria, id_genero, id_estado)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (talla, id_categoria, id_genero,1)
                )
                id_talla = cursor.lastrowid

            cursor.execute("""
                INSERT INTO variantes
                (id_producto, id_color, id_talla, precio, stock, id_estado)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (id_producto, id_color, id_talla, precio, stock,1)
                )
        conn.commit()
        return jsonify({"ok": True})

    except Exception as e:
        if conn:
            conn.rollback()
        print("ERROR AGREGANDO PRODUCTO:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/DeleteProductos", methods=["POST"])
def delete_productos():

    try:
        data = request.get_json(force=True)
        ids = data.get("ids", [])

        if not isinstance(ids, list) or len(ids) == 0:
            return jsonify({"error": "IDs inválidos"}), 400

        conn = get_connection()
        cursor = conn.cursor()

        placeholders = ",".join(["%s"] * len(ids))
        sql = f"""
            UPDATE variantes
            SET id_estado = 2
            WHERE id_variante IN ({placeholders})
        """

        cursor.execute(sql, ids)
        conn.commit()

        eliminados = cursor.rowcount

        cursor.close()
        conn.close()

        return jsonify({
            "ok": True,
            "eliminados": eliminados
        })

    except Exception as e:
        print("ERROR BORRANDO PRODUCTO:", e)
        return jsonify({"error": str(e)}), 500


@app.route("/ActualizarStock", methods=["POST"])
def actualizar_stock():
    data = request.get_json()

    id_variante  = data.get("id_variante")
    cantidad     = data.get("cantidad")
    precio_venta = data.get("precio_venta")

    if None in (id_variante, cantidad, precio_venta):
        return jsonify({"ok": False, "error": "Datos incompletos"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT stock FROM variantes WHERE id_variante = %s AND id_estado = 1",
            (id_variante,)
        )
        row = cursor.fetchone()

        if not row:
            return jsonify({"ok": False, "error": "Variante no encontrada"}), 404

        stock_anterior = row["stock"]

        if cantidad <= 0 or cantidad > stock_anterior:
            return jsonify({"ok": False, "error": "Cantidad inválida"}), 400

        stock_nuevo = stock_anterior - cantidad
        total_venta = cantidad * precio_venta

        cursor.execute(
            "UPDATE variantes SET stock = %s WHERE id_variante = %s",
            (stock_nuevo, id_variante)
        )

        cursor.execute("""
            INSERT INTO movimientos_inventario
            (id_variante, tipo, cantidad, stock_anterior, stock_nuevo,
             fecha, precio_venta, fecha_venta, total_venta)
            VALUES (%s, 'SALIDA', %s, %s, %s, NOW(), %s, NOW(), %s)
        """, (
            id_variante,
            cantidad,
            stock_anterior,
            stock_nuevo,
            precio_venta,
            total_venta
        ))

        conn.commit()
        return jsonify({"ok": True})

    except Exception as e:
        conn.rollback()
        print("ERROR ACTUALIZAR STOCK:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

@app.route("/EntradaStock", methods=["POST"])
def entrada_stock():
    data = request.get_json()

    id_variante = data.get("id_variante")
    cantidad    = data.get("cantidad")

    if None in (id_variante, cantidad):
        return jsonify({"ok": False, "error": "Datos incompletos"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT stock FROM variantes WHERE id_variante = %s",
            (id_variante,)
        )
        row = cursor.fetchone()

        stock_anterior = row["stock"]
        stock_nuevo = stock_anterior + cantidad

        cursor.execute(
            "UPDATE variantes SET stock = %s WHERE id_variante = %s",
            (stock_nuevo, id_variante)
        )

        cursor.execute("""
            INSERT INTO movimientos_inventario
            (id_variante, tipo, cantidad, stock_anterior, stock_nuevo, fecha)
            VALUES (%s, 'ENTRADA', %s, %s, %s, NOW())
        """, (
            id_variante,
            cantidad,
            stock_anterior,
            stock_nuevo
        ))

        conn.commit()
        return jsonify({"ok": True})

    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500




# ============================================
# RUTAS - CATÁLOGOS
# ============================================

@app.route("/GetCategorias", methods=["GET"])
def get_categorias():
        
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT id_categoria, nombre
        FROM categorias
        WHERE id_estado = 1
        ORDER BY nombre
    """)

    data = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/AddCategoria", methods=["POST"])
def add_categoria():
        
    data = request.get_json()
    nombre = data.get("nombre", "").strip()

    if not nombre:
        return jsonify({"ok": False, "error": "Nombre requerido"}), 400

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO categorias (nombre, id_estado) VALUES (%s, %s)",
            (nombre, 1)
        )
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route("/GetGeneros", methods=["GET"])
def get_generos():
        
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT id_genero, nombre
        FROM generos
        WHERE id_estado = 1
    """)

    data = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/GetColores", methods=["GET"])
def get_colores():
        
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT id_color, nombre FROM colores WHERE id_estado = 1 ORDER BY nombre")
    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/AddColor", methods=["POST",])
def add_color():

    data = request.get_json()
    nombre = data["nombre"]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO colores (nombre, id_estado) VALUES (%s, %s)",
        (nombre, 1)
    )

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"ok": True})


@app.route("/GetTallas", methods=["GET"])
def get_tallas():
        
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT id_talla, valor, id_genero FROM tallas ORDER BY valor")
    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/GetTallasPorCategoriaGenero", methods=["GET"])
def get_tallas_por_categoria_genero():
        
    id_categoria = request.args.get("id_categoria")
    id_genero = request.args.get("id_genero")

    if not id_categoria or not id_genero:
        return jsonify([])

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT DISTINCT valor, id_talla 
        FROM tallas
        WHERE id_categoria = %s
          AND id_genero = %s
          AND id_estado = 1
        ORDER BY id_talla
    """, (id_categoria, id_genero))

    data = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/GetTallasPorCategoria", methods=["GET"])
def get_tallas_por_categoria():
        
    id_categoria = request.args.get("id_categoria")

    if not id_categoria:
        return jsonify([])

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT DISTINCT valor AS talla
        FROM tallas
        WHERE id_categoria = %s
          AND id_estado = 1
        ORDER BY valor
    """, (id_categoria,))

    data = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/GetEstilosUnicos", methods=["GET"])
def get_estilos_unicos():
        
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT DISTINCT
            TRIM(LOWER(nombre)) AS nombre
        FROM estilos
        WHERE id_estado = 1
        ORDER BY nombre
    """)

    data = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/GetTallasValidas", methods=["GET"])
def get_tallas_validas():
        
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    id_categoria = request.args.get("id_categoria", type=int)

    query = """
        SELECT DISTINCT t.valor AS talla
        FROM variantes v
        INNER JOIN tallas t ON v.id_talla = t.id_talla
        WHERE v.id_estado = 1 and t.id_estado = 1
    """

    params = []
    if id_categoria:
        query += " AND t.id_categoria = %s"
        params.append(id_categoria)

    query += " ORDER BY t.valor"

    cursor.execute(query, params)

    data = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/GetNombresProductos", methods=["GET"])
def get_nombres_productos():
        
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            TRIM(LOWER(nombre)) AS nombre
        FROM productos
        WHERE id_estado = 1
        GROUP BY TRIM(LOWER(nombre))
        ORDER BY nombre
    """)

    data = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/InformationGeneral", methods=["GET"])
def reporte_general():
    
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:        
        def clean(val):
            return None if val in [None, "", "null", "undefined"] else val
        
        args = (
            clean(request.args.get('categoria')),
            clean(request.args.get('genero')),
            clean(request.args.get('producto')),
            clean(request.args.get('talla')),
            clean(request.args.get('estilo'))
        )
        
        cursor.callproc("InformationGeneral", args)
        
        rows = []
        columns = []

        for result in cursor.stored_results():
            columns = result.column_names
            rows = result.fetchall()

        return jsonify({
            "columns": columns,
            "rows": rows
        })

    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}") 
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()


# ============================================
# HEALTH CHECK
# ============================================
@app.route("/")
def health():
    return jsonify({"status": "ok", "message": "API funcionando correctamente"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)












