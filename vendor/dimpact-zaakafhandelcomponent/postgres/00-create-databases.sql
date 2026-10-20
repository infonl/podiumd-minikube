-- Every app's database and role on the one shared postgis/postgis instance.
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

CREATE ROLE objecten WITH LOGIN PASSWORD 'objecten';
CREATE DATABASE objecten OWNER objecten;

CREATE ROLE objecttypen WITH LOGIN PASSWORD 'objecttypen';
CREATE DATABASE objecttypen OWNER objecttypen;

CREATE ROLE opennotificaties WITH LOGIN PASSWORD 'opennotificaties';
CREATE DATABASE opennotificaties OWNER opennotificaties;

CREATE ROLE openarchiefbeheer WITH LOGIN PASSWORD 'openarchiefbeheer';
CREATE DATABASE openarchiefbeheer OWNER openarchiefbeheer;

-- Not in the original plan draft's "9 databases" list - openformulieren
-- also runs its own Postgres container in docker-compose.yaml and needs one
-- here too, added during the step 0 vendoring pass.
CREATE ROLE openformulieren WITH LOGIN PASSWORD 'openformulieren';
CREATE DATABASE openformulieren OWNER openformulieren;

-- pabc, as the podiumd chart and the reference environments (compose: "Pabc").
CREATE ROLE pabc WITH LOGIN PASSWORD 'pabc';
CREATE DATABASE pabc OWNER pabc;

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
-- OpenBao (frankgateway profile); its schema Job creates the tables.
CREATE ROLE openbao WITH LOGIN PASSWORD 'openbao';
CREATE DATABASE openbao OWNER openbao;
-- Open VTB and ZaakBrug (their profiles).
CREATE ROLE openvtb WITH LOGIN PASSWORD 'openvtb';
CREATE DATABASE openvtb OWNER openvtb;
CREATE ROLE zaakbrug WITH LOGIN PASSWORD 'zaakbrug';
CREATE DATABASE zaakbrug OWNER zaakbrug;

-- Open Inwoner (openinwoner profile): a PodiumD component, not in
-- docker-compose; credentials made up like contact's.
CREATE ROLE openinwoner WITH LOGIN PASSWORD 'openinwoner';
CREATE DATABASE openinwoner OWNER openinwoner;

-- PostGIS for the apps that need it, installed here: this script runs before
-- the postgis image's own initdb scripts, which add it to template1.
\c openzaak
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

\c objecten
CREATE EXTENSION IF NOT EXISTS postgis;

\c opennotificaties
CREATE EXTENSION IF NOT EXISTS postgis;

\c openarchiefbeheer
CREATE EXTENSION IF NOT EXISTS postgis;

\c openinwoner
CREATE EXTENSION IF NOT EXISTS postgis;

\c openvtb
CREATE EXTENSION IF NOT EXISTS postgis;

-- zac database schema/grants, copied from zac-database/init-zac-database.sql
\c zac
CREATE SCHEMA flowable;
GRANT CREATE, USAGE ON SCHEMA flowable TO zac;

CREATE SCHEMA zaakafhandelcomponent;
GRANT CREATE, USAGE ON SCHEMA zaakafhandelcomponent to zac;
