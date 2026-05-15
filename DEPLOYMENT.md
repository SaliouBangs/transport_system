# Mise en ligne Railway

## Stack retenue

- Hebergement : Railway
- Serveur Python : Gunicorn
- Base de donnees : PostgreSQL Railway
- Fichiers statiques : WhiteNoise
- Build : `build.sh`
- Runtime : `nixpacks.toml` + `Procfile`

## Fichiers de deploiement deja presents

- [Procfile](C:\Users\HP\transport_projet\transport_system\Procfile)
- [build.sh](C:\Users\HP\transport_projet\transport_system\build.sh)
- [nixpacks.toml](C:\Users\HP\transport_projet\transport_system\nixpacks.toml)
- [requirements.txt](C:\Users\HP\transport_projet\transport_system\requirements.txt)

## Variables Railway a definir

Obligatoires :

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS=<votre-service>.up.railway.app`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://<votre-service>.up.railway.app`
- `DATABASE_URL` : fournie par le plugin PostgreSQL Railway

Recommandees :

- `DJANGO_TIME_ZONE=Africa/Conakry`
- `DJANGO_SECURE_SSL_REDIRECT=True`
- `DJANGO_SECURE_HSTS_SECONDS=3600`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=True`
- `DJANGO_SECURE_HSTS_PRELOAD=False`

## Ce que fait deja le projet

- Build : installation dependances + `collectstatic`
- Start : `migrate` puis demarrage Gunicorn

En pratique, avec la configuration actuelle :

- `build.sh` lance `python manage.py collectstatic --noinput`
- `nixpacks.toml` lance `pip install -r requirements.txt`
- `Procfile` / start lance `python manage.py migrate && gunicorn transport_system.wsgi --log-file -`

## Checklist premiere mise en ligne

1. Pousser la branche a jour sur GitHub.
2. Sur Railway, creer un nouveau service depuis ce depot.
3. Verifier que le `Root Directory` pointe sur le dossier du projet Django si necessaire.
4. Ajouter un service PostgreSQL Railway.
5. Verifier que `DATABASE_URL` est bien injectee dans le service web.
6. Ajouter les variables d'environnement listees ci-dessus.
7. Lancer le deploiement.
8. Verifier dans les logs que :
   - les dependances s'installent
   - `collectstatic` passe
   - `migrate` passe
   - Gunicorn demarre sans erreur
9. Ouvrir l'URL publique Railway.
10. Creer un superutilisateur :
    - `python manage.py createsuperuser`

## Verifications post-deploiement

Verifier au minimum :

- connexion
- dashboard
- commandes
- encaissements
- detail client
- logistique
- maintenance

## Notes importantes

- Ne pas utiliser `db.sqlite3` en production.
- Les donnees locales `db.sqlite3` et la base Railway ne sont pas liees.
- Les camions / entretiens / depenses deja saisis localement ne monteront pas automatiquement sur Railway.
- Si vous voulez ces donnees sur Railway, il faudra les ressaisir ou faire un import dedie.
