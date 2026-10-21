-- ============================================================
-- Formulaire porteur : type de réparation + emplacement
-- À exécuter AVANT de déployer le nouveau index.html / app.py.
-- ============================================================

ALTER TABLE public.repair_requests
    ADD COLUMN IF NOT EXISTS type_reparation text,
    ADD COLUMN IF NOT EXISTS emplacement     text;

-- Codes autorisés (NULL = anciennes demandes, avant les listes)
ALTER TABLE public.repair_requests DROP CONSTRAINT IF EXISTS repair_requests_type_reparation_check;
ALTER TABLE public.repair_requests ADD CONSTRAINT repair_requests_type_reparation_check
    CHECK (type_reparation IS NULL OR type_reparation IN (
        'CP001','CP002','CP003','CP004','CP005',
        'CR001','CR002','CR003','CR004',
        'CB001','CB002','CB003'
    ));

ALTER TABLE public.repair_requests DROP CONSTRAINT IF EXISTS repair_requests_emplacement_check;
ALTER TABLE public.repair_requests ADD CONSTRAINT repair_requests_emplacement_check
    CHECK (emplacement IS NULL OR emplacement IN (
        'dos','genoux','entrejambe','poche_avant','poche_arriere','poche_gauche',
        'poche_droite','jambe_gauche','jambe_droite','bras_gauche','bras_droit'
    ));

CREATE INDEX IF NOT EXISTS idx_repair_requests_type_reparation ON public.repair_requests (type_reparation);

-- Vérification : doit renvoyer 2 lignes
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'repair_requests'
  AND column_name IN ('type_reparation', 'emplacement');
