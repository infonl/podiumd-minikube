-- Creates every database/role docker-compose.yaml runs as a separate Postgres
-- container, consolidated onto this one shared postgis/postgis instance.
-- Credentials copied verbatim from docker-compose.yaml's per-service
-- POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB values.
--
-- Runs once, on first init of an empty data directory (standard Postgres
-- docker-entrypoint-initdb.d behavior) - never re-applied on pod restarts
-- against an existing volume.

CREATE ROLE keycloak WITH LOGIN PASSWORD 'keycloak';
CREATE DATABASE keycloak OWNER keycloak;

CREATE ROLE openzaak WITH LOGIN PASSWORD 'openzaak';
CREATE DATABASE openzaak OWNER openzaak;

CREATE ROLE openklant WITH LOGIN PASSWORD 'openklant';
CREATE DATABASE openklant OWNER openklant;

-- Note: the objecten-api compose service's Postgres user/database is
-- literally named "objects", not "objecten" - copied as-is.
CREATE ROLE objects WITH LOGIN PASSWORD 'objects';
CREATE DATABASE objects OWNER objects;

CREATE ROLE objecttypes WITH LOGIN PASSWORD 'objecttypes';
CREATE DATABASE objecttypes OWNER objecttypes;

CREATE ROLE opennotificaties WITH LOGIN PASSWORD 'opennotificaties';
CREATE DATABASE opennotificaties OWNER opennotificaties;

CREATE ROLE openarchiefbeheer WITH LOGIN PASSWORD 'openarchiefbeheer';
CREATE DATABASE openarchiefbeheer OWNER openarchiefbeheer;

-- Not in the original plan draft's "9 databases" list - openformulieren
-- also runs its own Postgres container in docker-compose.yaml and needs one
-- here too, added during the step 0 vendoring pass.
CREATE ROLE openformulieren WITH LOGIN PASSWORD 'openformulieren';
CREATE DATABASE openformulieren OWNER openformulieren;

-- Note: pabc's compose database name is "Pabc" (capital P) while its user
-- is lowercase "pabc" - copied as-is.
CREATE ROLE pabc WITH LOGIN PASSWORD 'pabc';
CREATE DATABASE "Pabc" OWNER pabc;

CREATE ROLE zac WITH LOGIN PASSWORD 'password';
CREATE DATABASE zac OWNER zac;

-- KISS (kiss profile, chart name "contact") and ITA (ita profile): PodiumD
-- components, not in docker-compose; names as ExternalsPodiumD.
CREATE ROLE contact WITH LOGIN PASSWORD 'contact';
CREATE DATABASE contact OWNER contact;
CREATE ROLE ita WITH LOGIN PASSWORD 'ita';
CREATE DATABASE ita OWNER ita;
-- Referentielijsten and Open Beheer (their profiles), names as ExternalsPodiumD.
CREATE ROLE referentielijsten WITH LOGIN PASSWORD 'referentielijsten';
CREATE DATABASE referentielijsten OWNER referentielijsten;
CREATE ROLE openbeheer WITH LOGIN PASSWORD 'openbeheer';
CREATE DATABASE openbeheer OWNER openbeheer;

-- Open Inwoner (openinwoner profile): a PodiumD component, not in
-- docker-compose; credentials made up like contact's.
CREATE ROLE openinwoner WITH LOGIN PASSWORD 'openinwoner';
CREATE DATABASE openinwoner OWNER openinwoner;

-- PostGIS: only openzaak/objects/opennotificaties/openarchiefbeheer use the
-- postgis/postgis image in docker-compose.yaml (the others use plain
-- postgres:17.10). Explicitly installing the extension per-database here
-- rather than relying on the postgis image's own bundled initdb.d scripts
-- (which modify template1) to have already run first: Postgres executes
-- docker-entrypoint-initdb.d scripts in alphabetical order, and this
-- script's "00-" prefix would otherwise run *before* the postgis image's own
-- scripts (conventionally numbered higher, e.g. "10_postgis.sh"), so
-- databases created here could silently end up without the extension.
\c openzaak
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

\c objects
CREATE EXTENSION IF NOT EXISTS postgis;

\c opennotificaties
CREATE EXTENSION IF NOT EXISTS postgis;

\c openarchiefbeheer
CREATE EXTENSION IF NOT EXISTS postgis;

\c openinwoner
CREATE EXTENSION IF NOT EXISTS postgis;

-- zac database schema/grants, copied from zac-database/init-zac-database.sql
\c zac
CREATE SCHEMA flowable;
GRANT CREATE, USAGE ON SCHEMA flowable TO zac;

CREATE SCHEMA zaakafhandelcomponent;
GRANT CREATE, USAGE ON SCHEMA zaakafhandelcomponent to zac;
