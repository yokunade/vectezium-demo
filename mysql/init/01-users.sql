-- Runs once, when the MySQL volume is first created.

-- Vectezium's CDC user: the privileges the mysql_cdc source needs, nothing else.
CREATE USER 'vectezium'@'%' IDENTIFIED BY 'vectezium-demo-pw';
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'vectezium'@'%';

-- The application user the demo scripts and the UI use.
CREATE USER 'app'@'%' IDENTIFIED BY 'app-demo-pw';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES ON shop.* TO 'app'@'%';
