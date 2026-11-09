"""Pair interactif : publication, recherche et téléchargement."""
import argparse
import getpass
import socket
import ssl
import threading
from contextlib import ExitStack

from protocole import requete
from transfert import Partage, ServiceFichiers, telecharger


def publier(flux, service):
    requete(flux, "publier", port=service.server_address[1], hostname=socket.gethostname(),
            catalogue=service.partage.actualiser())


def interface(flux, service):
    resultats = []
    aide = ("chercher MOT | trier nom/hote | prendre NUMERO | "
            "actualiser | aide | quitter")
    print(aide, flush=True)
    while True:
        try:
            ligne = input("p2p> ").strip()
        except (KeyboardInterrupt, EOFError):
            ligne = "quitter"
        commande, _, arg = ligne.partition(" ")
        arg = arg.strip()
        try:
            if commande == "quitter":
                requete(flux, "quitter")
                return
            if commande == "chercher":
                resultats = requete(flux, "chercher", mot=arg)["resultats"]
            elif commande == "trier":
                if arg not in ("nom", "hote"):
                    raise ValueError("Tri attendu : nom ou hote")
                resultats.sort(key=lambda a: ((a.get("hostname", a["hote"]) if arg == "hote" else a["nom"]).casefold(), a["nom"].casefold(),
                                               a["hote"], a["port"]))
            elif commande == "prendre":
                numero = int(arg)
                if not 1 <= numero <= len(resultats):
                    raise ValueError("Numéro absent des résultats")
                try:
                    telecharger(service.partage, resultats[numero - 1])
                except (OSError, EOFError) as exc:
                    print(f"Téléchargement impossible : {exc}")
                    continue
                publier(flux, service)
                print("Fichier téléchargé et partagé.")
                continue
            elif commande == "actualiser":
                publier(flux, service)
                print("Catalogue publié.")
                continue
            else:
                print(aide)
                continue
            if not resultats:
                print("Aucun résultat.")
            for i, a in enumerate(resultats, 1):
                print(f"{i}. {a['nom']} — {a['description']} — {a.get('hostname', a['hote'])} [{a['hote']}:{a['port']}]")
        except ValueError as exc:
            print(f"Erreur : {exc}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--central", default="127.0.0.1")
    p.add_argument("--port-central", type=int, default=5000)
    p.add_argument("--ecoute", default="0.0.0.0")
    p.add_argument("--port", type=int, default=0, help="port du pair (0 = automatique)")
    p.add_argument("--dossier", required=True)
    p.add_argument("--cert", default="certs/central.crt")
    p.add_argument("--utilisateur", help="identifiant (demandé si omis)")
    p.add_argument("--inscrire", action="store_true", help="créer le compte au premier lancement")
    args = p.parse_args()
    partage = Partage(args.dossier)
    contexte = ssl.create_default_context(cafile=args.cert)
    utilisateur = args.utilisateur or input("Identifiant : ").strip()
    mot = getpass.getpass("Mot de passe : ")
    with ExitStack() as pile:
        sock = pile.enter_context(socket.create_connection((args.central, args.port_central), 10))
        tls = pile.enter_context(contexte.wrap_socket(sock, server_hostname=args.central))
        flux = pile.enter_context(tls.makefile("rwb"))
        requete(flux, "inscrire" if args.inscrire else "connexion", nom=utilisateur, mot=mot)
        service = pile.enter_context(ServiceFichiers((args.ecoute, args.port), partage))
        threading.Thread(target=service.serve_forever, daemon=True).start()
        pile.callback(service.shutdown)
        publier(flux, service)
        print(f"Connecté. Port de partage : {service.server_address[1]}")
        interface(flux, service)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, EOFError, OSError) as exc:
        raise SystemExit(f"Erreur : {exc}") from None
