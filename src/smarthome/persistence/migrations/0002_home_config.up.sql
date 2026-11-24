-- 0002: home configuration (ADR 0006, LLD P0-06). Numbers are double precision (not real) so a
-- value read back is exactly the value written: 2.8 must not come back as 2.7999999523. Keys are scoped by home so a row can never
-- point into another home; every foreign key is RESTRICT (placements CASCADE: derived data).

CREATE TABLE homes (
    id             text        PRIMARY KEY CHECK (id <> ''),
    name           text        NOT NULL CHECK (name <> ''),
    simulated      boolean     NOT NULL DEFAULT false,
    config_version integer     NOT NULL DEFAULT 1 CHECK (config_version >= 1),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE floors (
    home_id     text    NOT NULL REFERENCES homes (id) ON DELETE RESTRICT,
    id          text    NOT NULL CHECK (id <> ''),
    name        text    NOT NULL,
    level       integer NOT NULL,
    elevation_m double precision    NOT NULL DEFAULT 0,
    height_m double precision    NOT NULL DEFAULT 3 CHECK (height_m > 0),
    PRIMARY KEY (home_id, id),
    UNIQUE (home_id, level)
);

CREATE TABLE rooms (
    home_id   text  NOT NULL,
    id        text  NOT NULL CHECK (id <> ''),
    floor_id  text  NOT NULL,
    name      text  NOT NULL,
    footprint jsonb NOT NULL CHECK (jsonb_typeof(footprint) = 'array' AND jsonb_array_length(footprint) = 4),
    PRIMARY KEY (home_id, id),
    FOREIGN KEY (home_id, floor_id) REFERENCES floors (home_id, id) ON DELETE RESTRICT
);

CREATE TABLE gateway_connections (
    id         text    PRIMARY KEY CHECK (id <> ''),
    home_id    text    NOT NULL REFERENCES homes (id) ON DELETE RESTRICT,
    kind       text    NOT NULL CHECK (kind IN ('simulator', 'home_assistant')),
    name       text    NOT NULL,
    url        text,
    secret_ref text,
    enabled    boolean NOT NULL DEFAULT true,
    UNIQUE (id, home_id),
    CHECK (kind <> 'home_assistant' OR (url IS NOT NULL AND secret_ref IS NOT NULL))
);

CREATE TABLE devices (
    id           text        PRIMARY KEY CHECK (id <> ''),
    home_id      text        NOT NULL,
    room_id      text        NOT NULL,
    gateway_id   text        NOT NULL,
    external_ref text        NOT NULL CHECK (external_ref <> ''),
    name         text        NOT NULL,
    type         text        NOT NULL CHECK (type IN (
                     'light', 'curtain', 'air_conditioner', 'tv', 'fan', 'plug', 'sensor',
                     'lock', 'camera', 'energy_meter', 'water_heater')),
    manufacturer text        NOT NULL,
    model        text        NOT NULL,
    capabilities jsonb       NOT NULL CHECK (jsonb_typeof(capabilities) = 'array' AND jsonb_array_length(capabilities) > 0),
    removed_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, room_id),
    FOREIGN KEY (home_id, room_id)    REFERENCES rooms (home_id, id)               ON DELETE RESTRICT,
    FOREIGN KEY (gateway_id, home_id) REFERENCES gateway_connections (id, home_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX devices_gateway_entity_uq ON devices (gateway_id, external_ref) WHERE removed_at IS NULL;
CREATE INDEX devices_home_active_idx ON devices (home_id) WHERE removed_at IS NULL;

CREATE TABLE device_placements (
    device_id      text    PRIMARY KEY,
    room_id        text    NOT NULL,
    pos_x double precision    NOT NULL,
    pos_y double precision    NOT NULL,
    pos_z double precision    NOT NULL,
    rotation_y_deg double precision    NOT NULL DEFAULT 0,
    version        integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    FOREIGN KEY (device_id, room_id) REFERENCES devices (id, room_id) ON DELETE CASCADE
);
