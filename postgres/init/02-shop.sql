-- The demo database for postgres_cdc: the same small shop as MySQL's, in schema `public` of
-- database `shop`. Runs once, when the PostgreSQL volume is first created.
SET ROLE app;

CREATE TYPE loyalty_tier AS ENUM ('bronze', 'silver', 'gold');

CREATE TABLE customers (
  id            serial PRIMARY KEY,
  name          varchar(100) NOT NULL,
  email         varchar(200) NOT NULL UNIQUE,
  country       char(2) NOT NULL,
  loyalty_tier  loyalty_tier NOT NULL DEFAULT 'bronze',
  password_hash char(64) NOT NULL,               -- never leaves PostgreSQL: column_exclude_list
  tags          text[],
  created_at    timestamptz(3) NOT NULL DEFAULT now(),
  updated_at    timestamptz(3)
);

CREATE TABLE products (
  id         serial PRIMARY KEY,
  sku        varchar(20) NOT NULL UNIQUE,
  name       varchar(100) NOT NULL,
  price      numeric(10, 2) NOT NULL,
  stock      int NOT NULL,
  attributes jsonb
);

CREATE TABLE orders (
  id          bigserial PRIMARY KEY,
  customer_id int NOT NULL REFERENCES customers (id),
  status      text NOT NULL DEFAULT 'placed',
  total       numeric(12, 2) NOT NULL DEFAULT 0,
  placed_at   timestamp(3) NOT NULL DEFAULT now()
);

-- Full before images for updates and deletes (the default identity sends only the key).
ALTER TABLE customers REPLICA IDENTITY FULL;
ALTER TABLE products REPLICA IDENTITY FULL;
ALTER TABLE orders REPLICA IDENTITY FULL;

-- 600 customers, 120 products, 1,500 orders, generated deterministically.
INSERT INTO customers (name, email, country, loyalty_tier, password_hash, tags, created_at)
SELECT (ARRAY['Ada','Ben','Chloe','Dev','Elif','Femi','Grace','Hiro','Ines','Jonas','Kai','Lena'])[1 + i % 12]
         || ' ' || (ARRAY['Okafor','Silva','Novak','Patel','Kowalski','Haddad','Lindqvist','Tanaka','Moreau','Byrne'])[1 + (i * 7) % 10],
       'customer' || i || '@example.com',
       (ARRAY['GB','IE','FR','DE','NL','ES'])[1 + (i * 3) % 6],
       (ARRAY['bronze','silver','gold'])[1 + (i * 5) % 7 / 3]::loyalty_tier,
       encode(sha256(('secret-' || i)::bytea), 'hex'),
       ARRAY['newsletter', 'region-' || (i % 4)],
       timestamptz '2025-01-01 00:00:00+00' + i * interval '637 minutes'
FROM generate_series(1, 600) i;

INSERT INTO products (sku, name, price, stock, attributes)
SELECT 'SKU-' || lpad(i::text, 5, '0'),
       (ARRAY['Classic','Trail','Studio','Pocket','Everyday','Pro'])[1 + i % 6] || ' '
         || (ARRAY['Mug','Bottle','Backpack','Notebook','Lamp','Scarf','Cap','Tote'])[1 + i % 8],
       round((5 + (i * 37) % 200 + (i % 100) / 100.0)::numeric, 2),
       10 + (i * 13) % 300,
       jsonb_build_object('colour', (ARRAY['red','blue','green','black'])[1 + i % 4], 'weight_g', 100 + i * 3)
FROM generate_series(1, 120) i;

INSERT INTO orders (customer_id, status, total, placed_at)
SELECT 1 + (i * 7) % 500,
       (ARRAY['placed','paid','shipped','cancelled'])[1 + i % 4],
       round((10 + (i * 53) % 400 + (i % 100) / 100.0)::numeric, 2),
       timestamp '2025-02-01' + i * interval '173 minutes'
FROM generate_series(1, 1500) i;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO vectezium;
RESET ROLE;

-- The publication the source streams (created by the DBA: it needs CREATE on the database, and
-- superuser for a whole schema). It covers every table in the schema, now and later; the source's
-- table_include_list decides which are captured, as for MySQL.
CREATE PUBLICATION vectezium FOR TABLES IN SCHEMA public;
