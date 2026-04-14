# Mise En Ligne

## Stack de production recommandee

- Hebergement: Railway ou Render
- Serveur Python: Gunicorn
- Base de donnees: PostgreSQL
- Fichiers statiques: WhiteNoise

## Variables d'environnement a definir

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS=votre-domaine.railway.app,votre-domaine.onrender.com`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://votre-domaine.railway.app,https://votre-domaine.onrender.com`
- `DJANGO_TIME_ZONE=Africa/Conakry`
- `DATABASE_URL=postgresql://...`

## Commandes de build

### Railway

- Build: `pip install -r requirements.txt`
- Start: `gunicorn transport_system.wsgi --log-file -`

### Render

- Build Command: `pip install -r requirements.txt && bash build.sh`
- Start Command: `gunicorn transport_system.wsgi --log-file -`

## Premiere mise en ligne

1. Pousser le projet sur GitHub.
2. Creer un service web Railway ou Render depuis le depot GitHub.
3. Ajouter une base PostgreSQL.
4. Renseigner les variables d'environnement.
5. Lancer le deploiement.
6. Creer un superutilisateur si necessaire avec une commande shell distante.

## Important

- Ne pas utiliser `db.sqlite3` en production.
- Toujours faire les migrations avant les nouvelles versions.
- Vous pourrez continuer a faire des mises a jour apres la mise en ligne en redeployant le projet.
