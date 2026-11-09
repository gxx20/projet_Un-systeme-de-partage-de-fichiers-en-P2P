"""Tests d'intégration sur de vraies sockets TCP locales, avec certificats temporaires."""
import json
import os
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

from central import Annuaire
from pair import publier
from protocole import envoyer, recevoir, requete
from transfert import Partage, ServiceFichiers, telecharger

RACINE = Path(__file__).resolve().parent


class ProjetTest(unittest.TestCase):
    def setUp(self):
        self.pile = ExitStack()
        self.addCleanup(self.pile.close)
        self.tmp = Path(self.pile.enter_context(tempfile.TemporaryDirectory()))
        subprocess.run(["sh", str(RACINE / "certificat.sh"), "127.0.0.1", str(self.tmp)],
                       check=True, capture_output=True)
        self.cert = self.tmp / "central.crt"
        self.central = self.servir(Annuaire(("127.0.0.1", 0), self.tmp / "db",
                                             self.cert, self.tmp / "central.key"))

    def servir(self, serveur):
        self.pile.enter_context(serveur)
        t = threading.Thread(target=serveur.serve_forever, kwargs={"poll_interval": 0.02})
        t.start()
        self.pile.callback(t.join)
        self.pile.callback(serveur.shutdown)
        return serveur

    def session(self):
        contexte = ssl.create_default_context(cafile=self.cert)
        sock = self.pile.enter_context(socket.create_connection(self.central.server_address, 5))
        tls = self.pile.enter_context(contexte.wrap_socket(sock, server_hostname="localhost"))
        return self.pile.enter_context(tls.makefile("rwb"))

    def pair(self, nom):
        flux = self.session()
        requete(flux, "inscrire", nom=nom, mot="secret")
        partage = Partage(self.tmp / nom)
        service = self.servir(ServiceFichiers(("127.0.0.1", 0), partage))
        publier(flux, service)
        return flux, service

    def test_repartage_apres_depart(self):
        a, sa = self.pair("alice")
        b, sb = self.pair("bob")
        c, sc = self.pair("chloe")
        contenu = os.urandom(170000)
        (sa.partage.dossier / "cours.bin").write_bytes(contenu)
        sa.partage.manifeste.write_text(json.dumps({"cours.bin": "Cours de routage IP"}))
        publier(a, sa)
        def sources():
            return requete(c, "chercher", mot="ROUT", tri="hote")["resultats"]
        self.assertEqual(sources()[0]["hostname"], socket.gethostname())
        telecharger(sb.partage, sources()[0])
        publier(b, sb)
        self.assertEqual(len(sources()), 2)
        requete(a, "quitter")
        self.assertEqual([x["port"] for x in sources()], [sb.server_address[1]])
        telecharger(sc.partage, sources()[0])
        self.assertEqual((sc.partage.dossier / "cours.bin").read_bytes(), contenu)
        self.assertEqual(Partage(sc.partage.dossier).catalogue, {"cours.bin": "Cours de routage IP"})

    def test_authentification_et_concurrence(self):
        a, b = self.session(), self.session()
        with self.assertRaisesRegex(ValueError, "Authentification"):
            requete(a, "chercher", mot="test")
        with ThreadPoolExecutor(2) as pool:
            travaux = [pool.submit(requete, f, "inscrire", nom=n, mot="secret")
                       for f, n in [(a, "alice"), (b, "bob")]]
            for travail in travaux:
                self.assertTrue(travail.result()["ok"])
        autre = self.session()
        with self.assertRaisesRegex(ValueError, "déjà utilisé"):
            requete(autre, "inscrire", nom="alice", mot="autre")
        with self.assertRaisesRegex(ValueError, "incorrects"):
            requete(autre, "connexion", nom="alice", mot="incorrect")
        self.assertTrue(requete(autre, "connexion", nom="alice", mot="secret")["ok"])

    def test_reception_interrompue(self):
        class Interruption(socketserver.StreamRequestHandler):
            def handle(self):
                recevoir(self.rfile)
                envoyer(self.wfile, ok=True, taille=100)
                self.wfile.write(b"abc")

        hote, port = self.servir(socketserver.TCPServer(("127.0.0.1", 0), Interruption)).server_address
        partage = Partage(self.tmp / "reception")
        with self.assertRaises(EOFError):
            telecharger(partage, dict(hote=hote, port=port, nom="cours", description="test"))
        self.assertEqual(list(partage.dossier.iterdir()), [])
        with self.assertRaises(ValueError):
            partage.ouvrir("../central.key")

    def test_tri_des_hotes_et_recherche_vide(self):
        a, sa = self.pair("alice")
        b, sb = self.pair("bob")
        for flux, service, hostname, nom in [(a, sa, "zeta", "a.txt"), (b, sb, "alpha", "z.txt")]:
            requete(flux, "publier", port=service.server_address[1], hostname=hostname,
                    catalogue={nom: "Réseaux TCP"})
        for tri, attendu in [("nom", ["zeta", "alpha"]), ("hote", ["alpha", "zeta"])]:
            lignes = requete(a, "chercher", mot="tcp", tri=tri)["resultats"]
            self.assertEqual([ligne["hostname"] for ligne in lignes], attendu)
        self.assertEqual(requete(a, "chercher", mot="absent")["resultats"], [])

    def test_refus_upload(self):
        _, service = self.pair("alice")
        with socket.create_connection(service.server_address, timeout=5) as sock:
            with sock.makefile("rwb") as flux:
                with self.assertRaisesRegex(ValueError, "téléchargement"):
                    requete(flux, "upload", nom="intrus.txt")
        self.assertEqual(list(service.partage.dossier.iterdir()), [])

    def test_commandes_terminal(self):
        a, sa = self.pair("source")
        for nom in ["z.txt", "a.txt"]:
            (sa.partage.dossier / nom).write_text(nom)
        sa.partage.manifeste.write_text(json.dumps({"z.txt": "réseau", "a.txt": "réseau"}))
        publier(a, sa)
        dossier = self.tmp / "destination"
        cmd = [sys.executable, str(RACINE / "pair.py"), "--port-central",
               str(self.central.server_address[1]), "--dossier", str(dossier),
               "--cert", str(self.cert), "--inscrire"]
        resultat = subprocess.run(cmd, input="lecteur\nsecret\nchercher réseau\ntrier hote\nprendre 1\nquitter\n",
                                  text=True, capture_output=True, timeout=15, start_new_session=True)
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertIn("1. a.txt", resultat.stdout)
        self.assertIn("Fichier téléchargé et partagé", resultat.stdout)
        self.assertEqual((dossier / "a.txt").read_text(), "a.txt")


if __name__ == "__main__":
    unittest.main()
