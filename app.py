from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

import os
from datetime import datetime, date
from collections import defaultdict

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-cambiame-en-produccion")

database_url = os.environ.get("DATABASE_URL")

if database_url:
    database_url = database_url.replace("postgres://", "postgresql://", 1)
else:
    database_url = "sqlite:///database.db"

app.config["SQLALCHEMY_DATABASE_URI"] = database_url

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)


class Movimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), nullable=False)
    descripcion = db.Column(db.String(200), nullable=False)
    categoria = db.Column(db.String(100), default="Sin categoría")
    monto = db.Column(db.Float, nullable=False)
    fecha = db.Column(db.String(50), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False)


class Vencimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    fecha = db.Column(db.String(50), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))


def formato_pesos(numero):
    numero = int(numero)
    return f"{numero:,}".replace(",", ".")


def cargar_datos(mes=None, anio=None):
    datos = {
        "ingresos": [],
        "gastos": [],
        "fijos": [],
        "deudas": [],
        "vencimientos": []
    }

    query = Movimiento.query.filter_by(usuario_id=current_user.id)

    if mes and anio:
        prefijo = f"{anio}-{mes:02d}"
        query = query.filter(Movimiento.fecha.like(f"{prefijo}%"))

    movimientos = query.all()

    for mov in movimientos:
        datos[mov.tipo].append({
            "id": mov.id,
            "descripcion": mov.descripcion,
            "categoria": mov.categoria,
            "monto": mov.monto,
            "fecha": mov.fecha
        })

    vencimientos = Vencimiento.query.filter_by(usuario_id=current_user.id).all()

    for venc in vencimientos:
        datos["vencimientos"].append({
            "id": venc.id,
            "descripcion": venc.descripcion,
            "monto": venc.monto,
            "fecha": venc.fecha
        })

    return datos


def armar_historial(datos):
    historial = []

    nombres = {
        "ingresos": "Ingreso",
        "gastos": "Gasto variable",
        "fijos": "Gasto fijo",
        "deudas": "Deuda"
    }

    for tipo in ["ingresos", "gastos", "fijos", "deudas"]:
        for item in datos[tipo]:
            historial.append({
                "id": item.get("id"),
                "tipo": nombres.get(tipo, tipo),
                "tipo_original": tipo,
                "descripcion": item.get("descripcion", ""),
                "categoria": item.get("categoria", "Sin categoría"),
                "monto": formato_pesos(item.get("monto", 0)),
                "fecha": item.get("fecha", "Sin fecha")
            })

    historial.sort(key=lambda x: x["fecha"], reverse=True)
    return historial


def armar_vencimientos(datos):
    vencimientos = []

    for item in datos["vencimientos"]:
        vencimientos.append({
            "id": item.get("id"),
            "descripcion": item.get("descripcion", ""),
            "monto": formato_pesos(item.get("monto", 0)),
            "monto_numero": item.get("monto", 0),
            "fecha": item.get("fecha", "")
        })

    vencimientos.sort(key=lambda x: x["fecha"])
    return vencimientos


def generar_alertas_vencimientos(saldo, vencimientos):
    alertas = []
    hoy = date.today()

    for item in vencimientos:
        try:
            fecha_venc = datetime.strptime(item["fecha"], "%Y-%m-%d").date()
        except:
            continue

        dias = (fecha_venc - hoy).days

        if dias < 0:
            alertas.append({
                "tipo": "danger",
                "mensaje": f"🚨 Vencimiento atrasado: {item['descripcion']} por ${item['monto']}"
            })
        elif dias == 0:
            alertas.append({
                "tipo": "danger",
                "mensaje": f"🚨 Vence hoy: {item['descripcion']} por ${item['monto']}"
            })
        elif dias <= 7:
            alertas.append({
                "tipo": "warning",
                "mensaje": f"⚠️ Vence en {dias} días: {item['descripcion']} por ${item['monto']}"
            })

    return alertas


def generar_alertas_financieras(datos, total_ingresos, total_gastos, total_fijos, total_deudas, saldo):
    alertas = []

    gastos_totales = total_gastos + total_fijos + total_deudas

    if total_ingresos > 0 and gastos_totales > total_ingresos:
        diferencia = gastos_totales - total_ingresos
        alertas.append({
            "tipo": "danger",
            "mensaje": f"🚨 Tus gastos superan tus ingresos por ${formato_pesos(diferencia)}."
        })

    if total_ingresos > 0 and total_deudas > total_ingresos * 0.40:
        porcentaje = int((total_deudas / total_ingresos) * 100)
        alertas.append({
            "tipo": "warning",
            "mensaje": f"💳 Tus deudas consumen el {porcentaje}% de tus ingresos."
        })

    delivery_total = sum(
        item.get("monto", 0)
        for item in datos["gastos"]
        if "delivery" in item.get("categoria", "").lower()
    )

    if total_ingresos > 0 and delivery_total > total_ingresos * 0.15:
        alertas.append({
            "tipo": "warning",
            "mensaje": "🍔 Estás gastando mucho en delivery."
        })

    if saldo < 0:
        alertas.append({
            "tipo": "danger",
            "mensaje": f"🚨 Estás en negativo por ${formato_pesos(abs(saldo))}."
        })

    return alertas


def generar_alertas(datos, saldo, vencimientos, total_ingresos, total_gastos, total_fijos, total_deudas):
    alertas = []
    alertas.extend(generar_alertas_vencimientos(saldo, vencimientos))
    alertas.extend(generar_alertas_financieras(datos, total_ingresos, total_gastos, total_fijos, total_deudas, saldo))

    if not alertas:
        alertas.append({
            "tipo": "ok",
            "mensaje": "✅ Tus números se ven saludables."
        })

    return alertas


def grafico_por_categoria(datos):
    categorias = defaultdict(float)

    for tipo in ["gastos", "fijos", "deudas"]:
        for item in datos[tipo]:
            categoria = item.get("categoria", "Sin categoría")
            categorias[categoria] += item.get("monto", 0)

    return list(categorias.keys()), list(categorias.values())


def obtener_meses_disponibles():
    movimientos = Movimiento.query.filter_by(usuario_id=current_user.id).all()
    meses = set()
    for mov in movimientos:
        try:
            partes = mov.fecha[:7]  # "YYYY-MM"
            meses.add(partes)
        except:
            pass
    return sorted(meses, reverse=True)


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        password = request.form.get("password", "")

        if not usuario or not password:
            flash("Completá todos los campos.", "error")
            return render_template("registro.html")

        if len(password) < 4:
            flash("La contraseña debe tener al menos 4 caracteres.", "error")
            return render_template("registro.html")

        existe = Usuario.query.filter_by(usuario=usuario).first()

        if existe:
            flash("Ese usuario ya existe, elegí otro.", "error")
            return render_template("registro.html")

        nuevo_usuario = Usuario(
            usuario=usuario,
            password=generate_password_hash(password)
        )

        db.session.add(nuevo_usuario)
        db.session.commit()

        return redirect("/login")

    return render_template("registro.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        password = request.form.get("password", "")

        user = Usuario.query.filter_by(usuario=usuario).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect("/")

        flash("Usuario o contraseña incorrectos.", "error")
        return render_template("login.html")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")


@app.route("/")
@login_required
def home():
    mes_str = request.args.get("mes")
    mes = None
    anio = None

    if mes_str:
        try:
            anio, mes = int(mes_str[:4]), int(mes_str[5:7])
        except:
            pass

    datos = cargar_datos(mes=mes, anio=anio)

    total_ingresos = sum(item["monto"] for item in datos["ingresos"])
    total_gastos = sum(item["monto"] for item in datos["gastos"])
    total_fijos = sum(item["monto"] for item in datos["fijos"])
    total_deudas = sum(item["monto"] for item in datos["deudas"])
    total_vencimientos = sum(item["monto"] for item in datos["vencimientos"])

    saldo = total_ingresos - total_gastos - total_fijos - total_deudas

    vencimientos = armar_vencimientos(datos)

    alertas = generar_alertas(
        datos, saldo, vencimientos,
        total_ingresos, total_gastos, total_fijos, total_deudas
    )

    grafico_labels, grafico_valores = grafico_por_categoria(datos)
    meses_disponibles = obtener_meses_disponibles()

    return render_template(
        "index.html",
        ingresos=formato_pesos(total_ingresos),
        gastos=formato_pesos(total_gastos),
        fijos=formato_pesos(total_fijos),
        deudas=formato_pesos(total_deudas),
        vencimientos_total=formato_pesos(total_vencimientos),
        saldo=formato_pesos(saldo),
        saldo_numero=saldo,
        historial=armar_historial(datos),
        vencimientos=vencimientos,
        alertas=alertas,
        grafico_labels=grafico_labels,
        grafico_valores=grafico_valores,
        meses_disponibles=meses_disponibles,
        mes_seleccionado=mes_str or ""
    )


@app.route("/agregar", methods=["POST"])
@login_required
def agregar():
    tipo = request.form.get("tipo")
    descripcion = request.form.get("descripcion")
    categoria = request.form.get("categoria")
    monto = request.form.get("monto")

    if not tipo or not descripcion or not monto:
        return redirect("/")

    try:
        monto = float(monto)
    except:
        return redirect("/")

    nuevo_movimiento = Movimiento(
        tipo=tipo,
        descripcion=descripcion,
        categoria=categoria if categoria else "Sin categoría",
        monto=monto,
        fecha=datetime.now().strftime("%Y-%m-%d %H:%M"),
        usuario_id=current_user.id
    )

    db.session.add(nuevo_movimiento)
    db.session.commit()

    return redirect("/")


@app.route("/editar/<int:id>", methods=["GET", "POST"])
@login_required
def editar(id):
    movimiento = Movimiento.query.filter_by(
        id=id,
        usuario_id=current_user.id
    ).first()

    if not movimiento:
        return redirect("/")

    if request.method == "POST":
        movimiento.descripcion = request.form.get("descripcion")
        movimiento.categoria = request.form.get("categoria")

        try:
            movimiento.monto = float(request.form.get("monto"))
        except:
            pass

        db.session.commit()
        return redirect("/")

    return render_template("editar.html", movimiento=movimiento)


@app.route("/agregar_vencimiento", methods=["POST"])
@login_required
def agregar_vencimiento():
    descripcion = request.form.get("descripcion")
    monto = request.form.get("monto")
    fecha = request.form.get("fecha")

    if not descripcion or not monto or not fecha:
        return redirect("/")

    try:
        monto = float(monto)
    except:
        return redirect("/")

    nuevo_vencimiento = Vencimiento(
        descripcion=descripcion,
        monto=monto,
        fecha=fecha,
        usuario_id=current_user.id
    )

    db.session.add(nuevo_vencimiento)
    db.session.commit()

    return redirect("/")


@app.route("/eliminar/<int:id>", methods=["POST"])
@login_required
def eliminar(id):
    movimiento = Movimiento.query.filter_by(
        id=id,
        usuario_id=current_user.id
    ).first()

    if movimiento:
        db.session.delete(movimiento)
        db.session.commit()

    return redirect("/")


@app.route("/eliminar_vencimiento/<int:id>", methods=["POST"])
@login_required
def eliminar_vencimiento(id):
    vencimiento = Vencimiento.query.filter_by(
        id=id,
        usuario_id=current_user.id
    ).first()

    if vencimiento:
        db.session.delete(vencimiento)
        db.session.commit()

    return redirect("/")


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
