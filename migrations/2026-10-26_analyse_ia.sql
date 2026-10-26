-- ============================================================
-- Avis de l'IA sur les photos du défaut (visible NETEXIAL uniquement)
-- Table séparée : les clients n'y ont jamais accès (RLS admin seulement).
-- À exécuter AVANT de déployer le nouveau app.py / index.html.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.repair_ai_analyses (
    request_id             uuid PRIMARY KEY REFERENCES public.repair_requests(id) ON DELETE CASCADE,
    type_reparation        text,      -- code CP/CR/CB choisi par l'IA (NULL = incertain)
    emplacement            text,      -- code emplacement choisi par l'IA (NULL = incertain)
    confiance_type         smallint,  -- 0 à 100
    confiance_emplacement  smallint,  -- 0 à 100
    explication            text,      -- courte justification de l'IA
    modele                 text,      -- modèle Gemini utilisé
    nb_photos              smallint,
    erreur                 text,      -- rempli si l'analyse a échoué
    created_at             timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.repair_ai_analyses ENABLE ROW LEVEL SECURITY;

-- Lecture : administrateurs NETEXIAL uniquement. L'écriture se fait par le serveur (service_role).
DROP POLICY IF EXISTS "admin read ai analyses" ON public.repair_ai_analyses;
CREATE POLICY "admin read ai analyses" ON public.repair_ai_analyses
    FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM public.users u WHERE u.id = auth.uid() AND u.role = 'admin'));

-- Droits explicites (obligatoires pour les nouvelles tables Supabase)
GRANT SELECT ON public.repair_ai_analyses TO authenticated;
GRANT ALL    ON public.repair_ai_analyses TO service_role;
REVOKE ALL   ON public.repair_ai_analyses FROM anon;

-- Vérification : doit renvoyer 1 ligne avec rowsecurity = true
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname = 'public' AND tablename = 'repair_ai_analyses';
