-- bariq_site_content_ceayj.sql
-- ledger: silo=ceayjeamtmcyzzvqflus
-- (ihsanos multi-tenant DB — Bariq data home per operator ruling op#34118,
--  pinned in docs/data-store-registry.md; supersedes the substrate placement
--  floated in hc#576/#75222. That substrate draft was never applied and is deleted.)
-- assert: no_table_privilege anon public.bariq_site_content INSERT
-- assert: no_table_privilege authenticated public.bariq_site_content INSERT
-- assert: no_table_privilege anon public.bariq_site_content UPDATE
-- assert: no_table_privilege authenticated public.bariq_site_content UPDATE
-- assert: no_table_privilege anon public.bariq_site_content DELETE
-- assert: no_table_privilege authenticated public.bariq_site_content DELETE
-- assert: no_table_privilege anon public.bariq_site_content TRUNCATE
-- assert: no_table_privilege authenticated public.bariq_site_content TRUNCATE
-- assert: no_table_privilege anon public.bariq_site_content_history SELECT
-- assert: no_table_privilege authenticated public.bariq_site_content_history SELECT
-- assert: no_table_privilege anon public.bariq_site_content_history INSERT
-- assert: no_table_privilege authenticated public.bariq_site_content_history INSERT
--
-- bariq_site_content — DB-backed editable content for the Bariq Employment
-- site (client self-edit V1, cc-bariq, re-targeted to ceayj per op#34118).
-- Follows the ihsanos multi-tenant standard: org_id on every table,
-- RLS on every table (001_foundation "Amanah" rules), flat public schema
-- with a domain prefix.
--
-- Bariq org: type 'business' (live CHECK allows it), UEN 202219347M.
-- Deterministic UUID so the anon read policy and the site can pin it
-- without a lookup round-trip: 62617269-7100-4000-8000-000000000001.
--
-- Access model: the Vercel-hosted site reads with the ceayj ANON role,
-- SELECT-only, RLS-pinned to the Bariq org's rows (it can never see another
-- tenant's rows even though they share this table pattern). Writes come from
-- the fleet with a least-privilege write credential (separate ask, CAI-981).
-- History is fully private to the fleet (no anon/authenticated access).

-- ── 1. Bariq as an organization (tenant) ─────────────────────────────────────
INSERT INTO organizations (id, name, uen, type, slug, settings)
VALUES ('62617269-7100-4000-8000-000000000001',
        'Bariq Employment Services Pte. Ltd.',
        '202219347M',
        'business',
        'bariq-employment',
        '{}')
ON CONFLICT (id) DO NOTHING;

-- ── 2. Content store: one JSONB document per editable key, org-scoped ───────
CREATE TABLE bariq_site_content (
  org_id     UUID NOT NULL REFERENCES organizations(id),
  key        TEXT NOT NULL,          -- 'site' (contact info), 'hero', 'testimonials'
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL DEFAULT 'system',
  PRIMARY KEY (org_id, key)
);

-- ── 3. Append-only history (fleet-private) ──────────────────────────────────
CREATE TABLE bariq_site_content_history (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id     UUID NOT NULL REFERENCES organizations(id),
  key        TEXT NOT NULL,
  old_value  JSONB,
  new_value  JSONB NOT NULL,
  changed_by TEXT NOT NULL,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION bariq_site_content_audit() RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO bariq_site_content_history (org_id, key, old_value, new_value, changed_by)
  VALUES (NEW.org_id, NEW.key, OLD.value, NEW.value, NEW.updated_by);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

CREATE TRIGGER bariq_site_content_audit_trg
AFTER UPDATE ON bariq_site_content
FOR EACH ROW EXECUTE FUNCTION bariq_site_content_audit();

-- ── 4. Privileges + RLS ──────────────────────────────────────────────────────
-- pg_default_acl grants anon/authenticated ALL on new tables implicitly —
-- revoke everything, then grant back exactly one thing: anon SELECT.
REVOKE ALL ON bariq_site_content FROM anon, authenticated;
GRANT SELECT ON bariq_site_content TO anon;
REVOKE ALL ON bariq_site_content_history FROM anon, authenticated;
REVOKE ALL ON FUNCTION bariq_site_content_audit() FROM anon, authenticated;

ALTER TABLE bariq_site_content ENABLE ROW LEVEL SECURITY;
ALTER TABLE bariq_site_content_history ENABLE ROW LEVEL SECURITY;

-- Public read, pinned to the Bariq org only: the marketing site's anon reader
-- can see this one tenant's rows and nothing else in the shared DB.
CREATE POLICY bariq_site_content_public_read
  ON bariq_site_content FOR SELECT
  TO anon
  USING (org_id = '62617269-7100-4000-8000-000000000001');

-- No policies at all on history: default-deny for every non-bypass role.

-- ── 5. Seed: current production values ──────────────────────────────────────
INSERT INTO bariq_site_content (org_id, key, value, updated_by) VALUES
('62617269-7100-4000-8000-000000000001', 'site', $json$
{
  "phoneHotline": "+65 8831 1720",
  "phoneWhatsapp": "+65 8863 5086",
  "whatsappLink": "https://wa.me/6588635086",
  "email": "bariqemployment@gmail.com",
  "address": [
    "109 Kaki Bukit Avenue 1 #02-00 (Office 9)",
    "Shun Li Industrial Park, Singapore 415989"
  ],
  "addressNote": "5 min walk from Kaki Bukit MRT",
  "hours": [
    { "label": "Monday – Friday", "value": "10:00am – 6:00pm" },
    { "label": "Saturday", "value": "9:00am – 12:00pm (by appointment)" },
    { "label": "Sunday & Public Holidays", "value": "Closed" }
  ]
}
$json$, 'seed'),
('62617269-7100-4000-8000-000000000001', 'hero', $json$
{
  "headline": "Matching trust with true care.",
  "description": "For over a decade, Bariq Employment has helped Singapore families find domestic helpers who bring both skill and dedication into the home — with transparent pricing and support that doesn't end at placement."
}
$json$, 'seed'),
('62617269-7100-4000-8000-000000000001', 'testimonials', $json$
[
  {
    "quote": "I had a pleasant experience with Amirah from Bariq. Most of our correspondences were done conveniently on WhatsApp, with our only meet-up at the office being for picking up our helper. She was very patient to go through all the documents with us, and explained what to expect in the coming weeks. Thank you for the very seamless process!",
    "name": "Nurul Hudah"
  },
  {
    "quote": "I engaged Bariq Employment this year to find a new domestic helper, and the entire process was smooth and efficient. Biodata were shared promptly, and my preferences were clearly taken into account. The helper matched all the criteria I had set, and has been adapting well since joining us.",
    "name": "Diyana Rahayu"
  },
  {
    "quote": "We had an excellent experience with Bariq Employment Services. We liaised with Ms Noor Atiqah for both helpers. Their team was professional, efficient and attentive to our specific requirements. We would highly recommend their services to anyone in need of a reliable household helper.",
    "name": "Mr & Mrs M. H."
  },
  {
    "quote": "Stress-free to find a helper with the help of Amirah. All the required processes and requirements were arranged and well explained. Great after-service too — we managed to get a helper who is good at taking care of my elderly mum with dementia.",
    "name": "Lim Aishah"
  }
]
$json$, 'seed');
