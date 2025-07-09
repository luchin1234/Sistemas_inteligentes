import sqlite3
import time
import base64
import psutil
import socketio
import shutil
import tempfile
import os
import win32gui
import win32process
import requests
from config import SERVER_URL, BLACKLIST_PATH, PALABRAS_BLOQUEO_PATH
from urllib.parse import urlparse
from transformers import pipeline
from bs4 import BeautifulSoup
import cv2
from fer import FER
import signal

MAPEO_EMOCIONES = {
    "angry": "enojado",
    "disgust": "cansado",
    "fear": "asustado",
    "happy": "feliz",
    "sad": "triste",
    "surprise": "sorprendido",
    "neutral": "neutral"
}
PROCESOS_IGNORADOS = [
    "python.exe", "conhost.exe", "sihost.exe", "ctfmon.exe", "RuntimeBroker.exe",
    "SearchHost.exe", "SearchProtocolHost.exe", "SearchIndexer.exe", "svchost.exe",
    "sqlwriter.exe", "LsaIso.exe", "Registry", "ShellExperienceHost.exe",
    "StartMenuExperienceHost.exe", "backgroundTaskHost.exe", "armsvc.exe",
    "WidgetService.exe", "Widgets.exe", "NisSrv.exe", "OfficeClickToRun.exe",
    "CtesHostSvc.exe", "amdfendrsr.exe", "RadeonSoftware.exe", "AMDRSServ.exe",
    "AMDInstallManager.exe", "NVIDIA Overlay.exe", "nvcontainer.exe", "SSScheduler.exe",
    "XboxPcApp.exe"
]

# Cargar modelo Haarcascade para detección de rostros
cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(cascade_path)

PALABRAS_BLOQUEO_CACHE = []
PALABRAS_ULTIMA_ACTUALIZACION = 0
CHROME_HISTORY_PATH = os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data\Default\History")
clasificador_texto = pipeline("text-classification", model="unitary/toxic-bert")
clasificador_programas = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")
CATEGORIAS_PROGRAMA = ["videojuego", "navegador", "educativa", "multimedia", "sistema", "otro"]

sio = socketio.Client()
urls_alertadas = {}
programas_reportados = set()  # Solo registra programas abiertos una vez
programas_forzados_ya_cerrados = set()  # Controla cierre forzado de Edge
contador_emociones_negativas = 0
detector_emocion = FER()


def hay_rostro(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)
    return len(faces) > 0

def clasificar_tipo_programa(nombre_programa):
    try:
        texto = f"El programa llamado {nombre_programa} es de tipo:"
        resultado = clasificador_programas(texto, CATEGORIAS_PROGRAMA)
        return resultado["labels"][0], resultado["scores"][0]
    except Exception as e:
        print(f"[ERROR clasificando programa] {nombre_programa}: {e}")
        return "otro", 0
    

def cargar_palabras_bloqueo():
    global PALABRAS_BLOQUEO_CACHE, PALABRAS_ULTIMA_ACTUALIZACION
    import time
    # Solo actualiza cada 10 segundos para no saturar el servidor
    ahora = time.time()
    if ahora - PALABRAS_ULTIMA_ACTUALIZACION > 10:
        try:
            resp = requests.get(SERVER_URL + "/palabras_bloqueo", timeout=3)
            if resp.ok:
                PALABRAS_BLOQUEO_CACHE = [p.lower() for p in resp.json() if p.strip()]
                PALABRAS_ULTIMA_ACTUALIZACION = ahora
        except Exception as e:
            print("No se pudo sincronizar palabras de bloqueo:", e)
    return PALABRAS_BLOQUEO_CACHE


def limpiar_url(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".lower()

def cargar_lista_negra():
    if os.path.exists(BLACKLIST_PATH):
        with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
            return set([l.strip().lower() for l in f.readlines()])
    return set()

def guardar_en_lista_negra(url):
    url = url.strip().lower()
    lista = cargar_lista_negra()
    if url not in lista:
        with open(BLACKLIST_PATH, "a", encoding="utf-8") as f:
            f.write(url + "\n")
        print(f"[🛑 AGREGADO A LISTA NEGRA IA]: {url}")

def verificar_y_cerrar_por_emociones():
    try:
        r = requests.get(SERVER_URL + "/verificar_emociones_negativas")
        data = r.json()
        if data.get("bloquear"):
            print("🚫 Se detectaron 10 emociones negativas, cerrando navegador...")
            cerrar_navegadores(motivo="emociones_negativas")
    except Exception as e:
        print("[ERROR verificando emociones negativas]", e)

def emitir_frame_emocion():
    cam = cv2.VideoCapture(0)
    ret, frame = cam.read()
    cam.release()
    if not ret:
        return

    if not hay_rostro(frame):
        print("❌ No se detectó rostro en la cámara. No se envía emoción.")
        # Puedes notificar al servidor que no hay rostro, si quieres mostrar mensaje en panel web
        sio.emit("stream_frame", {
            "frame": "",
            "emocion": "sin rostro",
            "confianza": 0
        })
        return

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    emociones = detector_emocion.detect_emotions(frame_rgb)
    emocion_general = "desconocida"
    confianza = 0.0
    if emociones:
        emocion_principal = emociones[0]["emotions"]
        emocion = max(emocion_principal, key=emocion_principal.get)
        confianza = emocion_principal[emocion]
        emocion_general = MAPEO_EMOCIONES.get(emocion, "otro")
    else:
        emocion_general = "no detectada"
        confianza = 0

    # Codifica el frame en JPG y luego en base64
    frame = cv2.resize(frame, (320, 240))
    _, buffer = cv2.imencode('.jpg', frame)
    jpg_as_text = base64.b64encode(buffer).decode('utf-8')

    sio.emit("stream_frame", {
        "frame": jpg_as_text,
        "emocion": emocion_general,
        "confianza": round(confianza, 2)
    })


def cerrar_navegadores(motivo="desconocido"):
    # Solo registra como prohibido si el motivo es 'lista_negra'
    edge_cerrado = False
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == "msedge.exe":
                os.kill(proc.info["pid"], signal.SIGTERM)
                edge_cerrado = True
        except Exception as e:
            print(f"[ERROR cerrando navegador] {e}")

    if edge_cerrado and motivo == "lista_negra":
        payload = {
            "url": "[PROGRAMA] msedge.exe",
            "es_prohibida": True,
            "categoria_ia": "lista_negra",
            "fuente": "programa",
            "forzado_cierre": True
        }
        requests.post(SERVER_URL + "/registrar_visita", json=payload)
        programas_forzados_ya_cerrados.add("msedge.exe")
    elif edge_cerrado and motivo == "emociones_negativas":
        # No lo marques como prohibido ni lista negra
        payload = {
            "url": "[PROGRAMA] msedge.exe",
            "es_prohibida": False,
            "categoria_ia": "emociones_negativas",
            "fuente": "programa",
            "forzado_cierre": True
        }
        requests.post(SERVER_URL + "/registrar_visita", json=payload)
        programas_forzados_ya_cerrados.add("msedge.exe")

def obtener_titulo(url):
    try:
        resp = requests.get(url, timeout=5)
        if resp.ok:
            soup = BeautifulSoup(resp.text, "html.parser")
            return soup.title.string if soup.title else ""
    except:
        return ""
    return ""

def es_url_prohibida(url, titulo):
    for palabra in cargar_palabras_bloqueo():
        if palabra in url.lower() or palabra in (titulo or "").lower():
            return True, "palabra_clave"
    if titulo:
        resultado = clasificador_texto(titulo[:512])[0]
        if resultado['label'] == "toxic" and resultado['score'] > 0.7:
            return True, "toxic_ia"
    return False, "permitida"

def registrar_visita(url, es_prohibida, categoria, fuente="chrome"):
    payload = {
        "url": url,
        "es_prohibida": es_prohibida,
        "categoria_ia": categoria,
        "fuente": fuente
    }
    print("[CLIENTE] Enviando visita:", payload)
    try:
        requests.post(SERVER_URL + "/registrar_visita", json=payload)
    except Exception as e:
        print("[ERROR ENVÍO VISITA]", e)

def obtener_historial_chrome():
    if not os.path.exists(CHROME_HISTORY_PATH):
        return
    try:
        temp = tempfile.gettempdir()
        copia = os.path.join(temp, "temp_chrome.db")
        shutil.copy2(CHROME_HISTORY_PATH, copia)
        conn = sqlite3.connect(copia)
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM urls ORDER BY last_visit_time DESC LIMIT 10")
        resultados = cursor.fetchall()
        conn.close()
        lista_negra = cargar_lista_negra()
        for url, in resultados:
            limpia = limpiar_url(url)
            if limpia not in urls_alertadas and limpia not in lista_negra:
                titulo = obtener_titulo(limpia)
                es_malo, categoria = es_url_prohibida(limpia, titulo)
                urls_alertadas[limpia] = time.time()
                if es_malo:
                    guardar_en_lista_negra(limpia)
                registrar_visita(limpia, es_malo, categoria)
    except Exception as e:
        print("[ERROR HISTORIAL]", e)

def obtener_ventanas_visibles():
    visibles = set()
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            visibles.add(pid)
        return True
    win32gui.EnumWindows(callback, None)
    return visibles

def obtener_programas_abiertos():
    programas = []
    visibles = obtener_ventanas_visibles()
    for p in psutil.process_iter(['pid', 'name']):
        try:
            nombre = p.info['name']
            if (
                nombre and
                nombre.lower() not in [x.lower() for x in PROCESOS_IGNORADOS] and
                p.info['pid'] in visibles
            ):
                programas.append(nombre)
        except Exception:
            continue
    return list(set(programas))


def reportar_programa(nombre):
    # Solo reporta si no fue reportado antes
    if nombre in programas_reportados:
        return
    tipo, _ = clasificar_tipo_programa(nombre)
    registrar_visita(f"[PROGRAMA] {nombre}", False, tipo, fuente="programa")
    programas_reportados.add(nombre)

def limpiar_programas_reportados():
    # Si Edge ya no está, permite reportar de nuevo cuando se abra
    edge_abierto = False
    for p in psutil.process_iter(['name']):
        if p.info['name'] and p.info['name'].lower() == "msedge.exe":
            edge_abierto = True
    if not edge_abierto:
        programas_reportados.discard("msedge.exe")
        programas_forzados_ya_cerrados.discard("msedge.exe")

@sio.on("connect")
def conectado():
    print("[✅ CONECTADO AL SERVIDOR]")

if __name__ == "__main__":
    try:
        sio.connect(SERVER_URL)
        while True:
            emitir_frame_emocion()
        
            verificar_y_cerrar_por_emociones()
            for nombre in obtener_programas_abiertos():
                reportar_programa(nombre)
            limpiar_programas_reportados()  # Permite volver a registrar si Edge se cerró y abrió de nuevo
            obtener_historial_chrome()
            time.sleep(0.5)  # estaba en 20
    except Exception as e:
        print("[CLIENTE ERROR]", e)
