"""Trames de contrôle JSON en UTF-8 terminées par LF, puis données binaires pour GET."""
import json
from pathlib import Path

LIMITE = 4 * 1024 * 1024
BLOC = 65536


def envoyer(flux, **message):
    data = json.dumps(message, ensure_ascii=False).encode() + b"\n"
    if len(data) > LIMITE:
        raise ValueError("Message trop volumineux")
    flux.write(data)
    flux.flush()


def recevoir(flux):
    ligne = flux.readline(LIMITE + 1)
    if not ligne:
        raise EOFError("Connexion fermée")
    if len(ligne) > LIMITE or not ligne.endswith(b"\n"):
        raise ValueError("Trame incomplète ou trop longue")
    message = json.loads(ligne)
    if not isinstance(message, dict):
        raise ValueError("Objet JSON attendu")
    return message


def requete(flux, action, **champs):
    envoyer(flux, action=action, **champs)
    reponse = recevoir(flux)
    if not reponse.get("ok"):
        raise ValueError(reponse.get("erreur", "Requête refusée"))
    return reponse


def nom_valide(nom):
    if (not isinstance(nom, str) or not nom or nom.startswith(".")
            or Path(nom).name != nom or "/" in nom or "\\" in nom
            or any(ord(c) < 32 for c in nom) or nom.casefold() == "catalogue.json"):
        raise ValueError("Nom de fichier interdit")
    return nom
