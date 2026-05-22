-- =====================================================================
-- NETEXIAL — Plateforme de gestion des demandes de réparation
-- Setup Supabase complet : tables, RLS, storage, triggers, fonctions
-- =====================================================================
-- À exécuter dans Supabase SQL Editor (en une seule fois)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. EXTENSIONS
-- ---------------------------------------------------------------------
create extension if not exists "pgcrypto";

-- =====================================================================
-- 1. TABLES
-- =====================================================================

-- ---------------------------------------------------------------------
-- clients : entreprises clientes de NETEXIAL
-- ---------------------------------------------------------------------
create table if not exists public.clients (
    id              uuid primary key default gen_random_uuid(),
    code_client     text not null unique,                 -- ex: NTX-001 — utilisé pour login
    nom             text not null,
    email_contact   text,
    logo_url        text,                                 -- URL publique stockée dans le bucket "logos"
    couleur_primaire text default '#141B4D',              -- thème custom optionnel
    actif           boolean not null default true,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

comment on table public.clients is 'Entreprises clientes de NETEXIAL ayant accès au portail';

-- ---------------------------------------------------------------------
-- users : comptes utilisateurs liés à un client OU administrateur
-- ---------------------------------------------------------------------
-- On utilise auth.users de Supabase pour l'authentification.
-- Cette table étend les infos avec le rôle et le rattachement client.
create table if not exists public.users (
    id              uuid primary key references auth.users(id) on delete cascade,
    email           text not null unique,
    nom_complet     text,
    role            text not null check (role in ('admin', 'client')),
    client_id       uuid references public.clients(id) on delete cascade,
    created_at      timestamptz not null default now()
);

comment on table public.users is 'Profils utilisateurs (admin ou client) liés à auth.users';

-- ---------------------------------------------------------------------
-- forms : formulaires personnalisés par client
-- ---------------------------------------------------------------------
create table if not exists public.forms (
    id              uuid primary key default gen_random_uuid(),
    client_id       uuid not null references public.clients(id) on delete cascade,
    nom             text not null,
    description     text,
    -- structure JSON des champs personnalisés
    champs_json     jsonb not null default '[]'::jsonb,
    actif           boolean not null default true,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

comment on table public.forms is 'Formulaires personnalisés par client';

-- ---------------------------------------------------------------------
-- products : référentiel produits scannés
-- ---------------------------------------------------------------------
create table if not exists public.products (
    id              uuid primary key default gen_random_uuid(),
    code_barre      text not null unique,
    reference       text,
    numero_serie    text,
    type_equipement text,
    libelle         text,
    metadata        jsonb default '{}'::jsonb,
    created_at      timestamptz not null default now()
);

comment on table public.products is 'Référentiel des produits identifiés par code-barres';

-- ---------------------------------------------------------------------
-- repair_requests : demandes de réparation
-- ---------------------------------------------------------------------
create table if not exists public.repair_requests (
    id              uuid primary key default gen_random_uuid(),
    -- numéro ticket lisible : NTX-2026-000123
    ticket_number   text not null unique,
    client_id       uuid not null references public.clients(id) on delete restrict,
    user_id         uuid references public.users(id) on delete set null,
    form_id         uuid references public.forms(id) on delete set null,

    -- infos produit
    code_barre      text,
    reference_produit text,
    numero_serie    text,
    type_equipement text,

    -- description du problème
    description     text not null,

    -- statut et workflow
    statut          text not null default 'en_attente'
                    check (statut in ('en_attente', 'acceptee', 'refusee', 'cloturee')),
    raison_refus    text,

    -- données custom du formulaire (selon champs_json)
    donnees_form    jsonb default '{}'::jsonb,

    -- métadonnées
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    traite_at       timestamptz,
    traite_par      uuid references public.users(id) on delete set null
);

comment on table public.repair_requests is 'Demandes de réparation soumises par les clients';

create index if not exists idx_requests_client on public.repair_requests(client_id);
create index if not exists idx_requests_statut on public.repair_requests(statut);
create index if not exists idx_requests_created on public.repair_requests(created_at desc);
create index if not exists idx_requests_code_barre on public.repair_requests(code_barre);

-- ---------------------------------------------------------------------
-- request_images : photos liées à une demande
-- ---------------------------------------------------------------------
create table if not exists public.request_images (
    id              uuid primary key default gen_random_uuid(),
    request_id      uuid not null references public.repair_requests(id) on delete cascade,
    storage_path    text not null,                         -- chemin dans le bucket "repair-images"
    url_publique    text,
    filename        text,
    taille_octets   bigint,
    created_at      timestamptz not null default now()
);

create index if not exists idx_images_request on public.request_images(request_id);

-- ---------------------------------------------------------------------
-- notifications : journal des emails envoyés et notifs in-app
-- ---------------------------------------------------------------------
create table if not exists public.notifications (
    id              uuid primary key default gen_random_uuid(),
    request_id      uuid references public.repair_requests(id) on delete cascade,
    user_id         uuid references public.users(id) on delete set null,
    type            text not null,                         -- 'email_confirm', 'email_validation', 'in_app'
    destinataire    text,
    sujet           text,
    contenu         text,
    statut          text not null default 'envoye'
                    check (statut in ('envoye', 'echec', 'en_attente')),
    lue             boolean not null default false,
    created_at      timestamptz not null default now()
);

create index if not exists idx_notifs_user on public.notifications(user_id, lue);

-- =====================================================================
-- 2. FONCTIONS UTILITAIRES
-- =====================================================================

-- ---------------------------------------------------------------------
-- Auto-update du champ updated_at
-- ---------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_clients_updated on public.clients;
create trigger trg_clients_updated before update on public.clients
    for each row execute function public.set_updated_at();

drop trigger if exists trg_forms_updated on public.forms;
create trigger trg_forms_updated before update on public.forms
    for each row execute function public.set_updated_at();

drop trigger if exists trg_requests_updated on public.repair_requests;
create trigger trg_requests_updated before update on public.repair_requests
    for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------
-- Génération automatique du numéro de ticket : NTX-AAAA-NNNNNN
-- ---------------------------------------------------------------------
create or replace function public.generate_ticket_number()
returns trigger
language plpgsql
as $$
declare
    year_part text;
    seq_num   bigint;
begin
    if new.ticket_number is null or new.ticket_number = '' then
        year_part := to_char(now(), 'YYYY');
        select coalesce(max(
            case
                when ticket_number ~ ('^NTX-' || year_part || '-\d+$')
                then (split_part(ticket_number, '-', 3))::bigint
                else 0
            end
        ), 0) + 1
        into seq_num
        from public.repair_requests;

        new.ticket_number := 'NTX-' || year_part || '-' || lpad(seq_num::text, 6, '0');
    end if;
    return new;
end;
$$;

drop trigger if exists trg_generate_ticket on public.repair_requests;
create trigger trg_generate_ticket before insert on public.repair_requests
    for each row execute function public.generate_ticket_number();

-- ---------------------------------------------------------------------
-- Détection anti-doublon : même client + même code-barres + < 24h
-- ---------------------------------------------------------------------
create or replace function public.check_duplicate_request(
    p_client_id uuid,
    p_code_barre text,
    p_window_hours int default 24
)
returns table(id uuid, ticket_number text, created_at timestamptz)
language sql
stable
as $$
    select id, ticket_number, created_at
    from public.repair_requests
    where client_id = p_client_id
      and code_barre is not null
      and code_barre = p_code_barre
      and statut in ('en_attente', 'acceptee')
      and created_at > now() - (p_window_hours || ' hours')::interval
    order by created_at desc
    limit 1;
$$;

-- =====================================================================
-- 3. ROW LEVEL SECURITY (RLS)
-- =====================================================================

alter table public.clients          enable row level security;
alter table public.users            enable row level security;
alter table public.forms            enable row level security;
alter table public.products         enable row level security;
alter table public.repair_requests  enable row level security;
alter table public.request_images   enable row level security;
alter table public.notifications    enable row level security;

-- ---------------------------------------------------------------------
-- Helper : récupérer le rôle de l'utilisateur courant
-- ---------------------------------------------------------------------
create or replace function public.current_user_role()
returns text
language sql
stable
security definer
set search_path = public
as $$
    select role from public.users where id = auth.uid();
$$;

create or replace function public.current_user_client_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
    select client_id from public.users where id = auth.uid();
$$;

-- ---------------------------------------------------------------------
-- POLICIES : clients
-- ---------------------------------------------------------------------
drop policy if exists "admin all clients" on public.clients;
create policy "admin all clients" on public.clients
    for all using (public.current_user_role() = 'admin')
    with check (public.current_user_role() = 'admin');

drop policy if exists "client read own" on public.clients;
create policy "client read own" on public.clients
    for select using (
        public.current_user_role() = 'client'
        and id = public.current_user_client_id()
    );

-- ---------------------------------------------------------------------
-- POLICIES : users
-- ---------------------------------------------------------------------
drop policy if exists "admin all users" on public.users;
create policy "admin all users" on public.users
    for all using (public.current_user_role() = 'admin')
    with check (public.current_user_role() = 'admin');

drop policy if exists "user read self" on public.users;
create policy "user read self" on public.users
    for select using (id = auth.uid());

-- ---------------------------------------------------------------------
-- POLICIES : forms
-- ---------------------------------------------------------------------
drop policy if exists "admin all forms" on public.forms;
create policy "admin all forms" on public.forms
    for all using (public.current_user_role() = 'admin')
    with check (public.current_user_role() = 'admin');

drop policy if exists "client read own forms" on public.forms;
create policy "client read own forms" on public.forms
    for select using (
        public.current_user_role() = 'client'
        and client_id = public.current_user_client_id()
    );

-- ---------------------------------------------------------------------
-- POLICIES : products (lecture libre pour utilisateurs connectés)
-- ---------------------------------------------------------------------
drop policy if exists "auth read products" on public.products;
create policy "auth read products" on public.products
    for select using (auth.uid() is not null);

drop policy if exists "admin write products" on public.products;
create policy "admin write products" on public.products
    for all using (public.current_user_role() = 'admin')
    with check (public.current_user_role() = 'admin');

-- ---------------------------------------------------------------------
-- POLICIES : repair_requests
-- ---------------------------------------------------------------------
drop policy if exists "admin all requests" on public.repair_requests;
create policy "admin all requests" on public.repair_requests
    for all using (public.current_user_role() = 'admin')
    with check (public.current_user_role() = 'admin');

drop policy if exists "client read own requests" on public.repair_requests;
create policy "client read own requests" on public.repair_requests
    for select using (
        public.current_user_role() = 'client'
        and client_id = public.current_user_client_id()
    );

drop policy if exists "client create own requests" on public.repair_requests;
create policy "client create own requests" on public.repair_requests
    for insert with check (
        public.current_user_role() = 'client'
        and client_id = public.current_user_client_id()
    );

-- ---------------------------------------------------------------------
-- POLICIES : request_images
-- ---------------------------------------------------------------------
drop policy if exists "admin all images" on public.request_images;
create policy "admin all images" on public.request_images
    for all using (public.current_user_role() = 'admin')
    with check (public.current_user_role() = 'admin');

drop policy if exists "client read own images" on public.request_images;
create policy "client read own images" on public.request_images
    for select using (
        exists (
            select 1 from public.repair_requests r
            where r.id = request_id
              and r.client_id = public.current_user_client_id()
        )
    );

drop policy if exists "client create own images" on public.request_images;
create policy "client create own images" on public.request_images
    for insert with check (
        exists (
            select 1 from public.repair_requests r
            where r.id = request_id
              and r.client_id = public.current_user_client_id()
        )
    );

-- ---------------------------------------------------------------------
-- POLICIES : notifications
-- ---------------------------------------------------------------------
drop policy if exists "admin all notifs" on public.notifications;
create policy "admin all notifs" on public.notifications
    for all using (public.current_user_role() = 'admin')
    with check (public.current_user_role() = 'admin');

drop policy if exists "user read own notifs" on public.notifications;
create policy "user read own notifs" on public.notifications
    for select using (user_id = auth.uid());

drop policy if exists "user update own notifs" on public.notifications;
create policy "user update own notifs" on public.notifications
    for update using (user_id = auth.uid());

-- =====================================================================
-- 4. STORAGE BUCKETS
-- =====================================================================
-- À exécuter dans le SQL Editor également (les buckets sont stockés en BDD)

insert into storage.buckets (id, name, public)
values
    ('logos', 'logos', true),
    ('repair-images', 'repair-images', true)
on conflict (id) do nothing;

-- Policies storage : logos (lecture publique, écriture admin)
drop policy if exists "logos public read" on storage.objects;
create policy "logos public read" on storage.objects
    for select using (bucket_id = 'logos');

drop policy if exists "logos admin write" on storage.objects;
create policy "logos admin write" on storage.objects
    for insert with check (
        bucket_id = 'logos' and public.current_user_role() = 'admin'
    );

drop policy if exists "logos admin update" on storage.objects;
create policy "logos admin update" on storage.objects
    for update using (
        bucket_id = 'logos' and public.current_user_role() = 'admin'
    );

drop policy if exists "logos admin delete" on storage.objects;
create policy "logos admin delete" on storage.objects
    for delete using (
        bucket_id = 'logos' and public.current_user_role() = 'admin'
    );

-- Policies storage : repair-images
-- Lecture publique (URLs partageables), écriture par utilisateur authentifié
drop policy if exists "repair-images public read" on storage.objects;
create policy "repair-images public read" on storage.objects
    for select using (bucket_id = 'repair-images');

drop policy if exists "repair-images auth write" on storage.objects;
create policy "repair-images auth write" on storage.objects
    for insert with check (
        bucket_id = 'repair-images' and auth.uid() is not null
    );

drop policy if exists "repair-images admin delete" on storage.objects;
create policy "repair-images admin delete" on storage.objects
    for delete using (
        bucket_id = 'repair-images' and public.current_user_role() = 'admin'
    );

-- =====================================================================
-- 5. REALTIME : activation pour les tables qui en ont besoin
-- =====================================================================
alter publication supabase_realtime add table public.repair_requests;
alter publication supabase_realtime add table public.notifications;

-- =====================================================================
-- 6. SEED : données initiales (à adapter)
-- =====================================================================
-- Créer un premier admin manuellement après avoir créé son compte auth.users
-- via le dashboard Supabase, puis :
-- insert into public.users (id, email, nom_complet, role)
-- values ('<UUID_DE_AUTH_USERS>', 'admin@netexial.com', 'Admin Netexial', 'admin');

-- Exemple de client de démo
insert into public.clients (code_client, nom, email_contact, couleur_primaire)
values ('NTX-DEMO', 'Client Démo', 'demo@netexial.com', '#141B4D')
on conflict (code_client) do nothing;

-- =====================================================================
-- FIN DU SCRIPT
-- =====================================================================
