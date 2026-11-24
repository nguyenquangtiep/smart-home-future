-- Destroys all home configuration. 0001 (commands, idempotency, audit) is untouched.
DROP TABLE device_placements;
DROP TABLE devices;
DROP TABLE gateway_connections;
DROP TABLE rooms;
DROP TABLE floors;
DROP TABLE homes;
