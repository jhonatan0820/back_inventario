import time
import mysql.connector
import uuid
import os
import base64
from io import BytesIO
from urllib.parse import urlparse
from flask import Flask, jsonify, request, session, render_template_string, make_response
from flask_cors import CORS
from flask_mail import Mail, Message
from flask_bcrypt import Bcrypt
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from collections import defaultdict
import requests

try:
    from xhtml2pdf import pisa
except ImportError:
    pisa = None

app = Flask(__name__)

# ============================================
# CONFIGURACIÓN CORS MEJORADA PARA SAFARI
# ============================================
CORS(
    app,
    resources={
        r"/*": {
            "origins": [
                r"https://([a-z0-9-]+\.)?dotacioneszambrano\.com",
                "https://front-inventario.pages.dev",
                "http://127.0.0.1:5500",
                "http://localhost:5500"
            ],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
            "supports_credentials": True
        }
    }
)

def get_allowed_origin(origin):
    if not origin:
        return None

    allowed_exact = {
        "https://dotacioneszambrano.com",
        "https://www.dotacioneszambrano.com",
        "https://pruebas.dotacioneszambrano.com",
        "https://front-inventario.pages.dev",
        "http://127.0.0.1:5500",
        "http://localhost:5500"
    }

    if origin in allowed_exact:
        return origin

    try:
        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and (
            host == "dotacioneszambrano.com" or host.endswith(".dotacioneszambrano.com")
        ):
            return origin
    except Exception:
        return None

    return None


@app.before_request
def handle_cors_preflight():
    if request.method != "OPTIONS":
        return None

    origin = get_allowed_origin(request.headers.get("Origin"))
    response = make_response("", 204)

    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        request_headers = request.headers.get("Access-Control-Request-Headers", "").strip()
        response.headers["Access-Control-Allow-Headers"] = request_headers or "Content-Type, Authorization"
        response.headers["Vary"] = "Origin"

    return response


@app.after_request
def apply_cors_headers(response):
    origin = get_allowed_origin(request.headers.get("Origin"))
    if not origin:
        return response

    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"

    if "Access-Control-Allow-Headers" not in response.headers:
        request_headers = request.headers.get("Access-Control-Request-Headers", "").strip()
        response.headers["Access-Control-Allow-Headers"] = request_headers or "Content-Type, Authorization"

    response.headers["Vary"] = "Origin"
    return response


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
    try:
        database_url = os.environ.get("MYSQL_URL")
        if not database_url:
            print("MYSQL_URL no definida")
            return None

        parsed = urlparse(database_url)

        conn = mysql.connector.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
            ssl_disabled=False,
            connection_timeout=5
        )

        return conn

    except Exception as e:
        print("ERROR CONECTANDO A MYSQL:", e)
        return None

@app.route("/ping", methods=["GET", "HEAD"])
def ping():
    return "OK", 200

@app.route("/activador")
def activador():
    conn = None
    cursor = None
    try:
        conn = get_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as e:
        print("DB dormida o error:", e)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return jsonify({"ok": True})



# ============================================
# UTILIDADES
# ============================================


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
intentos_login = defaultdict(list)
VENTANA_SEGUNDOS = 300  
MAX_INTENTOS = 3

def verificar_password(password_plano, password_bd):
    return bcrypt.check_password_hash(password_bd, password_plano)


@app.route('/Login', methods=['POST'])
def login():
    conn = None
    cursor = None

    try:
        data = request.get_json()

        usuario = data.get('usuario', '').strip()
        password = data.get('password', '').strip()

        if not usuario or not password:
            return jsonify({'ok': False, 'error': 'Completa todos los campos'}), 400

        conn = get_connection()
        if not conn:
            return jsonify({'ok': False, 'error': 'DB no disponible'}), 500

        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT idUsuario, password FROM usuarios WHERE usuario = %s",
            (usuario,)
        )
        user = cursor.fetchone()

        if not user or not verificar_password(password, user['password']):
            return jsonify({'ok': False, 'error': 'Credenciales inválidas'}), 401

        session['idUsuario'] = user['idUsuario']
        session['usuario'] = usuario

        return jsonify({'ok': True}), 200

    except Exception as e:
        print("ERROR LOGIN:", e)
        return jsonify({'ok': False, 'error': str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


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
                    "INSERT INTO estilos (nombre, id_marca, id_estado) VALUES (%s, %s, %s)",
                    (estilo, id_marca, 1)
                )
                id_estilo = cursor.lastrowid

        # PRODUCTO (reutilizar si ya existe)
        cursor.execute(
            """
            SELECT id_producto, id_estado
            FROM productos
            WHERE nombre = %s
              AND id_categoria = %s
              AND id_genero = %s
              AND (id_marca <=> %s)
              AND (id_estilo <=> %s)
            LIMIT 1
            """,
            (nombre, id_categoria, id_genero, id_marca, id_estilo)
        )
        existing_product = cursor.fetchone()

        if existing_product:
            id_producto = existing_product["id_producto"]
            if existing_product["id_estado"] != 1:
                cursor.execute(
                    "UPDATE productos SET id_estado = 1 WHERE id_producto = %s",
                    (id_producto,)
                )
        else:
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

            cursor.execute(
                """
                SELECT id_variante, id_estado
                FROM variantes
                WHERE id_producto = %s
                  AND id_color = %s
                  AND id_talla = %s
                LIMIT 1
                """,
                (id_producto, id_color, id_talla)
            )
            existing_variante = cursor.fetchone()

            if existing_variante:
                cursor.execute(
                    """
                    UPDATE variantes
                    SET id_estado = 1, precio = %s, stock = %s
                    WHERE id_variante = %s
                    """,
                    (precio, stock, existing_variante["id_variante"])
                )
            else:
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
        cursor = conn.cursor(dictionary=True)

        placeholders = ",".join(["%s"] * len(ids))
        cursor.execute(
            f"""
            SELECT DISTINCT p.id_producto
            FROM variantes v
            JOIN productos p ON v.id_producto = p.id_producto
            WHERE v.id_variante IN ({placeholders})
            """,
            ids
        )
        producto_ids = [row["id_producto"] for row in cursor.fetchall()]

        sql = f"""
            UPDATE variantes
            SET id_estado = 2
            WHERE id_variante IN ({placeholders})
        """

        cursor.execute(sql, ids)
        conn.commit()

        eliminados = cursor.rowcount

        # Desactivar productos que ya no tengan variantes activas
        if producto_ids:
            placeholders_prod = ",".join(["%s"] * len(producto_ids))
            cursor.execute(
                f"""
                SELECT p.id_producto
                FROM productos p
                LEFT JOIN variantes v
                  ON v.id_producto = p.id_producto
                 AND v.id_estado = 1
                WHERE p.id_producto IN ({placeholders_prod})
                GROUP BY p.id_producto
                HAVING COUNT(v.id_variante) = 0
                """,
                producto_ids
            )
            productos_sin_variantes = [row["id_producto"] for row in cursor.fetchall()]

            if productos_sin_variantes:
                placeholders_disable = ",".join(["%s"] * len(productos_sin_variantes))
                cursor.execute(
                    f"""
                    UPDATE productos
                    SET id_estado = 2
                    WHERE id_producto IN ({placeholders_disable})
                    """,
                    productos_sin_variantes
                )
                conn.commit()

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

    id_variante = data.get("id_variante")
    cantidad = data.get("cantidad")
    precio_venta = data.get("precio_venta")

    if None in (id_variante, cantidad, precio_venta):
        return jsonify({"ok": False, "error": "Datos incompletos"}), 400

    try:
        id_variante = int(id_variante)
        cantidad = int(cantidad)
        precio_venta = float(precio_venta)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Formato inválido en id_variante, cantidad o precio_venta"}), 400

    if cantidad <= 0 or precio_venta < 0:
        return jsonify({"ok": False, "error": "Cantidad o precio de venta inválido"}), 400

    conn = get_connection()
    if not conn:
        return jsonify({"ok": False, "error": "No se pudo conectar a la base de datos"}), 500
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
             fecha, precio_venta, total_venta, precio_compra)
            VALUES (%s, 'SALIDA', %s, %s, %s, NOW(), %s, %s, %s)
        """, (
            id_variante,
            cantidad,
            stock_anterior,
            stock_nuevo,
            precio_venta,
            total_venta,
            None
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
    cantidad = data.get("cantidad")
    precio_compra = data.get("precio_compra")

    if None in (id_variante, cantidad, precio_compra):
        return jsonify({"ok": False, "error": "Datos incompletos"}), 400

    try:
        id_variante = int(id_variante)
        cantidad = int(cantidad)
        precio_compra = float(precio_compra)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Formato inválido en id_variante, cantidad o precio_compra"}), 400

    if cantidad <= 0 or precio_compra < 0:
        return jsonify({"ok": False, "error": "Cantidad o precio de compra inválido"}), 400

    conn = get_connection()
    if not conn:
        return jsonify({"ok": False, "error": "No se pudo conectar a la base de datos"}), 500
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
        stock_nuevo = stock_anterior + cantidad

        cursor.execute(
            "UPDATE variantes SET stock = %s WHERE id_variante = %s",
            (stock_nuevo, id_variante)
        )

        cursor.execute("""
            INSERT INTO movimientos_inventario
            (id_variante, tipo, cantidad, stock_anterior, stock_nuevo, fecha, precio_compra)
            VALUES (%s, 'ENTRADA', %s, %s, %s, NOW(), %s)
        """, (
            id_variante,
            cantidad,
            stock_anterior,
            stock_nuevo,
            precio_compra
        ))

        conn.commit()
        return jsonify({"ok": True})

    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


def normalizar_periodo_ventas(periodo_raw):
    periodo = str(periodo_raw or "dia").strip().lower()
    if periodo in ("dia", "semana", "mes"):
        return periodo
    return "dia"


def obtener_rango_periodo_ventas(periodo):
    ahora = datetime.now()
    inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)

    if periodo == "semana":
        inicio = (ahora - timedelta(days=ahora.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return inicio, ahora, "Semana actual"

    if periodo == "mes":
        inicio = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return inicio, ahora, "Mes actual"

    return inicio, ahora, "Hoy"


def consumir_fifo_lotes_detalle(lotes, cantidad_salida):
    restante = float(cantidad_salida)
    detalles = []

    while restante > 0 and lotes:
        lote = lotes[0]
        disponible = float(lote["restante"])
        if disponible <= 0:
            lotes.pop(0)
            continue

        tomado = min(restante, disponible)
        precio_compra = float(lote["precio_compra"])
        costo = tomado * precio_compra
        detalles.append({
            "cantidad": tomado,
            "precio_compra_unitario": precio_compra,
            "costo": costo
        })
        lote["restante"] = disponible - tomado
        restante -= tomado

        if lote["restante"] <= 0:
            lotes.pop(0)

    return detalles


@app.route("/VentasResumen", methods=["GET"])
def ventas_resumen():
    if "idUsuario" not in session:
        return jsonify({"ok": False, "error": "Sesión no válida"}), 401

    periodo = normalizar_periodo_ventas(request.args.get("periodo"))
    inicio_periodo, fin_periodo, periodo_label = obtener_rango_periodo_ventas(periodo)

    conn = get_connection()
    if not conn:
        return jsonify({"ok": False, "error": "No se pudo conectar a la base de datos"}), 500

    cursor = conn.cursor(dictionary=True)

    try:
        query = """
            SELECT
                mi.id_movimiento AS id_movimiento,
                mi.id_variante AS id_variante,
                mi.tipo AS tipo,
                mi.cantidad AS cantidad,
                mi.fecha AS fecha,
                mi.precio_venta AS precio_venta,
                mi.total_venta AS total_venta,
                mi.precio_compra AS precio_compra,
                p.id_producto AS id_producto,
                p.nombre AS producto
            FROM movimientos_inventario mi
            JOIN variantes v ON mi.id_variante = v.id_variante
            JOIN productos p ON v.id_producto = p.id_producto
            WHERE mi.tipo IN ('ENTRADA', 'SALIDA')
              AND mi.cantidad > 0
              AND mi.fecha <= %s
            ORDER BY mi.fecha ASC, mi.id_movimiento ASC
        """
        cursor.execute(query, (fin_periodo,))
        movimientos = cursor.fetchall()

        total_unidades = 0
        total_compra = 0.0
        total_venta = 0.0
        filas = []

        lotes_por_variante = defaultdict(list)
        acumulado_por_detalle = {}

        for mov in movimientos:
            tipo = (mov.get("tipo") or "").upper()
            id_variante = mov.get("id_variante")
            cantidad = int(mov.get("cantidad") or 0)
            fecha = mov.get("fecha")

            if cantidad <= 0 or not id_variante or not fecha:
                continue

            if tipo == "ENTRADA":
                precio_compra = mov.get("precio_compra")
                if precio_compra is None:
                    continue

                lotes_por_variante[id_variante].append({
                    "restante": float(cantidad),
                    "precio_compra": float(precio_compra)
                })
                continue

            if tipo != "SALIDA":
                continue

            detalles_fifo = consumir_fifo_lotes_detalle(lotes_por_variante[id_variante], cantidad)
            cantidad_cubierta = sum(float(det.get("cantidad") or 0) for det in detalles_fifo)
            faltante = max(0.0, float(cantidad) - cantidad_cubierta)
            if faltante > 0:
                detalles_fifo.append({
                    "cantidad": faltante,
                    "precio_compra_unitario": 0.0,
                    "costo": 0.0
                })

            if fecha < inicio_periodo or fecha > fin_periodo:
                continue

            precio_venta = mov.get("precio_venta")
            venta_total = mov.get("total_venta")
            venta_total_calc = float(venta_total) if venta_total is not None else float(cantidad) * float(precio_venta or 0)

            id_producto = mov.get("id_producto")
            producto = mov.get("producto") or "Sin nombre"
            fecha_venta = fecha.strftime("%Y-%m-%d") if hasattr(fecha, "strftime") else str(fecha)[:10]
            venta_unit_mov = (float(venta_total_calc) / float(cantidad)) if cantidad else 0.0

            for det in detalles_fifo:
                cantidad_det = float(det.get("cantidad") or 0)
                if cantidad_det <= 0:
                    continue

                precio_compra_unit = float(det.get("precio_compra_unitario") or 0.0)
                compra_total_det = float(det.get("costo") or 0.0)
                proporcion_venta = cantidad_det / float(cantidad) if cantidad else 0.0
                venta_total_det = float(venta_total_calc) * proporcion_venta

                key = (
                    id_producto,
                    fecha_venta,
                    round(precio_compra_unit, 2),
                    round(venta_unit_mov, 2)
                )

                if key not in acumulado_por_detalle:
                    acumulado_por_detalle[key] = {
                        "id_producto": id_producto,
                        "producto": producto,
                        "fecha_venta": fecha_venta,
                        "valor_compra_unitario": round(precio_compra_unit, 2),
                        "valor_venta_unitario": round(venta_unit_mov, 2),
                        "cantidad_vendida": 0.0,
                        "total_compra": 0.0,
                        "total_venta": 0.0
                    }

                acumulado_por_detalle[key]["cantidad_vendida"] += cantidad_det
                acumulado_por_detalle[key]["total_compra"] += compra_total_det
                acumulado_por_detalle[key]["total_venta"] += venta_total_det

        productos_unicos = set()

        for row in acumulado_por_detalle.values():
            cantidad_row = int(round(float(row["cantidad_vendida"])))
            compra_total_row = float(row["total_compra"])
            venta_total_row = float(row["total_venta"])
            ganancia_row = venta_total_row - compra_total_row

            total_unidades += cantidad_row
            total_compra += compra_total_row
            total_venta += venta_total_row
            productos_unicos.add(row.get("id_producto"))

            filas.append({
                "id_producto": row.get("id_producto"),
                "producto": row.get("producto") or "Sin nombre",
                "fecha_venta": row.get("fecha_venta"),
                "cantidad_vendida": cantidad_row,
                "valor_compra_unitario": round(float(row.get("valor_compra_unitario") or 0), 2),
                "valor_venta_unitario": round(float(row.get("valor_venta_unitario") or 0), 2),
                "total_compra": round(compra_total_row, 2),
                "total_venta": round(venta_total_row, 2),
                "ganancia": round(ganancia_row, 2)
            })

        filas.sort(
            key=lambda item: (
                item.get("fecha_venta") or "",
                (item.get("producto") or "").lower(),
                float(item.get("valor_compra_unitario") or 0)
            ),
            reverse=True
        )

        ganancia_total = total_venta - total_compra

        return jsonify({
            "ok": True,
            "periodo": periodo,
            "periodo_label": periodo_label,
            "rows": filas,
            "resumen": {
                "total_productos": len(productos_unicos),
                "total_unidades": total_unidades,
                "total_compra": round(total_compra, 2),
                "total_venta": round(total_venta, 2),
                "ganancia_total": round(ganancia_total, 2)
            }
        })

    except Exception as e:
        print("ERROR VENTAS RESUMEN:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()




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
    nombre = data.get("nombre", "").strip()

    if not nombre:
        return jsonify({"ok": False, "error": "Nombre requerido"}), 400

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO colores (nombre, id_estado) VALUES (%s, %s)",
            (nombre, 1)
        )
        conn.commit()

        return jsonify({
            "ok": True,
            "id_color": cursor.lastrowid
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


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
    try:        
        data = obtener_reporte_general_data()
        return jsonify(data)

    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}") 
        return jsonify({"error": str(e)}), 500

def clean_report_param(val):
    return None if val in [None, "", "null", "undefined"] else val


def obtener_reporte_general_data():
    conn = get_connection()
    if conn is None:
        raise RuntimeError("No se pudo conectar a la base de datos")

    cursor = conn.cursor(dictionary=True)

    try:
        args = (
            clean_report_param(request.args.get('categoria')),
            clean_report_param(request.args.get('genero')),
            clean_report_param(request.args.get('producto')),
            clean_report_param(request.args.get('talla')),
            clean_report_param(request.args.get('estilo'))
        )

        cursor.callproc("InformationGeneral", args)

        rows = []
        columns = []

        for result in cursor.stored_results():
            columns = list(result.column_names)
            rows = result.fetchall()

        return {
            "columns": columns,
            "rows": rows
        }
    finally:
        cursor.close()
        conn.close()


def parse_report_number(value):
    if value is None:
        return 0

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return 0

    cleaned = ''.join(ch for ch in text if ch.isdigit())
    return float(cleaned) if cleaned else 0


def format_number_es(value):
    return f"{int(round(value)):,}".replace(",", ".")


def format_currency_es(value):
    return f"$ {format_number_es(value)}"


def calcular_totales_reporte(rows):
    cantidad_total = 0
    valor_total = 0

    for row in rows:
        cantidad_total += parse_report_number(row.get("Cantidad"))
        valor_total += parse_report_number(row.get("Total"))

    return {
        "cantidad_total_num": int(cantidad_total),
        "cantidad_total": format_number_es(cantidad_total),
        "valor_total_num": int(valor_total),
        "valor_total": format_currency_es(valor_total)
    }


def get_report_logo_src():
    env_logo = os.environ.get("REPORT_COMPANY_LOGO", "").strip()
    if env_logo:
        return env_logo

    logo_path = os.path.join(os.path.dirname(__file__), "img", "Logo.jpg")
    if not os.path.exists(logo_path):
        return ""

    with open(logo_path, "rb") as logo_file:
        encoded = base64.b64encode(logo_file.read()).decode("ascii")

    return f"data:image/jpeg;base64,{encoded}"


def build_reporte_pdf_context():
    data = obtener_reporte_general_data()
    totals = calcular_totales_reporte(data["rows"])

    filtros = {
        "Categoria": clean_report_param(request.args.get("categoria")) or "Todos",
        "Genero": clean_report_param(request.args.get("genero")) or "Todos",
        "Producto": clean_report_param(request.args.get("producto")) or "Todos",
        "Talla": clean_report_param(request.args.get("talla")) or "Todos",
        "Estilo": clean_report_param(request.args.get("estilo")) or "Todos"
    }

    return {
        "empresa_nombre": os.environ.get("REPORT_COMPANY_NAME", "Dotaciones Zambrano"),
        "empresa_logo": get_report_logo_src(),
        "empresa_direccion": os.environ.get("REPORT_COMPANY_ADDRESS", "Calle 70 sur # 91-40"),
        "empresa_celular": os.environ.get("REPORT_COMPANY_PHONE", "3136673447"),
        "empresa_email": os.environ.get("REPORT_COMPANY_EMAIL", ""),
        "responsable": os.environ.get("REPORT_DEFAULT_RESPONSABLE", "Administrador"),
        "tipo_reporte": "Resumen general",
        "fecha_generacion": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "filtros": filtros,
        "columnas": data["columns"],
        "filas": data["rows"],
        "total_registros": len(data["rows"]),
        "cantidad_total": totals["cantidad_total"],
        "valor_total": totals["valor_total"]
    }


def load_report_template():
    template_path = os.path.join(
        os.path.dirname(__file__),
        "information_general_pdf.html"
    )

    with open(template_path, "r", encoding="utf-8") as template_file:
        return template_file.read()


@app.route("/InformationGeneralPdf", methods=["GET"])
def reporte_general_pdf():
    try:
        output_format = request.args.get("format", "pdf").strip().lower()
        context = build_reporte_pdf_context()
        context["render_mode"] = output_format
        html = render_template_string(load_report_template(), **context)

        if output_format == "html":
            return html

        if output_format != "pdf":
            return jsonify({"error": "Formato no soportado. Usa html o pdf"}), 400

        if pisa is None:
            return jsonify({
                "error": "La libreria xhtml2pdf no esta instalada en el servidor"
            }), 500

        pdf_buffer = BytesIO()
        pdf = pisa.CreatePDF(html, dest=pdf_buffer, encoding="utf-8")

        if pdf.err:
            return jsonify({"error": "No se pudo generar el PDF"}), 500

        response = make_response(pdf_buffer.getvalue())
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="resumen_general_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf"'
        )
        return response
    except Exception as e:
        print(f"DEBUG PDF ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500


# ============================================
# HEALTH CHECK
# ============================================
@app.route("/")
def health():
    return jsonify({"status": "ok", "message": "API funcionando correctamente"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=False)


