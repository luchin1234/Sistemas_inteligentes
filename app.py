import eventlet
eventlet.monkey_patch()
from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from models import db, Visita, Emocion
from config import DATABASE_URI, BLACKLIST_PATH,PALABRAS_BLOQUEO_PATH
import os

emociones_generales = []
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
navegador_cerrado_por_emociones = False
db.init_app(app)
socketio = SocketIO(app, async_mode='eventlet')
emociones_contador = {
    "happy": 0,
    "sad": 0,
    "angry": 0,
    "surprise": 0,
    "disgust": 0,
    "fear": 0,
    "neutral": 0
}


def cargar_palabras_bloqueo():
    if os.path.exists(PALABRAS_BLOQUEO_PATH):
        with open(PALABRAS_BLOQUEO_PATH, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []

def guardar_palabras_bloqueo(lista):
    print("📝 Guardando palabras en:", os.path.abspath(PALABRAS_BLOQUEO_PATH))
    with open(PALABRAS_BLOQUEO_PATH, "w", encoding="utf-8") as f:
        for palabra in lista:
            f.write(palabra.strip() + "\n")

@app.route("/palabras_bloqueo", methods=["GET", "POST"])
def palabras_bloqueo():
    if request.method == "GET":
        return jsonify(cargar_palabras_bloqueo())
    elif request.method == "POST":
        data = request.get_json()
        print("📩 Palabras recibidas:", data)
        if isinstance(data, list):
            guardar_palabras_bloqueo(data)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Formato inválido"}), 400

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/registrar_visita", methods=["POST"])
def registrar_visita():
    data = request.get_json()
    print("📥 Recibida:", data)

    visita = Visita(
        url=data.get("url"),
        es_prohibida=data.get("es_prohibida", False),
        categoria_ia=data.get("categoria_ia"),
        fuente=data.get("fuente", "desconocida"),
        forzado_cierre=data.get("forzado_cierre", False)
    )
    db.session.add(visita)
    db.session.commit()

    # Solo agregar a lista negra si NO es un programa
    if visita.es_prohibida and not visita.url.lower().startswith("[programa]"):
        agregar_a_lista_negra(visita.url)

    socketio.emit("nueva_visita", {
        "url": visita.url,
        "fecha": visita.fecha.strftime("%Y-%m-%d %H:%M:%S"),
        "es_prohibida": visita.es_prohibida,
        "categoria_ia": visita.categoria_ia,
        "fuente": visita.fuente,
        "forzado_cierre": visita.forzado_cierre
    })

    return jsonify({"ok": True})


@app.route("/visitas")
def visitas():
    ultimas = Visita.query.order_by(Visita.fecha.desc()).limit(50).all()
    return jsonify([
        {
            "url": v.url,
            "fecha": v.fecha.strftime("%Y-%m-%d %H:%M:%S"),
            "es_prohibida": v.es_prohibida,
            "categoria_ia": v.categoria_ia,
            "fuente": v.fuente
        } for v in ultimas
    ])

@app.route("/estadisticas")
def estadisticas():
    total = Visita.query.count()
    alertas = Visita.query.filter_by(es_prohibida=True).count()
    paginas_web = Visita.query.filter(Visita.fuente=="chrome").distinct(Visita.url).count()
    programas = Visita.query.filter(Visita.fuente=="programa").distinct(Visita.url).count()
    programas_forzados = Visita.query.filter_by(fuente="programa", forzado_cierre=True).count()
    return jsonify({
        "visitas_totales": total,
        "alertas_emitidas": alertas,
        "paginas_web": paginas_web,
        "programas": programas,
        "programas_forzados": programas_forzados
    })

@app.route("/lista_negra")
def lista_negra():
    if os.path.exists(BLACKLIST_PATH):
        with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
            return jsonify(f.read().splitlines())
    return jsonify([])

def agregar_a_lista_negra(url):
    url = url.strip().lower()
    if not os.path.exists(BLACKLIST_PATH):
        open(BLACKLIST_PATH, "w").close()
    with open(BLACKLIST_PATH, "r+", encoding="utf-8") as f:
        lineas = [line.strip().lower() for line in f.readlines()]
        if url not in lineas:
            f.write(url + "\n")
            print(f"🔝 Agregado a lista negra IA: {url}")
            
# --------- Socket.IO para emociones generales ---------
@app.route("/verificar_emociones_negativas")
def verificar_emociones_negativas():
    negativas = {"triste", "enojado", "cansado", "asustado"}
    ultimas = Emocion.query.order_by(Emocion.fecha.desc()).limit(15).all()
    contador = sum(1 for e in ultimas if e.emocion in negativas)
    return jsonify({"bloquear": contador >= 10})

@app.route("/emociones_generales")
def emociones_generales_api():
    emociones = Emocion.query.order_by(Emocion.fecha.desc()).limit(15).all()
    conteo = {}
    for e in emociones:
        clave = e.emocion
        conteo[clave] = conteo.get(clave, 0) + 1
    return jsonify(conteo)

@app.route("/emociones_recientes")
def emociones_recientes():
    ultimas = Emocion.query.order_by(Emocion.fecha.desc()).limit(15).all()
    return jsonify([
        {"emocion": e.emocion, "confianza": e.confianza, "fecha": e.fecha.strftime("%H:%M:%S")}
        for e in reversed(ultimas)
    ])

@socketio.on("emocion_general")
def recibir_emocion_general(data):
    global navegador_cerrado_por_emociones
    emocion = data.get("emocion")
    confianza = data.get("confianza")
    if not emocion or confianza is None:
        return

    nueva_emocion = Emocion(emocion=emocion, confianza=confianza)
    db.session.add(nueva_emocion)
    db.session.commit()

    # Limitar a 15 emociones
    total = Emocion.query.count()
    if total > 15:
        eliminar = Emocion.query.order_by(Emocion.fecha.asc()).limit(total - 15).all()
        for e in eliminar:
            db.session.delete(e)
        db.session.commit()

    socketio.emit("emocion_general", {"emocion": emocion, "confianza": confianza})

    # Evaluar si se deben cerrar navegadores
    negativas = ["enojado", "triste", "cansado", "asustado"]
    ultimas = Emocion.query.order_by(Emocion.fecha.desc()).limit(15).all()
    cuenta_negativas = sum(1 for e in ultimas if e.emocion in negativas)
    if cuenta_negativas >= 2:
        navegador_cerrado_por_emociones = True
        socketio.emit("navegador_cerrado_alerta", {"motivo": "❌ CERRANDO NAVEGADORES POR EXCESO DE EMOCIONES NEGATIVAS"})
    else:
        navegador_cerrado_por_emociones = False

@socketio.on("stream_frame")
def handle_stream_frame(data):
    # 1. Registrar emoción en BD
    emocion = data.get("emocion")
    confianza = data.get("confianza", 0)
    if emocion is not None and confianza is not None:
        nueva_emocion = Emocion(emocion=emocion, confianza=confianza)
        db.session.add(nueva_emocion)
        db.session.commit()
        # Limitar solo a 15 en la tabla
        total = Emocion.query.count()
        if total > 15:
            eliminar = Emocion.query.order_by(Emocion.fecha.asc()).limit(total - 15).all()
            for e in eliminar:
                db.session.delete(e)
            db.session.commit()
    # 2. Reemitir el frame + emoción a los paneles
    socketio.emit("stream_frame", data)  

@app.route("/detalle_webs")
def detalle_webs():
    # Solo visitas de tipo chrome, agrupadas por URL
    webs = Visita.query.filter(Visita.fuente=="chrome").order_by(Visita.fecha.desc()).all()
    return jsonify([
        {
            "url": v.url,
            "fecha": v.fecha.strftime("%Y-%m-%d %H:%M:%S"),
            "es_prohibida": v.es_prohibida,
            "categoria_ia": v.categoria_ia
        } for v in webs
    ])

@app.route("/detalle_programas")
def detalle_programas():
    # Solo visitas de tipo programa
    programas = Visita.query.filter(Visita.fuente=="programa").order_by(Visita.fecha.desc()).all()
    return jsonify([
        {
            "url": v.url,
            "fecha": v.fecha.strftime("%Y-%m-%d %H:%M:%S"),
            "es_prohibida": v.es_prohibida,
            "categoria_ia": v.categoria_ia,
            "forzado_cierre": v.forzado_cierre
        } for v in programas
    ])

@app.route("/detalle_alertas")
def detalle_alertas():
    alertas = Visita.query.filter_by(es_prohibida=True).order_by(Visita.fecha.desc()).all()
    return jsonify([
        {
            "url": v.url,
            "fecha": v.fecha.strftime("%Y-%m-%d %H:%M:%S"),
            "fuente": v.fuente,
            "categoria_ia": v.categoria_ia
        } for v in alertas
    ])

@app.route("/detalle_forzados")
def detalle_forzados():
    forzados = Visita.query.filter_by(forzado_cierre=True).order_by(Visita.fecha.desc()).all()
    return jsonify([
        {
            "url": v.url,
            "fecha": v.fecha.strftime("%Y-%m-%d %H:%M:%S"),
            "fuente": v.fuente,
            "categoria_ia": v.categoria_ia,
            "es_prohibida": v.es_prohibida
        } for v in forzados
    ])



@app.route("/emociones_db")
def emociones_db():
    ultimas = Emocion.query.order_by(Emocion.fecha.desc()).limit(15).all()
    return jsonify([
        {
            "id": e.id,
            "fecha": e.fecha.strftime("%Y-%m-%d %H:%M:%S"),
            "emocion": e.emocion,
            "confianza": round(e.confianza, 2)
        }
        for e in ultimas
    ])
    
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("🟢 Base de datos lista")
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
