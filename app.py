"""
============================================================================
NETEXIAL - Plateforme de gestion des demandes de réparation
Backend Flask
============================================================================

Architecture :
- Le frontend communique directement avec Supabase (auth, CRUD, storage, realtime)
  via supabase-js, ce qui est sécurisé par les Row Level Security policies.
- Ce backend Flask gère uniquement les opérations qui ne doivent PAS être
  exposées côté client :
    * envoi d'emails SMTP (clé service-role nécessaire pour bypass RLS sur logs)
    * vérification anti-doublon côté serveur
    * notifications email vers le service interne lors d'une validation
    * export PDF d'une demande

Le tout est sécurisé par vérification du JWT Supabase via header Authorization.
============================================================================
"""

import os

import smtplib
import logging
import json
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO


from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

from supabase import create_client, Client as SupaClient

import jwt as pyjwt


# ----------------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------------
load_dotenv()


SUPABASE_URL          = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_JWT_SECRET   = os.getenv("SUPABASE_JWT_SECRET", "")

SMTP_HOST             = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT             = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER             = os.getenv("SMTP_USER", "")
SMTP_PASSWORD         = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM             = os.getenv("SMTP_FROM", "no-reply@netexial.com")
SMTP_FROM_NAME        = os.getenv("SMTP_FROM_NAME", "IDEA by NETEXIAL")

SERVICE_EMAIL         = os.getenv("SERVICE_EMAIL", "reparations@netexial.com")

# Admin "de base" : reçoit TOUJOURS les notifications de pré-validation,
# quel que soit l'admin référent de l'utilisateur. Surchargeable via env.
BASE_ADMIN_ID         = os.getenv("BASE_ADMIN_ID", "55e0117b-b885-415c-9c9a-c1253ea6c9d4")

# URL publique de l'application (pour les liens de connexion dans les emails)
APP_URL               = os.getenv("APP_URL", "https://zenith-ao0n.onrender.com").rstrip("/")

ALLOWED_ORIGINS       = os.getenv("ALLOWED_ORIGINS", "*").split(",")
DOUBLON_WINDOW_HOURS  = int(os.getenv("DOUBLON_WINDOW_HOURS", "24"))

# Gemini Vision (OCR du nom du porteur)
GEMINI_API_KEY        = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL          = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ----------------------------------------------------------------------------
# INITIALISATION
# ----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("netexial")


app = Flask(__name__)

CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})


# Client Supabase avec clé service-role (bypass RLS, à utiliser avec prudence)
supabase: SupaClient = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) \
    if SUPABASE_URL and SUPABASE_SERVICE_KEY else None

if supabase is None:
    logger.warning(
        "Client Supabase NON initialise. Verifiez votre .env -> "
        "SUPABASE_URL: %s | SUPABASE_SERVICE_ROLE_KEY: %s",
        "OK" if SUPABASE_URL else "MANQUANTE",
        "OK" if SUPABASE_SERVICE_KEY else "MANQUANTE",
    )
else:
    logger.info("Client Supabase initialise avec succes.")


# ----------------------------------------------------------------------------
# AUTH : vérification du JWT Supabase
# ----------------------------------------------------------------------------
def verify_jwt(request_obj):
    """
    Vérifie le JWT Supabase passé dans le header Authorization.
    Utilise le SDK Supabase pour valider le token, ce qui fonctionne
    avec n'importe quel algorithme de signature (HS256, ES256, etc.).
    Retourne (payload, None) si valide, (None, error_response) sinon.
    """
    auth_header = request_obj.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, (jsonify({"error": "Token manquant"}), 401)

    token = auth_header.replace("Bearer ", "").strip()

    if not supabase:
        return None, (jsonify({"error": "Backend Supabase non configuré"}), 500)

    try:
        # Validation via le SDK Supabase (gère HS256 ET les nouveaux ECC keys)
        user_response = supabase.auth.get_user(token)
        user = user_response.user if user_response else None

        if not user:
            return None, (jsonify({"error": "Token invalide"}), 401)

        # Retourne un payload compatible avec l'ancien code
        return {"sub": user.id, "email": user.email}, None
    except Exception as e:
        logger.warning(f"JWT invalide : {e}")
        return None, (jsonify({"error": "Token invalide"}), 401)


def get_user_profile(user_id: str):
    """Récupère le profil utilisateur depuis la table public.users."""
    if not supabase:
        return None
    res = supabase.table("users").select("*").eq("id", user_id).single().execute()
    return res.data if res.data else None


# ----------------------------------------------------------------------------
# EMAILS : templates HTML respectant la charte NETEXIAL
# ----------------------------------------------------------------------------
def _email_template(titre: str, contenu_html: str, footer_extra: str = "") -> str:
    """
    Template HTML d'email aux couleurs NETEXIAL.
    Bleu principal #141B4D, orange contrastant #FC6100,
    typographies safe-email (Arial / sans-serif) pour compatibilité Outlook.
    """
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titre}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6fb;font-family:Arial,Helvetica,sans-serif;color:#141B4D;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f6fb;padding:40px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:12px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(20,27,77,0.08);">

          <!-- Bandeau IDEA -->
          <tr>
            <td style="background-color:#141B4D;padding:28px 32px;text-align:left;">
              <div style="color:#ffffff;font-size:26px;font-weight:700;letter-spacing:6px;">
                IDEA
              </div>
              <div style="color:#C1D1EB;font-size:11px;letter-spacing:1.5px;margin-top:6px;">
                BY NETEXIAL · PARTENAIRE SPÉCIALISTE DE LA PROTECTION AU TRAVAIL
              </div>
            </td>
          </tr>

          <!-- Bande accent orange -->
          <tr><td style="background-color:#FC6100;height:3px;line-height:3px;font-size:0;">&nbsp;</td></tr>

          <!-- Corps -->
          <tr>
            <td style="padding:36px 32px 28px 32px;">
              <h1 style="margin:0 0 16px 0;color:#141B4D;font-size:22px;
                         font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">
                {titre}
              </h1>
              <div style="color:#1C3775;font-size:15px;line-height:1.65;">
                {contenu_html}
              </div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color:#f4f6fb;padding:20px 32px;
                       border-top:1px solid #C1D1EB;color:#1C3775;font-size:12px;line-height:1.5;">
              {footer_extra}
              <div style="margin-top:8px;color:#98B2DD;">
                © {datetime.now().year} IDEA by NETEXIAL — Cet email a été généré automatiquement.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Envoi d'email via SMTP. Retourne True si OK."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("SMTP non configuré — email simulé.")
        logger.info(f"[EMAIL SIMULÉ] À: {to} | Sujet: {subject}")
        return True

    msg = MIMEMultipart("alternative")
    msg["From"]    = f"{SMTP_FROM_NAME} <{SMTP_FROM}>"
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.sendmail(SMTP_FROM, [to], msg.as_string())
        logger.info(f"Email envoyé à {to} — {subject}")
        return True
    except Exception as e:
        logger.error(f"Échec envoi email : {e}")
        return False


# ----------------------------------------------------------------------------
# ROUTES
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# ROUTE PRINCIPALE : sert le frontend (index.html)
# ----------------------------------------------------------------------------
@app.route("/")
def serve_index():
    """Sert le fichier index.html à la racine."""
    return send_from_directory(".", "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    """Health check pour Render / monitoring."""
    return jsonify({"status": "ok", "service": "netexial-api", "time": datetime.utcnow().isoformat()})


# ===========================================================================
# ROUTES PUBLIQUES (anonymes — pour le QR code des sites)
# ===========================================================================

@app.route("/api/public/site/<site_id>", methods=["GET"])
def public_site_info(site_id):
    """
    Retourne les infos d'un site + son entreprise (nom, logo, code).
    Utilisé par le formulaire public quand un opérateur scanne un QR.
    Aucune authentification requise.
    """
    try:
        res = supabase.table("sites").select(
            "id, nom, actif, entreprises(id, nom, code_entreprise, logo_url, actif, autoriser_sans_photo_desc)"
        ).eq("id", site_id).single().execute()

        if not res.data:
            return jsonify({"error": "Site introuvable"}), 404

        site = res.data
        ent = site.get("entreprises") or {}

        if not site.get("actif") or not ent.get("actif", True):
            return jsonify({"error": "Site inactif"}), 410

        return jsonify({
            "site": {
                "id": site["id"],
                "nom": site["nom"],
            },
            "entreprise": {
                "id": ent.get("id"),
                "nom": ent.get("nom"),
                "code": ent.get("code_entreprise"),
                "logo_url": ent.get("logo_url"),
                "sans_photo_desc": bool(ent.get("autoriser_sans_photo_desc")),
            },
        })
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Erreur public_site_info: {err_msg}")
        # Distinguer timeout réseau d'un site vraiment introuvable
        if any(k in err_msg.lower() for k in ("timeout", "winerror 10060", "winerror 10061", "connection", "refused", "unreachable")):
            return jsonify({"error": "Connexion à la base de données impossible. Vérifiez votre connexion Internet."}), 503
        return jsonify({"error": "Site introuvable"}), 404


@app.route("/api/public/ocr-porteur", methods=["POST"])
def public_ocr_porteur():
    """
    Reçoit une image (base64) capturée lors du scan d'un code-barres
    et utilise Gemini Vision pour extraire le nom du porteur s'il est
    inscrit à côté du code-barres.
    Body : { "image_b64": "...", "mime_type": "image/jpeg" }
    Réponse : { "name": "..." } ou { "name": null }
    """
    if not GEMINI_API_KEY:
        return jsonify({"name": None, "error": "Gemini non configuré"}), 200

    data = request.get_json(silent=True) or {}
    image_b64 = (data.get("image_b64") or "").strip()
    mime_type = data.get("mime_type") or "image/jpeg"

    if not image_b64:
        return jsonify({"name": None, "error": "Image manquante"}), 400

    # Nettoyer le préfixe data:image/...;base64, si présent
    if image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[-1]

    prompt = (
        "Cette photo montre l'étiquette ou un vêtement professionnel comportant "
        "un code-barres. À côté ou autour du code-barres, il y a souvent le nom "
        "du porteur (prénom, nom de famille, ou les deux), parfois imprimé ou brodé.\n\n"
        "TÂCHE : Identifie UNIQUEMENT le nom du porteur (personne) inscrit sur "
        "le vêtement. Ignore les noms d'entreprise, les modèles, les références.\n\n"
        "RÉPONDS STRICTEMENT en JSON, sans aucun autre texte, avec ce format :\n"
        '{"nom": "PRENOM NOM"} ou {"nom": null} si aucun nom n\'est lisible.\n\n'
        "Exemple : si tu vois 'JEAN DUPONT' brodé → {\"nom\": \"JEAN DUPONT\"}\n"
        "Exemple : si seul le code-barres est visible → {\"nom\": null}"
    )

    body = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))

        text = response_data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        name = parsed.get("nom")
        if isinstance(name, str):
            name = name.strip()
            if not name or name.lower() in {"null", "none", ""}:
                name = None
        return jsonify({"name": name})
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        logger.error(f"Gemini HTTP {e.code} : {err_body[:300]}")
        return jsonify({"name": None, "error": f"Gemini error {e.code}"}), 200
    except Exception as e:
        logger.error(f"Erreur OCR Gemini : {e}")
        return jsonify({"name": None, "error": str(e)}), 200


@app.route("/api/public/submit-request", methods=["POST"])
def public_submit_request():
    """
    Crée une demande anonyme côté serveur (avec la clé service-role, bypass RLS).
    Body : {
        "site_id": "...",
        "code_barre": "...",
        "nom_porteur": "...",
        "nom_porteur_source": "manual" | "auto" | "corrected",
        "description": "...",
        "photos": [ { "b64": "...", "content_type": "image/jpeg" }, ... ]
    }
    Réponse : { "ticket_number": "...", "id": "..." }
    """
    if not supabase:
        return jsonify({"error": "Backend non configuré"}), 500

    import base64

    data = request.get_json(silent=True) or {}
    site_id            = data.get("site_id")
    code_barre         = (data.get("code_barre") or "").strip().upper()
    nom_porteur        = (data.get("nom_porteur") or "").strip()
    nom_porteur_source = data.get("nom_porteur_source") or "manual"
    description        = (data.get("description") or "").strip()
    photos             = data.get("photos") or []

    if not site_id:        return jsonify({"error": "site_id requis"}), 400
    if not code_barre:     return jsonify({"error": "code_barre requis"}), 400
    if not nom_porteur:    return jsonify({"error": "nom_porteur requis"}), 400

    try:
        # 1. Récupérer l'entreprise + ses réglages depuis le site
        site_res = supabase.table("sites").select(
            "id, entreprise_id, actif, entreprises(autoriser_sans_photo_desc, auto_validation)"
        ).eq("id", site_id).single().execute()
        if not site_res.data:
            return jsonify({"error": "Site introuvable"}), 404
        if not site_res.data.get("actif", True):
            return jsonify({"error": "Site inactif"}), 410

        entreprise_id = site_res.data["entreprise_id"]
        ent_obj = site_res.data.get("entreprises") or {}
        sans_obligation = bool(ent_obj.get("autoriser_sans_photo_desc"))
        # Auto-validation : l'entreprise n'a rien à valider, la demande part
        # directement chez NETEXIAL (statut pre_validee).
        auto_validation = bool(ent_obj.get("auto_validation"))

        # Description obligatoire, sauf si l'entreprise autorise l'envoi sans
        if not description and not sans_obligation:
            return jsonify({"error": "description requise"}), 400

        # 2. Créer la demande
        req_res = supabase.table("repair_requests").insert({
            "site_id": site_id,
            "entreprise_id": entreprise_id,
            "code_barre": code_barre,
            "nom_porteur": nom_porteur,
            "nom_porteur_source": nom_porteur_source if nom_porteur_source in ("manual", "auto", "corrected") else "manual",
            "description": description,
            "statut": "pre_validee" if auto_validation else "en_attente",
        }).execute()

        if not req_res.data:
            return jsonify({"error": "Échec création"}), 500
        req = req_res.data[0]
        req_id = req["id"]

        # 3. Upload les photos
        for i, photo in enumerate(photos):
            try:
                b64 = photo.get("b64") or ""
                if b64.startswith("data:"):
                    b64 = b64.split(",", 1)[-1]
                content_type = photo.get("content_type") or "image/jpeg"
                photo_bytes = base64.b64decode(b64)
                filename = f"{req_id}/{int(datetime.utcnow().timestamp()*1000)}_{i}.jpg"

                supabase.storage.from_("repair-photos").upload(
                    filename,
                    photo_bytes,
                    {"content-type": content_type, "upsert": "false"},
                )
                public_url = supabase.storage.from_("repair-photos").get_public_url(filename)

                supabase.table("request_images").insert({
                    "request_id": req_id,
                    "storage_path": filename,
                    "url_publique": public_url,
                    "filename": f"photo_{i}.jpg",
                    "taille_octets": len(photo_bytes),
                    "ordre": i,
                }).execute()
            except Exception as e_photo:
                logger.error(f"Erreur upload photo {i} : {e_photo}")
                continue

        # 4. Notification email (best-effort, ne bloque pas si échec)
        try:
            if auto_validation:
                # La demande est déjà pré-validée → on notifie directement
                # NETEXIAL (admin), pas l'entreprise (qui n'a rien à valider).
                full = supabase.table("repair_requests").select(
                    "*, entreprises(nom, email_contact, code_client_sis), sites(id, nom, code_client_livre)"
                ).eq("id", req_id).single().execute()
                if full.data:
                    _notify_admin_validation(full.data, None)
            else:
                _notify_submission_internal(req_id)
        except Exception as e_notif:
            logger.error(f"Erreur notif submission : {e_notif}")

        return jsonify({
            "id": req_id,
            "ticket_number": req.get("ticket_number"),
            "success": True,
        })

    except Exception as e:
        logger.error(f"Erreur public_submit_request : {e}")
        return jsonify({"error": str(e)}), 500


def _notify_submission_internal(request_id):
    """Logique d'envoi de notification refactorée."""
    res = supabase.table("repair_requests").select(
        "*, entreprises(nom, email_contact, desactiver_notifs_client), sites(nom, id)"
    ).eq("id", request_id).single().execute()
    if not res.data:
        return
    req = res.data
    ent = req.get("entreprises") or {}
    site = req.get("sites") or {}

    # Si l'entreprise a désactivé les notifications par demande,
    # on n'envoie aucun email aux clients pour cette soumission.
    if ent.get("desactiver_notifs_client"):
        logger.info(
            f"Notifications client désactivées pour l'entreprise "
            f"{ent.get('nom') or req.get('entreprise_id')} — envoi ignoré (demande {request_id})."
        )
        return

    # Nombre de demandes en attente pour l'entreprise (inclut celle qui vient d'arriver)
    pending = 0
    try:
        cnt = supabase.table("repair_requests").select("id", count="exact").eq(
            "entreprise_id", req.get("entreprise_id")
        ).eq("statut", "en_attente").execute()
        pending = cnt.count or 0
    except Exception as e:
        logger.error(f"Erreur comptage demandes en attente : {e}")

    users_to_notify = set()
    if site.get("id"):
        assigned = supabase.table("user_sites").select(
            "user_id, users!inner(email, nom_complet, role, entreprise_id)"
        ).eq("site_id", site["id"]).execute()
        for row in (assigned.data or []):
            u = row.get("users") or {}
            if u.get("role") == "client" and u.get("entreprise_id") == req.get("entreprise_id"):
                users_to_notify.add(u.get("email"))

    all_users = supabase.table("users").select(
        "id, email, nom_complet, role, entreprise_id"
    ).eq("entreprise_id", req.get("entreprise_id")).eq("role", "client").execute()
    for u in (all_users.data or []):
        user_sites = supabase.table("user_sites").select("site_id").eq("user_id", u["id"]).execute()
        if not user_sites.data:
            users_to_notify.add(u["email"])

    contenu = f"""
    <p style="color:#1C3775; margin-bottom:24px;">Bonjour,</p>
    <p style="color:#1C3775;">Une nouvelle demande de réparation vient d'être soumise depuis le site
    <strong>{site.get('nom') or '—'}</strong> ({ent.get('nom') or '—'}).</p>
    <table style="width:100%; margin-top:20px; font-family:Arial; font-size:14px; border-collapse:collapse;">
        <tr style="background:#f5f7fb;"><td style="padding:10px 16px; color:#65748b; width:35%;">N° de ticket</td><td style="padding:10px 16px; font-weight:700; color:#FC6100;">{req.get('ticket_number','—')}</td></tr>
        <tr><td style="padding:10px 16px; color:#65748b;">Porteur</td><td style="padding:10px 16px;">{req.get('nom_porteur') or '—'}</td></tr>
        <tr style="background:#f5f7fb;"><td style="padding:10px 16px; color:#65748b;">Code-barres</td><td style="padding:10px 16px; font-family:monospace;">{req.get('code_barre') or '—'}</td></tr>
        <tr><td style="padding:10px 16px; color:#65748b;">Site</td><td style="padding:10px 16px;">{site.get('nom') or '—'}</td></tr>
    </table>
    <p style="color:#1C3775; margin-top:24px;">Vous avez actuellement
        <strong style="color:#FC6100;">{pending}</strong>
        demande{'s' if pending > 1 else ''} en attente de validation.</p>
    <div style="text-align:center; margin:28px 0 4px;">
        <a href="{APP_URL}" style="display:inline-block; background-color:#FC6100; color:#ffffff;
           text-decoration:none; font-weight:700; font-size:15px; padding:14px 34px; border-radius:8px;">
            Se connecter à IDEA
        </a>
    </div>
    <p style="color:#65748b; font-size:13px; text-align:center; margin-top:10px;">
        ou copiez ce lien : <a href="{APP_URL}" style="color:#1C3775;">{APP_URL}</a>
    </p>
    """
    html = _email_template("Nouvelle demande à valider", contenu)
    subject = f"[IDEA] Demande à valider — {req.get('ticket_number','')}"

    for email in users_to_notify:
        if email:
            send_email(email, subject, html)


# ===========================================================================
# WORKFLOW DE VALIDATION : User entreprise → admin NETEXIAL
# ===========================================================================

@app.route("/api/requests/<request_id>/pre-validate", methods=["POST"])
def pre_validate_request(request_id):
    """
    Pré-validation par un user entreprise.
    Passe le statut de 'en_attente' → 'pre_validee' puis notifie l'admin NETEXIAL.
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    profile = get_user_profile(payload["sub"])
    if not profile:
        return jsonify({"error": "Profil introuvable"}), 403

    # Récupérer la demande
    res = supabase.table("repair_requests").select(
        "*, entreprises(nom, email_contact, code_client_sis), sites(id, nom, code_client_livre)"
    ).eq("id", request_id).single().execute()
    if not res.data:
        return jsonify({"error": "Demande introuvable"}), 404

    req = res.data

    # Vérification d'autorisation : admin OK, ou user entreprise de la bonne entreprise
    if profile.get("role") == "admin":
        pass  # admin peut tout faire
    elif profile.get("role") == "client":
        if profile.get("entreprise_id") != req.get("entreprise_id"):
            return jsonify({"error": "Accès refusé"}), 403
        # Vérifier accès au site si user multi-sites restreint
        user_sites = supabase.table("user_sites").select("site_id").eq("user_id", payload["sub"]).execute()
        if user_sites.data:  # si l'user a des restrictions de sites
            allowed = {row["site_id"] for row in user_sites.data}
            if req.get("site_id") not in allowed:
                return jsonify({"error": "Accès refusé à ce site"}), 403
    else:
        return jsonify({"error": "Rôle non autorisé"}), 403

    # Vérifier que le statut actuel est bien "en_attente"
    if req.get("statut") != "en_attente":
        return jsonify({"error": f"Cette demande est déjà au statut '{req.get('statut')}'"}), 400

    # Update
    upd = supabase.table("repair_requests").update({
        "statut": "pre_validee",
        "traite_par": payload["sub"],
    }).eq("id", request_id).execute()

    # Notifier l'admin NETEXIAL
    try:
        _notify_admin_validation(req, profile)
    except Exception as e:
        logger.error(f"Erreur notif admin : {e}")

    return jsonify({"success": True})


@app.route("/api/requests/<request_id>/refuse-by-entreprise", methods=["POST"])
def refuse_request_by_entreprise(request_id):
    """
    Refus par un user entreprise (statut 'en_attente' → 'refusee', définitif).
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    profile = get_user_profile(payload["sub"])
    if not profile:
        return jsonify({"error": "Profil introuvable"}), 403

    data = request.get_json(silent=True) or {}
    motif = (data.get("motif") or "").strip()

    res = supabase.table("repair_requests").select("*").eq("id", request_id).single().execute()
    if not res.data:
        return jsonify({"error": "Demande introuvable"}), 404
    req = res.data

    # Autorisation
    if profile.get("role") == "admin":
        pass
    elif profile.get("role") == "client":
        if profile.get("entreprise_id") != req.get("entreprise_id"):
            return jsonify({"error": "Accès refusé"}), 403
    else:
        return jsonify({"error": "Rôle non autorisé"}), 403

    if req.get("statut") != "en_attente":
        return jsonify({"error": f"Cette demande est déjà au statut '{req.get('statut')}'"}), 400

    supabase.table("repair_requests").update({
        "statut": "refusee",
        "traite_par": payload["sub"],
        "raison_refus": motif or None,
    }).eq("id", request_id).execute()

    return jsonify({"success": True})


@app.route("/api/requests/<request_id>/reopen", methods=["POST"])
def reopen_request(request_id):
    """
    Rouvre une demande refusée par erreur → la remet en 'en_attente'.
    Accessible par l'admin ou un user entreprise de la bonne entreprise.
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    profile = get_user_profile(payload["sub"])
    if not profile:
        return jsonify({"error": "Profil introuvable"}), 403

    res = supabase.table("repair_requests").select("*").eq("id", request_id).single().execute()
    if not res.data:
        return jsonify({"error": "Demande introuvable"}), 404
    req = res.data

    # Autorisation
    if profile.get("role") == "admin":
        pass
    elif profile.get("role") == "client":
        if profile.get("entreprise_id") != req.get("entreprise_id"):
            return jsonify({"error": "Accès refusé"}), 403
    else:
        return jsonify({"error": "Rôle non autorisé"}), 403

    # On ne rouvre que ce qui est refusé
    if req.get("statut") != "refusee":
        return jsonify({"error": f"Seules les demandes refusées peuvent être rouvertes (statut actuel : '{req.get('statut')}')"}), 400

    supabase.table("repair_requests").update({
        "statut": "en_attente",
        "raison_refus": None,
    }).eq("id", request_id).execute()

    return jsonify({"success": True})


def _notify_admin_validation(req, validator_profile=None):
    """Email à l'admin référent de l'entreprise quand une demande passe en pre_validee.

    Destinataire : l'admin défini sur entreprises.admin_referent_id (source unique).
    C'est le même admin qui voit la demande dans son espace.
    Fallback : si l'entreprise n'a pas de référent, on notifie l'admin de base (BASE_ADMIN_ID).
    """
    ent = req.get("entreprises") or {}
    site = req.get("sites") or {}

    desc_raw = (req.get("description") or "").strip()
    desc_html = (desc_raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")) or "—"

    contenu = f"""
    <p style="color:#1C3775; margin-bottom:24px;">Bonjour,</p>
    <p style="color:#1C3775;">Une demande a été <strong>pré-validée</strong> par un utilisateur entreprise et attend votre décision.</p>
    <table style="width:100%; margin-top:20px; font-family:Arial; font-size:14px; border-collapse:collapse;">
        <tr style="background:#f5f7fb;"><td style="padding:10px 16px; color:#65748b; width:35%;">N° de ticket</td><td style="padding:10px 16px; font-weight:700; color:#FC6100;">{req.get('ticket_number','—')}</td></tr>
        <tr><td style="padding:10px 16px; color:#65748b;">Entreprise</td><td style="padding:10px 16px;">{ent.get('nom') or '—'}</td></tr>
        <tr style="background:#f5f7fb;"><td style="padding:10px 16px; color:#65748b;">Code client SIS</td><td style="padding:10px 16px; font-family:monospace;">{ent.get('code_client_sis') or '—'}</td></tr>
        <tr><td style="padding:10px 16px; color:#65748b;">Site</td><td style="padding:10px 16px;">{site.get('nom') or '—'}</td></tr>
        <tr style="background:#f5f7fb;"><td style="padding:10px 16px; color:#65748b;">Code client livré (ATLAS)</td><td style="padding:10px 16px; font-family:monospace;">{site.get('code_client_livre') or '—'}</td></tr>
        <tr><td style="padding:10px 16px; color:#65748b;">Porteur</td><td style="padding:10px 16px;">{req.get('nom_porteur') or '—'}</td></tr>
        <tr style="background:#f5f7fb;"><td style="padding:10px 16px; color:#65748b;">Code-barres</td><td style="padding:10px 16px; font-family:monospace;">{req.get('code_barre') or '—'}</td></tr>
        <tr><td style="padding:10px 16px; color:#65748b; vertical-align:top;">Description</td><td style="padding:10px 16px; color:#1C3775;">{desc_html}</td></tr>
    </table>
    <p style="color:#1C3775; margin-top:24px;">Connectez-vous à l'espace IDEA pour accepter ou refuser cette demande.</p>
    """
    html = _email_template("Demande pré-validée — Action requise", contenu)
    subject = f"[IDEA] À traiter — {req.get('ticket_number','')}"

    # Destinataire unique : l'admin référent de l'ENTREPRISE concernée.
    # C'est le même admin qui voit la demande dans son espace.
    recipients = set()
    referent_id = None
    entreprise_id = req.get("entreprise_id")
    if entreprise_id:
        try:
            er = supabase.table("entreprises").select("admin_referent_id").eq("id", entreprise_id).single().execute()
            referent_id = (er.data or {}).get("admin_referent_id")
        except Exception as e:
            logger.error(f"Erreur résolution admin référent entreprise : {e}")

    if referent_id:
        try:
            ar = supabase.table("users").select("email").eq("id", referent_id).eq("role", "admin").single().execute()
            if ar.data and ar.data.get("email"):
                recipients.add(ar.data["email"])
        except Exception as e:
            logger.error(f"Erreur résolution email admin référent : {e}")

    # Filet de sécurité : si aucun référent n'est défini sur l'entreprise, on notifie l'admin de base
    if not recipients and BASE_ADMIN_ID:
        try:
            ab = supabase.table("users").select("email").eq("id", BASE_ADMIN_ID).single().execute()
            if ab.data and ab.data.get("email"):
                recipients.add(ab.data["email"])
        except Exception as e:
            logger.error(f"Erreur résolution admin de base : {e}")

    for email in recipients:
        send_email(email, subject, html)


@app.route("/api/public/notify-submission", methods=["POST"])
def public_notify_submission():
    """OBSOLÈTE — conservé désactivé. La notification est désormais faite
    directement par /api/public/submit-request via _notify_submission_internal()."""
    return jsonify({"error": "Endpoint obsolète"}), 410


# ===========================================================================
# ROUTES AUTHENTIFIÉES
# ===========================================================================

# --- Anti-doublon ----------------------------------------------------------
@app.route("/api/check-duplicate", methods=["POST"])
def check_duplicate():
    """
    Vérifie si une demande similaire (même entreprise + même code-barres) existe
    déjà dans la fenêtre de DOUBLON_WINDOW_HOURS heures.
    Body : { "code_barre": "..." }
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    user_profile = get_user_profile(payload["sub"])
    if not user_profile:
        return jsonify({"error": "Profil utilisateur introuvable"}), 403

    data = request.get_json(silent=True) or {}
    code_barre = (data.get("code_barre") or "").strip()
    if not code_barre:
        return jsonify({"duplicate": False})

    entreprise_id = user_profile.get("entreprise_id")
    if not entreprise_id:
        return jsonify({"duplicate": False})

    res = supabase.rpc("check_duplicate_request", {
        "p_entreprise_id": entreprise_id,
        "p_code_barre": code_barre,
        "p_window_hours": DOUBLON_WINDOW_HOURS,
    }).execute()

    if res.data and len(res.data) > 0:
        return jsonify({"duplicate": True, "existing": res.data[0]})
    return jsonify({"duplicate": False})


# --- Confirmation client ---------------------------------------------------
@app.route("/api/send-confirmation", methods=["POST"])
def send_confirmation():
    """
    Envoie l'email de confirmation au client après création d'une demande.
    Body : { "request_id": "..." }
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    request_id = data.get("request_id")
    if not request_id:
        return jsonify({"error": "request_id manquant"}), 400

    # Récupérer la demande + le client
    res = supabase.table("repair_requests").select(
        "*, entreprises(nom, email_contact)"
    ).eq("id", request_id).single().execute()

    if not res.data:
        return jsonify({"error": "Demande introuvable"}), 404

    req = res.data
    email_dest = req["entreprises"]["email_contact"] if req.get("entreprises") else None
    if not email_dest:
        # Fallback : email de l'utilisateur courant
        user_profile = get_user_profile(payload["sub"])
        email_dest = user_profile.get("email") if user_profile else None

    if not email_dest:
        return jsonify({"error": "Aucun email destinataire"}), 400

    contenu = f"""
        <p>Bonjour,</p>
        <p>Nous accusons réception de votre demande de réparation. Elle est désormais en cours de traitement par nos équipes.</p>

        <table style="width:100%;border-collapse:collapse;margin:20px 0;background:#f4f6fb;border-radius:8px;overflow:hidden;">
            <tr>
                <td style="padding:14px 18px;color:#1C3775;font-size:13px;width:40%;">N° de ticket</td>
                <td style="padding:14px 18px;color:#141B4D;font-size:15px;font-weight:700;">{req['ticket_number']}</td>
            </tr>
            <tr style="background:#ffffff;">
                <td style="padding:14px 18px;color:#1C3775;font-size:13px;">Date de soumission</td>
                <td style="padding:14px 18px;color:#141B4D;font-size:14px;">{datetime.fromisoformat(req['created_at'].replace('Z','+00:00')).strftime('%d/%m/%Y à %H:%M')}</td>
            </tr>
            <tr>
                <td style="padding:14px 18px;color:#1C3775;font-size:13px;">Référence produit</td>
                <td style="padding:14px 18px;color:#141B4D;font-size:14px;">{req.get('reference_produit') or '—'}</td>
            </tr>
            <tr style="background:#ffffff;">
                <td style="padding:14px 18px;color:#1C3775;font-size:13px;vertical-align:top;">Description</td>
                <td style="padding:14px 18px;color:#141B4D;font-size:14px;">{(req.get('description') or '')[:300]}</td>
            </tr>
        </table>

        <p>Vous serez notifié(e) par email dès qu'une décision aura été prise sur votre demande.</p>
        <p style="margin-top:24px;color:#1C3775;">Cordialement,<br><strong>L'équipe IDEA</strong></p>
    """
    html = _email_template(
        "Votre demande a bien été reçue",
        contenu,
        footer_extra=f"Conservez ce numéro de ticket pour tout suivi : <strong>{req['ticket_number']}</strong>"
    )

    ok = send_email(email_dest, f"[IDEA] Confirmation de votre demande {req['ticket_number']}", html)

    # Journalisation
    supabase.table("notifications").insert({
        "request_id": request_id,
        "user_id": payload["sub"],
        "type": "email_confirm",
        "destinataire": email_dest,
        "sujet": f"Confirmation {req['ticket_number']}",
        "contenu": "Email de confirmation client",
        "statut": "envoye" if ok else "echec",
    }).execute()

    return jsonify({"sent": ok, "to": email_dest})


# --- Notification service interne après validation -------------------------
@app.route("/api/notify-service", methods=["POST"])
def notify_service():
    """OBSOLÈTE — l'ancien workflow de notification service interne n'est plus utilisé.
    Conservé désactivé pour compatibilité."""
    return jsonify({"error": "Endpoint obsolète"}), 410


# --- Export PDF d'une demande ----------------------------------------------
@app.route("/api/export-pdf/<request_id>", methods=["GET"])
def export_pdf(request_id):
    """
    Génère un PDF récapitulatif d'une demande.
    Nécessite reportlab.
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        )
        from reportlab.lib.enums import TA_LEFT
    except ImportError:
        return jsonify({"error": "reportlab non installé"}), 500

    res = supabase.table("repair_requests").select(
        "*, entreprises(nom, code_entreprise, email_contact)"
    ).eq("id", request_id).single().execute()

    if not res.data:
        return jsonify({"error": "Demande introuvable"}), 404
    req = res.data

    # Vérifier accès : admin ou propriétaire
    user_profile = get_user_profile(payload["sub"])
    if user_profile["role"] != "admin" and user_profile.get("entreprise_id") != req["entreprise_id"]:
        return jsonify({"error": "Accès refusé"}), 403

    images_res = supabase.table("request_images").select("url_publique") \
        .eq("request_id", request_id).execute()
    images = images_res.data or []

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm
    )

    NAVY   = HexColor("#141B4D")
    ORANGE = HexColor("#FC6100")
    LIGHT  = HexColor("#C1D1EB")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleNX", parent=styles["Title"],
        textColor=NAVY, fontSize=22, alignment=TA_LEFT,
        spaceAfter=6, leading=26
    )
    sub_style = ParagraphStyle(
        "SubNX", parent=styles["Normal"],
        textColor=ORANGE, fontSize=10, spaceAfter=20
    )
    label_style = ParagraphStyle(
        "Label", parent=styles["Normal"],
        textColor=HexColor("#1C3775"), fontSize=9
    )
    value_style = ParagraphStyle(
        "Value", parent=styles["Normal"],
        textColor=NAVY, fontSize=11
    )

    story = []
    story.append(Paragraph("IDEA", title_style))
    story.append(Paragraph("DEMANDE DE RÉPARATION", sub_style))
    story.append(Spacer(1, 6))

    # Bandeau ticket
    ticket_table = Table(
        [[Paragraph("N° TICKET", label_style),
          Paragraph(f"<b>{req['ticket_number']}</b>", value_style)]],
        colWidths=[4 * cm, 12 * cm]
    )
    ticket_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f4f6fb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBEFORE", (0, 0), (0, -1), 3, ORANGE),
    ]))
    story.append(ticket_table)
    story.append(Spacer(1, 16))

    # Détails
    statut_libelle = {
        "en_attente": "En attente",
        "acceptee":   "Acceptée",
        "refusee":    "Refusée",
        "cloturee":   "Clôturée",
    }.get(req["statut"], req["statut"])

    rows = [
        ["Entreprise",          req["entreprises"]["nom"] if req.get("entreprises") else "—"],
        ["Code entreprise",     req["entreprises"]["code_entreprise"] if req.get("entreprises") else "—"],
        ["Date soumission", datetime.fromisoformat(req["created_at"].replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")],
        ["Statut",          statut_libelle],
        ["Code-barres",     req.get("code_barre") or "—"],
        ["Référence",       req.get("reference_produit") or "—"],
        ["N° de série",     req.get("numero_serie") or "—"],
        ["Type",            req.get("type_equipement") or "—"],
    ]
    detail_table = Table(rows, colWidths=[5 * cm, 11 * cm])
    detail_table.setStyle(TableStyle([
        ("TEXTCOLOR",   (0, 0), (0, -1), HexColor("#1C3775")),
        ("TEXTCOLOR",   (1, 0), (1, -1), NAVY),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",  (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW",   (0, 0), (-1, -1), 0.5, LIGHT),
    ]))
    story.append(detail_table)
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>DESCRIPTION DU PROBLÈME</b>", label_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(req.get("description") or "—", value_style))

    if req.get("raison_refus"):
        story.append(Spacer(1, 16))
        story.append(Paragraph("<b>MOTIF DE REFUS</b>", label_style))
        story.append(Spacer(1, 6))
        story.append(Paragraph(req["raison_refus"], value_style))

    if images:
        story.append(Spacer(1, 16))
        story.append(Paragraph(f"<b>PHOTOS JOINTES</b> ({len(images)})", label_style))
        story.append(Spacer(1, 6))
        for img in images:
            story.append(Paragraph(
                f'<link href="{img["url_publique"]}" color="#FC6100">{img["url_publique"]}</link>',
                value_style
            ))

    doc.build(story)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        download_name=f"IDEA_{req['ticket_number']}.pdf",
        as_attachment=True
    )


# --- Création d'un compte utilisateur lié à un client (admin only) ---------
@app.route("/api/admin/create-user", methods=["POST"])
def create_user():
    """
    Crée un compte auth.users + un profil dans public.users, lié à une entreprise.
    Body : {
        "email": "...", "password": "...", "nom_complet": "...",
        "role": "client" (= utilisateur d'entreprise) | "admin",
        "entreprise_id": "...",
        "site_ids": ["uuid1", "uuid2", ...]  // optionnel ; [] ou null = accès à tous les sites
    }
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    user_profile = get_user_profile(payload["sub"])
    if not user_profile or user_profile.get("role") != "admin":
        return jsonify({"error": "Accès admin requis"}), 403

    data = request.get_json(silent=True) or {}
    email          = data.get("email")
    password       = data.get("password")
    nom            = data.get("nom_complet", "")
    role           = data.get("role", "client")
    entreprise_id  = data.get("entreprise_id")
    site_ids       = data.get("site_ids") or []
    admin_referent_id = data.get("admin_referent_id")

    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400
    if role == "client" and not entreprise_id:
        return jsonify({"error": "entreprise_id requis pour un utilisateur d'entreprise"}), 400
    if role == "client" and not admin_referent_id:
        return jsonify({"error": "Un admin référent est requis pour un utilisateur d'entreprise"}), 400

    try:
        # Création du compte auth (utilise l'API admin)
        auth_res = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
        })
        new_user_id = auth_res.user.id

        # Création du profil
        supabase.table("users").insert({
            "id": new_user_id,
            "email": email,
            "nom_complet": nom,
            "role": role,
            "entreprise_id": entreprise_id if role == "client" else None,
            "admin_referent_id": admin_referent_id if role == "client" else None,
        }).execute()

        # Assigner les sites (si role = client/entreprise_user)
        if role == "client" and site_ids:
            user_sites_rows = [
                {"user_id": new_user_id, "site_id": sid}
                for sid in site_ids
            ]
            supabase.table("user_sites").insert(user_sites_rows).execute()

        return jsonify({"success": True, "user_id": new_user_id})
    except Exception as e:
        logger.error(f"Erreur création utilisateur : {e}")
        return jsonify({"error": str(e)}), 500


# --- Mettre à jour les sites assignés à un utilisateur ---------------------
@app.route("/api/admin/update-user-sites", methods=["POST"])
def update_user_sites():
    """
    Met à jour les sites assignés à un utilisateur (remplace l'ensemble).
    Body : { "user_id": "...", "site_ids": ["uuid1", "uuid2", ...] }
    Un tableau vide signifie : accès à tous les sites de son entreprise (pas de restriction).
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    user_profile = get_user_profile(payload["sub"])
    if not user_profile or user_profile.get("role") != "admin":
        return jsonify({"error": "Accès admin requis"}), 403

    data = request.get_json(silent=True) or {}
    user_id   = data.get("user_id")
    site_ids  = data.get("site_ids") or []

    if not user_id:
        return jsonify({"error": "user_id requis"}), 400

    try:
        # Supprime les anciennes assignations
        supabase.table("user_sites").delete().eq("user_id", user_id).execute()
        # Insère les nouvelles
        if site_ids:
            supabase.table("user_sites").insert([
                {"user_id": user_id, "site_id": sid} for sid in site_ids
            ]).execute()
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Erreur update user_sites : {e}")
        return jsonify({"error": str(e)}), 500


# --- Suppression d'un utilisateur (admin only) ------------------------------
@app.route("/api/admin/delete-user/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    """
    Supprime un compte (auth + profil). Admin only.
    Le profil est supprimé automatiquement via ON DELETE CASCADE.
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    user_profile = get_user_profile(payload["sub"])
    if not user_profile or user_profile.get("role") != "admin":
        return jsonify({"error": "Accès admin requis"}), 403

    # Empêcher la suppression de soi-même (par sécurité)
    if user_id == payload["sub"]:
        return jsonify({"error": "Vous ne pouvez pas supprimer votre propre compte"}), 400

    try:
        supabase.auth.admin.delete_user(user_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Erreur suppression utilisateur : {e}")
        return jsonify({"error": str(e)}), 500


# --- Réinitialisation de mot de passe (admin only) --------------------------
@app.route("/api/admin/reset-password", methods=["POST"])
def reset_password():
    """
    Définit un nouveau mot de passe pour un user. Admin only.
    Body : { "user_id": "...", "new_password": "..." }
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    user_profile = get_user_profile(payload["sub"])
    if not user_profile or user_profile.get("role") != "admin":
        return jsonify({"error": "Accès admin requis"}), 403

    data = request.get_json(silent=True) or {}
    target_id = data.get("user_id")
    new_password = data.get("new_password")

    if not target_id or not new_password:
        return jsonify({"error": "user_id et new_password requis"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "Le mot de passe doit faire au moins 6 caractères"}), 400

    try:
        supabase.auth.admin.update_user_by_id(target_id, {"password": new_password})
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Erreur reset password : {e}")
        return jsonify({"error": str(e)}), 500


# --- Mise à jour des infos d'un utilisateur (admin only) --------------------
@app.route("/api/admin/update-user", methods=["POST"])
def update_user():
    """
    Met à jour les infos d'un utilisateur : email, nom, rôle, entreprise, sites.
    Ne touche PAS au mot de passe (géré par /admin/reset-password).
    Body : {
        "user_id": "...", "email": "...", "nom_complet": "...",
        "role": "client" | "admin", "entreprise_id": "...",
        "site_ids": ["uuid1", ...]   // [] ou null = accès à tous les sites
    }
    """
    payload, err = verify_jwt(request)
    if err:
        return err

    user_profile = get_user_profile(payload["sub"])
    if not user_profile or user_profile.get("role") != "admin":
        return jsonify({"error": "Accès admin requis"}), 403

    data = request.get_json(silent=True) or {}
    target_id     = data.get("user_id")
    email         = (data.get("email") or "").strip()
    nom           = data.get("nom_complet", "")
    role          = data.get("role", "client")
    entreprise_id = data.get("entreprise_id")
    site_ids      = data.get("site_ids") or []
    admin_referent_id = data.get("admin_referent_id")

    if not target_id:
        return jsonify({"error": "user_id requis"}), 400
    if not email or "@" not in email:
        return jsonify({"error": "Email invalide"}), 400
    if role not in ("client", "admin"):
        return jsonify({"error": "Rôle invalide"}), 400
    if role == "client" and not entreprise_id:
        return jsonify({"error": "entreprise_id requis pour un utilisateur d'entreprise"}), 400
    if role == "client" and not admin_referent_id:
        return jsonify({"error": "Un admin référent est requis pour un utilisateur d'entreprise"}), 400

    # Empêcher un admin de se rétrograder lui-même (éviter de se verrouiller dehors)
    if target_id == payload["sub"] and role != "admin":
        return jsonify({"error": "Vous ne pouvez pas retirer votre propre rôle administrateur"}), 400

    try:
        # 1) Email côté auth (email_confirm pour éviter un mail de confirmation)
        supabase.auth.admin.update_user_by_id(
            target_id, {"email": email, "email_confirm": True}
        )

        # 2) Profil public.users
        supabase.table("users").update({
            "email": email,
            "nom_complet": nom,
            "role": role,
            "entreprise_id": entreprise_id if role == "client" else None,
            "admin_referent_id": admin_referent_id if role == "client" else None,
        }).eq("id", target_id).execute()

        # 3) Sites assignés (on remplace l'ensemble)
        supabase.table("user_sites").delete().eq("user_id", target_id).execute()
        if role == "client" and site_ids:
            supabase.table("user_sites").insert([
                {"user_id": target_id, "site_id": sid} for sid in site_ids
            ]).execute()

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Erreur mise à jour utilisateur : {e}")
        return jsonify({"error": str(e)}), 500


# --- Suppression EN CASCADE d'une entreprise (admin only) -------------------
@app.route("/api/admin/delete-entreprise-cascade", methods=["POST"])
def delete_entreprise_cascade():
    """
    Supprime une entreprise et TOUTES ses données dépendantes :
      - demandes (repair_requests) + photos (request_images + storage)
      - sites
      - utilisateurs (public.users + user_sites + comptes auth.users)
    Action IRRÉVERSIBLE. Admin only.
    """
    payload, err = verify_jwt(request)
    if err:
        return err
    me = get_user_profile(payload["sub"])
    if not me or me.get("role") != "admin":
        return jsonify({"error": "Accès admin requis"}), 403

    data = request.get_json(silent=True) or {}
    ent_id = data.get("entreprise_id")
    if not ent_id:
        return jsonify({"error": "entreprise_id requis"}), 400

    try:
        # Vérifier que l'entreprise existe
        ent_res = supabase.table("entreprises").select("id, nom").eq("id", ent_id).single().execute()
        if not ent_res.data:
            return jsonify({"error": "Entreprise introuvable"}), 404
        ent_nom = ent_res.data.get("nom")

        # 1) Collecter les IDs des demandes et des utilisateurs
        req_res = supabase.table("repair_requests").select("id").eq("entreprise_id", ent_id).execute()
        request_ids = [r["id"] for r in (req_res.data or [])]

        users_res = supabase.table("users").select("id").eq("entreprise_id", ent_id).execute()
        user_ids = [u["id"] for u in (users_res.data or [])]

        # 2) Photos : storage + table
        if request_ids:
            imgs_res = supabase.table("request_images").select("storage_path") \
                .in_("request_id", request_ids).execute()
            paths = [i["storage_path"] for i in (imgs_res.data or []) if i.get("storage_path")]
            if paths:
                try:
                    supabase.storage.from_("repair-photos").remove(paths)
                except Exception as e:
                    logger.warning(f"Suppression storage partielle : {e}")
            supabase.table("request_images").delete().in_("request_id", request_ids).execute()

        # 3) Demandes
        supabase.table("repair_requests").delete().eq("entreprise_id", ent_id).execute()

        # 4) Liaisons user_sites
        if user_ids:
            supabase.table("user_sites").delete().in_("user_id", user_ids).execute()

        # 5) Profils utilisateurs (public.users)
        supabase.table("users").delete().eq("entreprise_id", ent_id).execute()

        # 6) Sites
        supabase.table("sites").delete().eq("entreprise_id", ent_id).execute()

        # 7) Entreprise
        supabase.table("entreprises").delete().eq("id", ent_id).execute()

        # 8) Comptes Supabase Auth (best effort : on ne fait pas planter la
        #    suppression si l'un échoue, mais on log)
        for uid in user_ids:
            try:
                supabase.auth.admin.delete_user(uid)
            except Exception as e:
                logger.warning(f"Suppression auth user {uid} echouee : {e}")

        return jsonify({
            "success": True,
            "deleted": {
                "entreprise": ent_nom,
                "demandes": len(request_ids),
                "utilisateurs": len(user_ids),
            }
        })
    except Exception as e:
        logger.error(f"Erreur cascade delete entreprise : {e}")
        return jsonify({"error": str(e)}), 500


# --- Suppression EN CASCADE d'un site (admin only) --------------------------
@app.route("/api/admin/delete-site-cascade", methods=["POST"])
def delete_site_cascade():
    """
    Supprime un site et toutes ses demandes (+ photos).
    Les utilisateurs entreprise restent (seule leur liaison à ce site disparait).
    Action IRRÉVERSIBLE. Admin only.
    """
    payload, err = verify_jwt(request)
    if err:
        return err
    me = get_user_profile(payload["sub"])
    if not me or me.get("role") != "admin":
        return jsonify({"error": "Accès admin requis"}), 403

    data = request.get_json(silent=True) or {}
    site_id = data.get("site_id")
    if not site_id:
        return jsonify({"error": "site_id requis"}), 400

    try:
        site_res = supabase.table("sites").select("id, nom").eq("id", site_id).single().execute()
        if not site_res.data:
            return jsonify({"error": "Site introuvable"}), 404
        site_nom = site_res.data.get("nom")

        # 1) Demandes du site
        req_res = supabase.table("repair_requests").select("id").eq("site_id", site_id).execute()
        request_ids = [r["id"] for r in (req_res.data or [])]

        # 2) Photos
        if request_ids:
            imgs_res = supabase.table("request_images").select("storage_path") \
                .in_("request_id", request_ids).execute()
            paths = [i["storage_path"] for i in (imgs_res.data or []) if i.get("storage_path")]
            if paths:
                try:
                    supabase.storage.from_("repair-photos").remove(paths)
                except Exception as e:
                    logger.warning(f"Suppression storage partielle : {e}")
            supabase.table("request_images").delete().in_("request_id", request_ids).execute()

        # 3) Demandes
        supabase.table("repair_requests").delete().eq("site_id", site_id).execute()

        # 4) Liaisons utilisateurs ↔ site
        supabase.table("user_sites").delete().eq("site_id", site_id).execute()

        # 5) Site
        supabase.table("sites").delete().eq("id", site_id).execute()

        return jsonify({
            "success": True,
            "deleted": {
                "site": site_nom,
                "demandes": len(request_ids),
            }
        })
    except Exception as e:
        logger.error(f"Erreur cascade delete site : {e}")
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------------------------------
# LANCEMENT
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    logger.info(f"Démarrage NETEXIAL API sur le port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)