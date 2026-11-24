-- Destroys users, memberships and sessions. 0001 and 0002 are untouched.
DROP TABLE auth_events;
DROP TABLE login_attempts;
DROP TABLE sessions;
DROP TABLE memberships;
DROP TABLE users;
