-- Ajout des emplacements « Ventre » et « Col » (demande Graphic Packaging)
-- À exécuter AVANT de déployer app.py / reparations.js.
ALTER TABLE public.repair_requests DROP CONSTRAINT IF EXISTS repair_requests_emplacement_check;
ALTER TABLE public.repair_requests ADD CONSTRAINT repair_requests_emplacement_check
    CHECK (emplacement IS NULL OR emplacement IN (
        'dos','genoux','entrejambe','poche_avant','poche_arriere','poche_gauche',
        'poche_droite','jambe_gauche','jambe_droite','bras_gauche','bras_droit',
        'ventre','col'
    ));
