from flask import Flask, jsonify, request
from flask_cors import CORS
import mysql.connector

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

def get_connection():
    return mysql.connector.connect(
        host="byrui10afnftlqj38wlc-mysql.services.clever-cloud.com",
        user="uro8vvynewtyknux",
        password="CmNVHboKXsz0YzcrNFFh",
        database="byrui10afnftlqj38wlc"
    )

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







