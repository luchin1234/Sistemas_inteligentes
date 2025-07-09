from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Visita(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    url = db.Column(db.String(512))
    es_prohibida = db.Column(db.Boolean)
    categoria_ia = db.Column(db.String(100))
    fuente = db.Column(db.String(100))
    forzado_cierre = db.Column(db.Boolean, default=False)
    

class Emocion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    emocion = db.Column(db.String(50), nullable=False)
    confianza = db.Column(db.Float, nullable=False)  