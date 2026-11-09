# Partage de fichiers en P2P

Projet de programmation TCP/IP en Python. Le serveur central authentifie les utilisateurs et indexe les descriptions. Chaque pair publie son catalogue, recherche des fichiers et sert les téléchargements directement aux autres pairs.

## Préparation

Sur macOS ou Linux, utiliser Python 3.9 ou ultérieur et la commande `openssl`. Les modules Python utilisés appartiennent à la bibliothèque standard. Depuis le répertoire du projet, préparer un certificat local et un fichier chez Alice :

```sh
sh certificat.sh
mkdir -p demo/alice demo/bob
printf '%s\n' 'Exemple de cours sur les tables de routage.' > demo/alice/cours.txt
printf '%s\n' '{"cours.txt":"Introduction au routage IP"}' > demo/alice/catalogue.json
```

Le catalogue associe chaque nom de fichier à sa description. Pour proposer un nouveau fichier, le placer dans le dossier du pair, ajouter son entrée à `catalogue.json`, puis saisir `actualiser`.

## Démonstration locale

Ouvrir trois terminaux dans le répertoire du projet. Lancer d'abord le central :

```sh
python3 central.py
```

Dans le deuxième terminal, démarrer Alice :

```sh
python3 pair.py --utilisateur alice --inscrire --port 5001 --dossier demo/alice
```

Dans le troisième, démarrer Bob :

```sh
python3 pair.py --utilisateur bob --inscrire --port 5002 --dossier demo/bob
```

Chaque utilisateur choisit son mot de passe à l'invite. `--inscrire` crée le compte et ouvre la session. Pour les lancements ultérieurs, retirer cette option et saisir le même mot de passe.

1. Chez Bob, saisir `chercher routage`. Le résultat indique `cours.txt` à l'adresse du pair Alice sur le port 5001.
2. Saisir `prendre 1`. Bob vérifie l'empreinte du transfert, enregistre le fichier et sa description, puis publie son catalogue.
3. Saisir `quitter` chez Alice. Chez Bob, relancer `chercher routage` : seule la source sur le port 5002 apparaît.
4. Pour confirmer le repartage, lancer un troisième pair dans le terminal libéré avec `python3 pair.py --utilisateur chloe --inscrire --port 5003 --dossier demo/chloe`. Saisir `chercher routage`, puis `prendre 1`. Le fichier provient de Bob.

| Commande chez un pair | Effet |
|---|---|
| `chercher MOT` | Recherche une sous-chaîne dans les descriptions, sans distinction de casse. |
| `trier nom` ou `trier hote` | Réordonne et renumérote les derniers résultats. |
| `prendre N` | Télécharge le résultat N et l'ajoute au partage. Un fichier existant est préservé. |
| `actualiser` | Relit le catalogue du disque et le republie. |
| `quitter` | Retire les annonces et ferme le pair. |

## Essai avec deux ordinateurs

Relier les deux machines au même réseau local. Exemple avec le central sur la machine A d'adresse **192.168.1.20** (adapter cette adresse).

- Sur A, générer un certificat pour son adresse avec `sh certificat.sh 192.168.1.20 certs-lan`. Conserver la clé privée sur A. Transmettre uniquement `central.crt` au binôme par un canal de confiance.
- Lancer le central sur A avec `python3 central.py --hote 0.0.0.0 --cert certs-lan/central.crt --cle certs-lan/central.key`.
- Sur chaque machine, lancer le pair avec les arguments de la démonstration et ajouter `--central 192.168.1.20 --cert CHEMIN/central.crt`. Chaque pair annonce le port sur lequel il écoute. Le central utilise l'adresse IP source de sa connexion.
- Si un pare-feu filtre les connexions entrantes, autoriser le port TCP 5000 sur A et le port de partage choisi sur chaque machine. La démonstration suppose que les pairs peuvent se joindre directement.

## Fonctionnement

- **Annuaire** (`central.py`). Une connexion TCP/TLS persistante par pair, traitée dans un thread. Le serveur conserve les comptes dans SQLite avec un sel aléatoire et PBKDF2-HMAC-SHA256. L'index des fichiers est en mémoire et protégé par un verrou. Une fermeture de connexion détectée retire les annonces associées.
- **Protocole** (`protocole.py`). Chaque message de contrôle est un objet JSON UTF-8 terminé par un saut de ligne. Les actions du central sont `inscrire`, `connexion`, `publier`, `chercher` et `quitter`. Chaque réponse indique `ok` et, en cas d'échec, `erreur`.
- **Transfert** (`transfert.py`). Le pair destinataire envoie `obtenir` et le nom du fichier. La source répond par sa taille, les octets du fichier, puis une trame contenant son SHA-256. Le destinataire reçoit dans un répertoire temporaire et intègre le fichier après vérification. Les transferts directs utilisent TCP en clair sur le réseau local. L'empreinte vérifie la cohérence du transfert ; elle n'authentifie pas la source.
- **Client** (`pair.py`). Le serveur de fichiers reste disponible en arrière-plan pendant les commandes interactives. Après un redémarrage du central, relancer les pairs pour rétablir leurs sessions et republier les catalogues.

## Tests

```sh
python3 -m unittest -v
```

Les tests génèrent un certificat temporaire, ouvrent des ports locaux attribués par le système et créent des dossiers de partage isolés. Ils exercent l'authentification concurrente, l'interface en ligne de commande, le nettoyage après interruption et le repartage entre trois pairs.
