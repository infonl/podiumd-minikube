-- Every app's database and role on the one shared postgis/postgis instance.
--
-- As podiumd-infra's Azure Flexible Server (scripts/create-postgresql-databases.sh):
-- the databases belong to the server admin (here postgres), each app role gets
-- ALL on its database and schema public; extensions only from the allow-list.
--
-- Runs once, on first init of an empty data directory (standard Postgres
-- docker-entrypoint-initdb.d behavior) - never re-applied on pod restarts
-- against an existing volume.

\c template1
\ir 00-azure-extension-allow-list.sql
\c postgres

CREATE ROLE keycloak WITH LOGIN PASSWORD 'keycloak';
CREATE DATABASE keycloak;

CREATE ROLE openzaak WITH LOGIN PASSWORD 'openzaak';
CREATE DATABASE openzaak;

CREATE ROLE openklant WITH LOGIN PASSWORD 'openklant';
CREATE DATABASE openklant;

CREATE ROLE objecten WITH LOGIN PASSWORD 'objecten';
CREATE DATABASE objecten;

CREATE ROLE objecttypen WITH LOGIN PASSWORD 'objecttypen';
CREATE DATABASE objecttypen;

CREATE ROLE opennotificaties WITH LOGIN PASSWORD 'opennotificaties';
CREATE DATABASE opennotificaties;

CREATE ROLE openarchiefbeheer WITH LOGIN PASSWORD 'openarchiefbeheer';
CREATE DATABASE openarchiefbeheer;

-- Not in the original plan draft's "9 databases" list - openformulieren
-- also runs its own Postgres container in docker-compose.yaml and needs one
-- here too, added during the step 0 vendoring pass.
CREATE ROLE openformulieren WITH LOGIN PASSWORD 'openformulieren';
CREATE DATABASE openformulieren;

-- pabc, as the podiumd chart and the reference environments (compose: "Pabc").
CREATE ROLE pabc WITH LOGIN PASSWORD 'pabc';
CREATE DATABASE pabc;

CREATE ROLE zac WITH LOGIN PASSWORD 'password';
CREATE DATABASE zac;

-- KISS (kiss profile, chart name "contact") and ITA (ita profile): PodiumD
-- components, not in docker-compose; names as ExternalsPodiumD.
CREATE ROLE contact WITH LOGIN PASSWORD 'contact';
CREATE DATABASE contact;
CREATE ROLE ita WITH LOGIN PASSWORD 'ita';
CREATE DATABASE ita;
-- Referentielijsten and Open Beheer (their profiles), names as ExternalsPodiumD.
CREATE ROLE referentielijsten WITH LOGIN PASSWORD 'referentielijsten';
CREATE DATABASE referentielijsten;
CREATE ROLE openbeheer WITH LOGIN PASSWORD 'openbeheer';
CREATE DATABASE openbeheer;
-- OpenBao (frankgateway profile); its schema Job creates the tables.
CREATE ROLE openbao WITH LOGIN PASSWORD 'openbao';
CREATE DATABASE openbao;
-- Open VTB and ZaakBrug (their profiles).
CREATE ROLE openvtb WITH LOGIN PASSWORD 'openvtb';
CREATE DATABASE openvtb;
CREATE ROLE zaakbrug WITH LOGIN PASSWORD 'zaakbrug';
CREATE DATABASE zaakbrug;

-- Open Inwoner (openinwoner profile): a PodiumD component, not in
-- docker-compose; credentials made up like contact's.
CREATE ROLE openinwoner WITH LOGIN PASSWORD 'openinwoner';
CREATE DATABASE openinwoner;

-- PostGIS for the apps that need it: this ConfigMap replaces the image's
-- initdb directory, so its 10_postgis.sh never runs.
\c openzaak
CREATE EXTENSION IF NOT EXISTS postgis;

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

-- Each app role's grants (postgres.align_with_azure does the same on a running cluster).
\c keycloak
GRANT ALL ON DATABASE keycloak TO keycloak;
GRANT ALL ON SCHEMA public TO keycloak;
\c openzaak
GRANT ALL ON DATABASE openzaak TO openzaak;
GRANT ALL ON SCHEMA public TO openzaak;
\c openklant
GRANT ALL ON DATABASE openklant TO openklant;
GRANT ALL ON SCHEMA public TO openklant;
\c objecten
GRANT ALL ON DATABASE objecten TO objecten;
GRANT ALL ON SCHEMA public TO objecten;
\c objecttypen
GRANT ALL ON DATABASE objecttypen TO objecttypen;
GRANT ALL ON SCHEMA public TO objecttypen;
\c opennotificaties
GRANT ALL ON DATABASE opennotificaties TO opennotificaties;
GRANT ALL ON SCHEMA public TO opennotificaties;
\c openarchiefbeheer
GRANT ALL ON DATABASE openarchiefbeheer TO openarchiefbeheer;
GRANT ALL ON SCHEMA public TO openarchiefbeheer;
\c openformulieren
GRANT ALL ON DATABASE openformulieren TO openformulieren;
GRANT ALL ON SCHEMA public TO openformulieren;
\c pabc
GRANT ALL ON DATABASE pabc TO pabc;
GRANT ALL ON SCHEMA public TO pabc;
\c zac
GRANT ALL ON DATABASE zac TO zac;
GRANT ALL ON SCHEMA public TO zac;
\c contact
GRANT ALL ON DATABASE contact TO contact;
GRANT ALL ON SCHEMA public TO contact;
\c ita
GRANT ALL ON DATABASE ita TO ita;
GRANT ALL ON SCHEMA public TO ita;
\c referentielijsten
GRANT ALL ON DATABASE referentielijsten TO referentielijsten;
GRANT ALL ON SCHEMA public TO referentielijsten;
\c openbeheer
GRANT ALL ON DATABASE openbeheer TO openbeheer;
GRANT ALL ON SCHEMA public TO openbeheer;
\c openbao
GRANT ALL ON DATABASE openbao TO openbao;
GRANT ALL ON SCHEMA public TO openbao;
\c openvtb
GRANT ALL ON DATABASE openvtb TO openvtb;
GRANT ALL ON SCHEMA public TO openvtb;
\c zaakbrug
GRANT ALL ON DATABASE zaakbrug TO zaakbrug;
GRANT ALL ON SCHEMA public TO zaakbrug;
\c openinwoner
GRANT ALL ON DATABASE openinwoner TO openinwoner;
GRANT ALL ON SCHEMA public TO openinwoner;
