import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PALABRAS_BLOQUEO_PATH = os.path.join(BASE_DIR, "palabras_bloqueo.txt")

DATABASE_URI = "sqlite:///C:/Users/Victus/OneDrive/Documents/control_parental/instance/control_parental.db"

BLACKLIST_PATH = os.path.join(BASE_DIR, "lista_negra.txt")
SERVER_URL = "http://192.168.1.101:5000"
