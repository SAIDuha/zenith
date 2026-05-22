# NETEXIAL — Portail de Réparations

Plateforme SaaS premium de gestion de demandes de réparation pour NETEXIAL.  
Frontend **single-file** HTML/CSS/JS + Backend **Python (Flask)** + **Supabase** (auth, BDD, storage, realtime).

---

## 🎨 Charte graphique

Strictement conforme au guide graphique NETEXIAL :
- **Bleu principal** `#141B4D` · **Bleu foncé** `#1C3775` · **Bleu moyen** `#98B2DD` · **Bleu clair** `#C1D1EB`
- **Orange contrastant** `#FC6100`
- **Typographies** : Oswald (titres) · Roboto (texte) · Monoton (chiffres-clés)
- Logo NETEXIAL reproduit en SVG inline (silhouette à filets arrondis, bleu de marque)
- Motifs subtils inspirés du guide en filigrane

---

## 📁 Structure du projet

```
netexial/
├── index.html              # Frontend complet (single-file SPA)
├── app.py                  # Backend Flask (emails, anti-doublon, PDF, notifications)
├── requirements.txt        # Dépendances Python
├── supabase_setup.sql      # Schéma BDD complet + RLS + triggers + storage
├── .env.example            # Variables d'environnement (à dupliquer en .env)
└── README.md               # Ce fichier
```

---

## 🚀 Lancement en local — pas à pas

### 1. Créer le projet Supabase

1. Créer un compte sur https://supabase.com et un nouveau projet
2. Aller dans **SQL Editor** → copier-coller le contenu intégral de `supabase_setup.sql` → **Run**  
   Cela crée : 7 tables, triggers (numéros de ticket auto `NTX-YYYY-NNNNNN`), policies RLS, fonctions RPC, buckets storage et publication realtime
3. Aller dans **Storage** → vérifier que les buckets `logos` et `repair-images` existent et sont **publics**
4. Aller dans **Project Settings → API** et noter :
   - `Project URL` → variable `SUPABASE_URL`
   - `anon public key` → variable `SUPABASE_ANON_KEY`
   - `service_role key` → variable `SUPABASE_SERVICE_ROLE_KEY` (⚠️ jamais côté client)
5. Aller dans **Project Settings → API → JWT Settings** et noter `JWT Secret` → `SUPABASE_JWT_SECRET`

### 2. Créer le premier administrateur

Dans le dashboard Supabase :
1. **Authentication → Users → Add user → Create new user** avec un email + mot de passe
2. Copier l'`id` de cet utilisateur (UUID)
3. Dans **SQL Editor**, exécuter :
   ```sql
   INSERT INTO public.users (id, email, full_name, role)
   VALUES ('<UUID_COPIÉ>', 'admin@netexial.com', 'Admin NETEXIAL', 'admin');
   ```

> Les utilisateurs clients peuvent ensuite être créés depuis l'interface admin de l'app.

### 3. Configurer le backend Python

```bash
cd netexial
python -m venv venv
source venv/bin/activate          # macOS / Linux
# venv\Scripts\activate           # Windows
pip install -r requirements.txt
cp .env.example .env
```

Éditer `.env` :

```env
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOi...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...
SUPABASE_JWT_SECRET=ton-secret-jwt

# SMTP (optionnel — sans config, les emails sont simulés en console)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=ton.email@gmail.com
SMTP_PASSWORD=mot-de-passe-application
SMTP_FROM=NETEXIAL <noreply@netexial.com>
SERVICE_EMAIL=service.reparations@netexial.com

DOUBLON_WINDOW_HOURS=24
PORT=5000
FLASK_DEBUG=1
ALLOWED_ORIGINS=*
```

Lancer le backend :
```bash
python app.py
```
Le backend écoute sur `http://localhost:5000`.

### 4. Configurer le frontend

Ouvrir `index.html` et compléter l'objet `CONFIG` en tout début de `<script>` (~ ligne 1900) :

```js
const CONFIG = {
    SUPABASE_URL:      'https://xxx.supabase.co',
    SUPABASE_ANON_KEY: 'eyJhbGciOi...',
    API_BASE:          'http://localhost:5000/api'
};
```

### 5. Servir le frontend

Le plus simple :
```bash
python -m http.server 8000
```
Puis ouvrir http://localhost:8000/index.html

> Pour un déploiement rapide en prod, glisse `index.html` sur **Netlify Drop** (https://app.netlify.com/drop) et héberge le backend Flask sur Render / Railway / Fly.io.

---

## 🧑‍💼 Espace client

- Connexion via email + code client (transmis par l'admin)
- **Scan de code-barres** webcam (ZXing : EAN, UPC, Code128, QR…) OU saisie manuelle
- Auto-remplissage produit (référence, n° série, type équipement) depuis la table `products`
- Drag & drop multi-photos avec **compression auto** (1600px max, JPEG 85%) avant upload
- Description avec compteur de caractères (2000 max)
- **Anti-doublon** automatique : alerte si demande similaire dans les 24h
- Email de confirmation HTML aux couleurs NETEXIAL
- Historique consultable

## 🛠️ Espace administrateur

- **Dashboard** : 4 KPI cards + graphique d'évolution 30j + répartition statuts
- **Gestion demandes** : tableau filtrable (statut/recherche temps réel/pagination), modal détail, accepter/refuser
- **Gestion clients** : CRUD complet (nom, code, logo URL, email, formulaire associé)
- **Gestion formulaires** : éditeur de champs personnalisés en JSON
- À l'acceptation : email auto au service réparation avec toutes les infos + liens photos
- Au refus : email au client avec motif
- **Export PDF** branded par demande
- **Temps réel** : notifications instantanées sur nouvelles demandes (Supabase Realtime)

---

## 🎁 Bonus inclus (en plus du cahier des charges)

✅ Mode sombre (toggle dans la sidebar)  
✅ Numéros de ticket auto-générés au format `NTX-YYYY-NNNNNN` (trigger Postgres)  
✅ Notifications temps réel (Supabase Realtime)  
✅ Export PDF avec branding NETEXIAL (reportlab)  
✅ Compression image côté client avant upload  
✅ Lightbox pour aperçu photos  
✅ Formulaires personnalisables par client (champs JSON)  
✅ Restauration de session au reload  
✅ Anti-doublon avec confirmation utilisateur  
✅ Toasts modernes (success / error / warning / info)  
✅ Animations fluides, glassmorphism subtil, micro-interactions  
✅ Responsive mobile / tablette / desktop  
✅ Tableau filtres + recherche + pagination dynamique  
✅ Stats graphiques (Chart.js : line chart 30j + doughnut statuts)

---

## 🔐 Sécurité

- **RLS Postgres activé** sur toutes les tables sensibles
- Admins → accès total · Clients → accès uniquement à leurs propres données
- Backend Flask vérifie chaque JWT via `SUPABASE_JWT_SECRET` (PyJWT)
- La `service_role key` n'est utilisée **que** côté backend (jamais exposée)
- CORS configurable via `ALLOWED_ORIGINS`
- Storage public en lecture mais upload contrôlé par RLS auth

---

## 📡 Endpoints backend

| Méthode | Endpoint | Rôle |
|---|---|---|
| GET  | `/api/health` | Healthcheck |
| POST | `/api/check-duplicate` | Vérif anti-doublon avant envoi |
| POST | `/api/send-confirmation` | Email HTML au client après création |
| POST | `/api/notify-service` | Email au service interne (accept) ou client (refus) |
| GET  | `/api/export-pdf/<id>` | Export PDF branded d'une demande |
| POST | `/api/admin/create-user` | Création user auth + profil (admin only) |

Tous les endpoints (sauf `/health`) requièrent un header `Authorization: Bearer <jwt_supabase>`.

---

## 🐛 Troubleshooting

**"Failed to fetch" au login** → vérifier `SUPABASE_URL` / `ANON_KEY` dans `CONFIG` (index.html)  
**"Invalid JWT" backend** → le `SUPABASE_JWT_SECRET` est sous **Project Settings → API → JWT Settings**, pas la clé service_role  
**Photos n'apparaissent pas** → vérifier que le bucket `repair-images` est bien **public** dans Storage  
**Emails non envoyés** → sans `SMTP_*` configurés, l'app simule l'envoi (les emails apparaissent en console serveur)  
**Aucune demande visible côté admin** → vérifier que le user est bien `role = 'admin'` dans la table `public.users`

---

Made with ⚡ for **NETEXIAL** · Partenaire spécialiste de la protection au travail.
