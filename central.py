"""Annuaire central des utilisateurs et des fichiers disponibles."""
import argparse
import hashlib
import hmac
import os
import socketserver
import sqlite3
import ssl
import threading
from contextlib import closing

from protocole import envoyer, recevoir, nom_valide


class Annuaire(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, adresse, base, certificat, cle):
        self.base = base
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(certificat, cle)
        self.pairs = {}
        self.verrou = threading.Lock()
        with closing(sqlite3.connect(base)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS comptes "
                       "(nom TEXT PRIMARY KEY, sel BLOB, empreinte BLOB)")
        super().__init__(adresse, Session)

    def identifier(self, action, nom, mot):
        if not isinstance(nom, str) or not nom.strip() or len(nom) > 80:
            raise ValueError("Identifiant invalide")
        if not isinstance(mot, str) or not 1 <= len(mot) <= 1024:
            raise ValueError("Mot de passe invalide")
        with closing(sqlite3.connect(self.base)) as db:
            ligne = db.execute("SELECT sel, empreinte FROM comptes WHERE nom=?",
                               (nom,)).fetchone()
            sel = os.urandom(16) if action == "inscrire" or not ligne else ligne[0]
            empreinte = hashlib.pbkdf2_hmac("sha256", mot.encode(), sel, 600000)
            if action == "inscrire":
                try:
                    with db:
                        db.execute("INSERT INTO comptes VALUES (?, ?, ?)", (nom, sel, empreinte))
                except sqlite3.IntegrityError:
                    raise ValueError("Identifiant déjà utilisé") from None
            elif not ligne or not hmac.compare_digest(empreinte, ligne[1]):
                raise ValueError("Identifiants incorrects")


class Session(socketserver.BaseRequestHandler):
    def handle(self):
        self.nom = None
        try:
            self.request.settimeout(10)
            with self.server.tls.wrap_socket(self.request, server_side=True) as sock:
                with sock.makefile("rwb") as flux:
                    while True:
                        msg = recevoir(flux)
                        try:
                            resultat = self.traiter(msg)
                        except (ValueError, KeyError, TypeError) as exc:
                            envoyer(flux, ok=False, erreur=str(exc))
                            continue
                        envoyer(flux, ok=True, **resultat)
                        if self.nom is not None:
                            sock.settimeout(None)
                        if msg.get("action") == "quitter":
                            break
        except (EOFError, OSError, ValueError):
            pass
        finally:
            with self.server.verrou:
                if self.nom is not None:
                    self.server.pairs.pop(self, None)

    def traiter(self, msg):
        action = msg["action"]
        if action in ("inscrire", "connexion") and self.nom is None:
            self.server.identifier(action, msg["nom"], msg["mot"])
            self.nom = msg["nom"]
            return {}
        if self.nom is None:
            raise ValueError("Authentification nécessaire")
        if action == "quitter":
            with self.server.verrou:
                self.server.pairs.pop(self, None)
            return {}
        if action == "publier":
            port, catalogue = msg["port"], msg["catalogue"]
            if type(port) is not int or not 1 <= port <= 65535 or not isinstance(catalogue, dict):
                raise ValueError("Annonce invalide")
            annonces = []
            for nom, description in catalogue.items():
                nom_valide(nom)
                if not isinstance(description, str) or not description.strip():
                    raise ValueError("Description manquante")
                annonces.append(dict(nom=nom, description=description,
                                     hote=self.client_address[0], port=port))
            with self.server.verrou:
                self.server.pairs[self] = annonces
            return {}
        if action == "chercher":
            mot, tri = msg["mot"], msg.get("tri", "nom")
            if not isinstance(mot, str) or not mot.strip() or tri not in ("nom", "hote"):
                raise ValueError("Recherche invalide")
            with self.server.verrou:
                lignes = [a for groupe in self.server.pairs.values() for a in groupe
                          if mot.casefold() in a["description"].casefold()]
            return {"resultats": sorted(lignes, key=lambda a: (a[tri].casefold(),
                                       a["nom"].casefold(), a["hote"], a["port"]))}
        raise ValueError("Commande inconnue")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hote", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--base", default="comptes.sqlite")
    p.add_argument("--cert", default="certs/central.crt")
    p.add_argument("--cle", default="certs/central.key")
    args = p.parse_args()
    with Annuaire((args.hote, args.port), args.base, args.cert, args.cle) as serveur:
        print(f"Annuaire en écoute sur {serveur.server_address}", flush=True)
        try:
            serveur.serve_forever()
        except KeyboardInterrupt:
            pass
