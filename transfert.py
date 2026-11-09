"""Catalogue local et transfert direct de fichiers entre pairs."""
import hashlib
import json
import os
import socket
import socketserver
import tempfile
import threading
from pathlib import Path

from protocole import BLOC, envoyer, nom_valide, recevoir, requete


class Partage:
    def __init__(self, dossier):
        self.dossier = Path(dossier)
        self.dossier.mkdir(parents=True, exist_ok=True)
        self.manifeste = self.dossier / "catalogue.json"
        self.verrou = threading.RLock()
        self.catalogue = {}
        self.actualiser()

    def actualiser(self):
        with self.verrou:
            contenu = json.loads(self.manifeste.read_text("utf8")) if self.manifeste.exists() else {}
            if not isinstance(contenu, dict):
                raise ValueError("Le catalogue doit être un objet JSON")
            catalogue = {}
            for nom, description in contenu.items():
                chemin = self.dossier / nom_valide(nom)
                if not isinstance(description, str) or not description.strip():
                    raise ValueError(f"Description manquante pour {nom}")
                if chemin.is_file() and not chemin.is_symlink():
                    catalogue[nom] = description
            self.catalogue = catalogue
            return dict(catalogue)

    def ouvrir(self, nom):
        with self.verrou:
            if nom_valide(nom) not in self.catalogue:
                raise ValueError("Fichier indisponible")
            return open(self.dossier / nom, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW))

    def integrer(self, temporaire, nom, description):
        cible = self.dossier / nom_valide(nom)
        with self.verrou:
            self.actualiser()
            catalogue = {**self.catalogue, nom: description}
            with tempfile.NamedTemporaryFile(dir=self.dossier, prefix=".catalogue-",
                                             mode="w", encoding="utf8", delete=False) as f:
                brouillon = Path(f.name)
            try:
                brouillon.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2), "utf8")
                os.link(temporaire, cible)  # Création exclusive, même si deux réceptions se terminent ensemble.
                try:
                    brouillon.replace(self.manifeste)
                except OSError:
                    cible.unlink()
                    raise
                self.catalogue = catalogue
            finally:
                brouillon.unlink(missing_ok=True)


class ServiceFichiers(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, adresse, partage):
        self.partage = partage
        super().__init__(adresse, Envoi)


class Envoi(socketserver.StreamRequestHandler):
    timeout = 20

    def handle(self):
        try:
            msg = recevoir(self.rfile)
            if msg.get("action") != "obtenir":
                raise ValueError("Seul le téléchargement est accepté")
            source = self.server.partage.ouvrir(msg.get("nom"))
        except (ValueError, OSError, EOFError) as exc:
            try:
                envoyer(self.wfile, ok=False, erreur=str(exc))
            except OSError:
                pass
            return
        try:
            with source:
                taille = os.fstat(source.fileno()).st_size
                envoyer(self.wfile, ok=True, taille=taille)
                empreinte = hashlib.sha256()
                restant = taille
                while restant:
                    bloc = source.read(min(BLOC, restant))
                    if not bloc:
                        return
                    self.wfile.write(bloc)
                    empreinte.update(bloc)
                    restant -= len(bloc)
                envoyer(self.wfile, sha256=empreinte.hexdigest())
        except OSError:
            return


def telecharger(partage, annonce):
    nom = nom_valide(annonce["nom"])
    with socket.create_connection((annonce["hote"], annonce["port"]), timeout=20) as sock:
        with sock.makefile("rwb") as flux:
            taille = requete(flux, "obtenir", nom=nom)["taille"]
            if type(taille) is not int or taille < 0:
                raise ValueError("Taille invalide")
            with tempfile.TemporaryDirectory(dir=partage.dossier, prefix=".reception-") as tmp:
                chemin = Path(tmp) / "contenu"
                empreinte = hashlib.sha256()
                with chemin.open("wb") as f:
                    while taille:
                        bloc = flux.read(min(BLOC, taille))
                        if not bloc:
                            raise EOFError("Transfert interrompu")
                        f.write(bloc)
                        empreinte.update(bloc)
                        taille -= len(bloc)
                if recevoir(flux).get("sha256") != empreinte.hexdigest():
                    raise ValueError("Empreinte du fichier incorrecte")
                partage.integrer(chemin, nom, annonce["description"])
