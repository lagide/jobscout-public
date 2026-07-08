# JobScout — maintenance géographique et liens

Démon externe (hors container) qui purge les offres hors périmètre géographique
et les liens morts. À lancer depuis la racine du projet, sur l'hôte qui héberge
la stack Docker.

## Politique

- Les offres `full_remote` restent autorisées partout en France.
- Les offres hybrides, sur site ou de mode inconnu sont conservées uniquement si leur localisation appartient à l'union suivante :
  - toute l'Île-de-France ;
  - le périmètre défini dans `config/geo_scope.json` (origine + rayon, voir `scripts/generate_geo_scope.py`).
- Une offre du pipeline (`application_status`) ou archivée n'est jamais supprimée.
- Les liens 404/410, explicitement expirés ou redirigés vers une page d'expiration sont supprimés si toutes les sources sont fermées.
- Les 403/429 anti-bot ne déclenchent jamais une suppression immédiate.
- Pour Indeed seulement, une offre d'au moins 7 jours est supprimée si toutes ses sources restent bloquées pendant trois contrôles quotidiens consécutifs.
- Les autres erreurs réseau doivent également se répéter trois fois avant suppression.

## Planification

Le daemon tourne en tant qu'utilisateur non-root, sous le compte qui héberge le projet :

- filtre géographique chaque heure ;
- validation complète des liens toutes les 24 heures ;
- quatre workers, timeout 12 secondes ;
- état persistant dans `state.json` ;
- journal rotatif dans `maintenance.log`.

Commandes :

```sh
~/jobscout/maintenance/start.sh
~/jobscout/maintenance/stop.sh
cat ~/jobscout/maintenance/daemon.pid
tail -f ~/jobscout/maintenance/maintenance.log
```

Dry-runs :

```sh
cd ~/jobscout
python3 maintenance/maintenance.py --dry-run --skip-links
python3 maintenance/maintenance.py --dry-run --skip-geo --workers 4 --timeout 12
```

## Sauvegardes

Chaque suppression crée d'abord un JSON complet dans `maintenance/backups/`, avec SHA-256 dans le rapport associé. Les rapports vivent dans `maintenance/reports/`.

## Composants

Les fichiers suivants sont déjà installés dans les sources :

- `backend/geo_scope.py` ;
- `backend/job_urls.py` ;
- `backend/scraper.py` ;
- `backend/tests/test_geo_scope.py` ;
- `backend/tests/test_scraper_urls.py` ;
- `config/geo_scope.json`.

Ils ajoutent le filtre avant insertion et rendent le lien employeur direct prioritaire pour Indeed, tout en conservant le lien Indeed dans `sources`.

Le backend tourne depuis une image Docker : ces changements de scraper ne sont actifs qu'après un rebuild (`docker compose build backend && docker compose up -d backend`). Le daemon externe doit être relancé après un redémarrage de l'hôte (pas de service systemd fourni par défaut).
