-- Postgres only. Run once as the table owner, after schema.sql.
--
-- The application connects, or switches, to followup_app. That role does not own the
-- tables, is not a superuser and cannot bypass row level security. Every query it
-- runs sees only rows whose shop_id equals the app.shop_id setting, which the
-- application sets per transaction. With no setting the comparison is against NULL,
-- so every query returns nothing. It fails closed.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'followup_app') THEN
        CREATE ROLE followup_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO followup_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO followup_app;

-- Enable and force row level security on every table that has a shop_id column.
-- Looping over the catalog means a table added later is covered by rerunning this file.
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN
        SELECT c.table_name
        FROM information_schema.columns c
        JOIN information_schema.tables tb
          ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
        WHERE c.table_schema = 'public' AND c.column_name = 'shop_id' AND tb.table_type = 'BASE TABLE'
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS shop_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY shop_isolation ON %I '
            'USING (shop_id = current_setting(''app.shop_id'', true)) '
            'WITH CHECK (shop_id = current_setting(''app.shop_id'', true))',
            t
        );
    END LOOP;
END
$$;
