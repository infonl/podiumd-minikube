-- Azure Flexible Server's extension allow-list, as podiumd-infra sets
-- azure.extensions = POSTGIS,PG_TRGM: CREATE EXTENSION of anything else fails.
-- Per database (event triggers are); postgres.align_with_azure installs it in
-- template1 and every database, the init SQL in template1. Idempotent.
CREATE OR REPLACE FUNCTION azure_extension_allow_list() RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
  created record;
BEGIN
  FOR created IN SELECT object_identity FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'CREATE EXTENSION' LOOP
    IF created.object_identity NOT IN ('postgis', 'pg_trgm') THEN
      RAISE EXCEPTION 'extension % is not allowed: Azure allows only POSTGIS,PG_TRGM (azure.extensions)', created.object_identity;
    END IF;
  END LOOP;
END
$$;
DROP EVENT TRIGGER IF EXISTS azure_extension_allow_list;
CREATE EVENT TRIGGER azure_extension_allow_list ON ddl_command_end WHEN TAG IN ('CREATE EXTENSION')
  EXECUTE FUNCTION azure_extension_allow_list();
