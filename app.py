from flask import Flask, jsonify, request, session
from flask_cors import CORS
from flask_mail import Mail, Message
from flask_bcrypt import Bcrypt
from itsdangerous import URLSafeTimedSerializer
import mysql.connector
import uuid
from datetime import datetime, timedelta
import os

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
mail = Mail(app)
app.secret_key = "DotacionesZambrano" 
bcrypt = Bcrypt(app)

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


app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = os.environ.get("MAIL_USER")
app.config['MAIL_PASSWORD'] = os.environ.get("MAIL_PASS")
app.config['MAIL_DEFAULT_SENDER'] = ('Inventario', 'jhonizam2023@gmail.com')

mail = Mail(app)
serializer = URLSafeTimedSerializer(app.secret_key)


@app.route("/RecuperarPassword", methods=["POST", "OPTIONS"])
def recuperar_password():
    if request.method == "OPTIONS":
        return "", 200

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

        if not user:
            return jsonify({"ok": False, "error": "Correo no registrado"}), 404

        token = str(uuid.uuid4())
        expiracion = datetime.now() + timedelta(minutes=30)

        cursor.execute("""
            INSERT INTO password_resets (idUsuario, token, expira)
            VALUES (%s, %s, %s)
        """, (user["idUsuario"], token, expiracion))

        conn.commit()

        link = f"http://127.0.0.1:5500/reset.html?token={token}"

        msg = Message(
            "Recuperar contraseña",
            recipients=[email],
            html=f"""
            <h3>Recuperación de contraseña</h3>
            <p>Haz clic para cambiar tu contraseña:</p>
            <a href="{link}">{link}</a>
            <p>Este enlace expira en 30 minutos.</p>
            """
        )

        mail.send(msg)

        return jsonify({"ok": True})

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/ResetPassword", methods=["POST", "OPTIONS"])
def reset_password():
    if request.method == "OPTIONS":
        return "", 200

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
            WHERE token=%s AND usado=0 AND expira > NOW()
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
            "UPDATE password_resets SET usado=1 WHERE id=%s",
            (reset["id"],)
        )

        conn.commit()

        return jsonify({"ok": True})

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/CrearUsuario", methods=["POST"])
def crear_usuario():
    data = request.json
    usuario = data["usuario"]
    password = data["password"]

    password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO usuarios (usuario, password, id_estado)
        VALUES (%s, %s, 1)
    """, (usuario, password_hash))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"ok": True})


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
        return jsonify({"ok": False, "error": "Usuario no existe o inactivo"}), 401

    if not bcrypt.check_password_hash(user["password"], password):
        return jsonify({"ok": False, "error": "Contraseña incorrecta"}), 401

    # guardar sesión
    session["idUsuario"] = user["idUsuario"]
    session["usuario"] = user["usuario"]

    return jsonify({
        "ok": True,
        "usuario": user["usuario"]
    })

@app.route("/CheckSession")
def check_session():
    if "idUsuario" not in session:
        return jsonify({"ok": False}), 401
    return jsonify({"ok": True, "usuario": session["usuario"]})

@app.route("/Logout", methods=["POST", "OPTIONS"])
def logout():
    if request.method == "OPTIONS":
        return "", 200

    session.clear()
    return jsonify({"ok": True})


@app.route("/GetProductos", methods=["GET"])
def get_productos():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            v.id_variante,
            m.nombre AS marca,
            e.nombre AS estilo,
            c.nombre AS color,
            t.valor AS talla,
            v.precio,
            v.stock
        FROM variantes v
        JOIN productos p ON v.id_producto = p.id_producto
        JOIN marcas m ON p.id_marca = m.id_marca
        LEFT JOIN estilos e ON p.id_estilo = e.id_estilo
        JOIN colores c ON v.id_color = c.id_color
        JOIN tallas t ON v.id_talla = t.id_talla
        WHERE v.id_estado = 1 and p.id_estado = 1
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

        id_categoria = data.get("id_categoria")
        nombre = data.get("nombre", "").strip()
        marca  = data.get("marca", "").strip()
        estilo = data.get("estilo")
        id_color = data.get("id_color")
        variantes = data.get("variantes")
        

        if not id_categoria:
            return jsonify({"ok": False, "error": "Categoría requerida"}), 400

        if not nombre:
            return jsonify({"ok": False, "error": "Nombre requerido"}), 400

        if not marca:
            return jsonify({"ok": False, "error": "Marca requerida"}), 400

        if not variantes:
            return jsonify({"ok": False, "error": "Debe tener variantes"}), 400

        if estilo:
            estilo = estilo.strip()
            if estilo == "":
                estilo = None

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)


        # ======================
        # MARCA
        # ======================
        cursor.execute(
            "SELECT id_marca FROM marcas WHERE nombre = (%s)",
            (marca,)
        )
        row = cursor.fetchone()

        if row:
            id_marca = row["id_marca"]
        else:
            cursor.execute(
                "INSERT INTO marcas (nombre) VALUES (%s)",
                (marca,)
            )
            id_marca = cursor.lastrowid

        # ======================
        # ESTILO (OPCIONAL)
        # ======================
        id_estilo = None
        if estilo:
            cursor.execute(
                "SELECT id_estilo FROM estilos WHERE nombre = %s AND id_marca = %s",
                (estilo, id_marca)
            )
            row = cursor.fetchone()

            if row:
                id_estilo = row["id_estilo"]
            else:
                cursor.execute(
                    "INSERT INTO estilos (id_marca, nombre) VALUES (%s, %s)",
                    (id_marca, estilo)
                )
                id_estilo = cursor.lastrowid

        # ======================
        # PRODUCTO
        # ======================
        cursor.execute("""
            INSERT INTO productos (nombre, id_marca, id_estilo, id_estado,id_categoria)
            VALUES (%s, %s, %s, 1, %s)
        """, (nombre, id_marca, id_estilo, id_categoria))

        id_producto = cursor.lastrowid

        # ======================
        # VARIANTES
        # ======================
        for v in variantes:
            talla  = v["talla"]
            precio = v["precio"]
            stock  = v["stock"]

            cursor.execute(
                "SELECT id_talla FROM tallas WHERE valor = %s",
                (talla,)
            )
            row = cursor.fetchone()

            if row:
                id_talla = row["id_talla"]
            else:
                cursor.execute(
                    "INSERT INTO tallas (valor) VALUES (%s)",
                    (talla,)
                )
                id_talla = cursor.lastrowid

            cursor.execute("""
                INSERT INTO variantes
                (id_producto, id_color, id_talla, precio, stock, id_estado)
                VALUES (%s, %s, %s, %s, %s, 1)
            """, (id_producto, id_color, id_talla, precio, stock))

        conn.commit()
        return jsonify({"ok": True})

    except Exception as e:
        if conn:
            conn.rollback()
        print("🔥 ERROR ADD PRODUCTO:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/GetCategorias")
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

@app.route("/ActualizarStock", methods=["POST"])
def actualizar_stock():
    data = request.get_json()

    id_variante   = data.get("id_variante")
    nuevo_stock   = data.get("nuevo_stock")

    if id_variante is None or nuevo_stock is None:
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

        if stock_anterior == nuevo_stock:
            return jsonify({"ok": True})  # nada que hacer

        diferencia = nuevo_stock - stock_anterior

        tipo = "ENTRADA" if diferencia > 0 else "SALIDA"
        cantidad = abs(diferencia)

        cursor.execute(
            "UPDATE variantes SET stock = %s WHERE id_variante = %s",
            (nuevo_stock, id_variante)
        )
        cursor.execute("""
            INSERT INTO movimientos_inventario
            (id_variante, tipo, cantidad, stock_anterior, stock_nuevo)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            id_variante,
            tipo,
            cantidad,
            stock_anterior,
            nuevo_stock
        ))

        conn.commit()
        return jsonify({"ok": True})

    except Exception as e:
        conn.rollback()
        print("🔥 ERROR ACTUALIZAR STOCK:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()



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


@app.route("/AddColor", methods=["POST", "OPTIONS"])
def add_color():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

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


@app.route("/GetColores")
def get_colores():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT id_color, nombre FROM colores ORDER BY nombre")
    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(data)


@app.route("/GetTallas")
def get_tallas():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT id_talla, valor FROM tallas ORDER BY valor")
    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(data)



@app.route("/DeleteProductos", methods=["POST", "OPTIONS"])
def delete_productos():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

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
        print("🔥 ERROR DELETE:", e)
        return jsonify({"error": str(e)}), 500



if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))













