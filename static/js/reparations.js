/* =============================================================
   Listes du formulaire porteur — partagées par declarer.html et index.html
   (mêmes codes dans app.py et dans la migration SQL)
   ============================================================= */
const REPAIR_TYPE_GROUPS = [
    { label: 'Couture décousue', items: [['CP001', 'Poche décousue'], ['CP002', 'Ourlet décousu'], ['CP003', 'Bande rétro décousue'], ['CP004', 'Élément décousu'], ['CP005', 'Entrejambe décousue']] },
    { label: 'Déchirure ou trou', items: [['CR001', 'Lanière arrachée'], ['CR002', 'Trou'], ['CR003', 'Déchirure'], ['CR004', 'Déchirure entrejambe']] },
    { label: 'Bouton ou fermeture', items: [['CB001', 'Manque bouton'], ['CB002', 'Bouton cassé'], ['CB003', 'Fermeture cassée']] },
];
const REPAIR_TYPES = Object.fromEntries(REPAIR_TYPE_GROUPS.flatMap(g => g.items));
const EMPLACEMENTS = {
    dos: 'Dos', genoux: 'Genoux', entrejambe: 'Entrejambe',
    poche_avant: 'Poche avant', poche_arriere: 'Poche arrière', poche_gauche: 'Poche gauche', poche_droite: 'Poche droite',
    jambe_gauche: 'Jambe gauche', jambe_droite: 'Jambe droite', bras_gauche: 'Bras gauche', bras_droit: 'Bras droit',
    ventre: 'Ventre', col: 'Col',
};
const repairTypeImg = code => `/static/reparations/${code}.jpg`;
// « Déchirure · Genoux » (vide pour les anciennes demandes)
function repairSummary(r) {
    return [REPAIR_TYPES[r?.type_reparation], EMPLACEMENTS[r?.emplacement]].filter(Boolean).join(' · ');
}
// Texte court pour les listes : type · emplacement — précisions
function repairText(r) {
    return [repairSummary(r), (r?.description || '').trim()].filter(Boolean).join(' — ');
}
