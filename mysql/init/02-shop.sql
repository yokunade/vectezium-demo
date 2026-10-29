-- The demo database: a small shop. Runs once, when the MySQL volume is first created.
-- The rows are generated deterministically, so every fresh start has the same data.
SET NAMES utf8mb4;
SET SESSION cte_max_recursion_depth = 10000;

CREATE DATABASE shop;
USE shop;

CREATE TABLE customers (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  name          VARCHAR(100) NOT NULL,
  email         VARCHAR(200) NOT NULL UNIQUE,
  country       CHAR(2) NOT NULL,
  loyalty_tier  ENUM('bronze', 'silver', 'gold') NOT NULL DEFAULT 'bronze',
  password_hash CHAR(64) NOT NULL,              -- never leaves MySQL: column_exclude_list
  created_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at    DATETIME(3) NULL ON UPDATE CURRENT_TIMESTAMP(3)
);

CREATE TABLE products (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  sku        VARCHAR(20) NOT NULL UNIQUE,
  name       VARCHAR(100) NOT NULL,
  price      DECIMAL(10, 2) NOT NULL,
  stock      INT NOT NULL,
  attributes JSON
);

CREATE TABLE orders (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  customer_id INT NOT NULL,
  status      ENUM('placed', 'paid', 'shipped', 'cancelled') NOT NULL DEFAULT 'placed',
  total       DECIMAL(12, 2) NOT NULL DEFAULT 0,
  placed_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  FOREIGN KEY (customer_id) REFERENCES customers (id)
);

-- A composite primary key: change events carry both columns in metadata.key.
CREATE TABLE order_items (
  order_id   BIGINT NOT NULL,
  line_no    SMALLINT NOT NULL,
  product_id INT NOT NULL,
  qty        INT NOT NULL,
  unit_price DECIMAL(10, 2) NOT NULL,
  PRIMARY KEY (order_id, line_no),
  FOREIGN KEY (order_id) REFERENCES orders (id),
  FOREIGN KEY (product_id) REFERENCES products (id)
);

-- 600 customers. The last 100 have no orders, so the delete script has something to delete.
INSERT INTO customers (id, name, email, country, loyalty_tier, password_hash, created_at)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 600)
SELECT i,
       CONCAT(ELT(1 + i % 12, 'Ada', 'Ben', 'Chloe', 'Dev', 'Elif', 'Femi', 'Grace', 'Hiro',
                  'Ines', 'Jonas', 'Kai', 'Lena'), ' ',
              ELT(1 + (i * 7) % 10, 'Okafor', 'Silva', 'Novak', 'Patel', 'Kowalski', 'Haddad',
                  'Lindqvist', 'Tanaka', 'Moreau', 'Byrne')),
       CONCAT('customer', i, '@example.com'),
       ELT(1 + (i * 3) % 6, 'GB', 'IE', 'FR', 'DE', 'NL', 'ES'),
       ELT(1 + (i * 5) % 7 DIV 3, 'bronze', 'silver', 'gold'),
       SHA2(CONCAT('secret-', i), 256),
       TIMESTAMP('2025-01-01') + INTERVAL i * 637 MINUTE
FROM n;

-- 120 products.
INSERT INTO products (id, sku, name, price, stock, attributes)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 120)
SELECT i,
       CONCAT('SKU-', LPAD(i, 5, '0')),
       CONCAT(ELT(1 + i % 6, 'Classic', 'Trail', 'Studio', 'Pocket', 'Everyday', 'Pro'), ' ',
              ELT(1 + (i * 7) % 8, 'Mug', 'Backpack', 'Lamp', 'Notebook', 'Kettle', 'Scarf',
                  'Speaker', 'Bottle')),
       ROUND(4.99 + (i * 37) % 200 + ((i * 13) % 100) / 100, 2),
       20 + (i * 11) % 180,
       JSON_OBJECT('colour', ELT(1 + i % 5, 'black', 'white', 'sage', 'navy', 'rust'),
                   'weight_g', 100 + (i * 29) % 1900)
FROM n;

-- 2,500 orders from the first 500 customers.
INSERT INTO orders (id, customer_id, status, placed_at)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 2500)
SELECT i,
       1 + (i * 17) % 500,
       ELT(1 + (i * 3) % 10, 'shipped', 'shipped', 'shipped', 'shipped', 'paid', 'paid', 'placed',
           'placed', 'cancelled', 'shipped'),
       TIMESTAMP('2025-06-01') + INTERVAL i * 157 MINUTE
FROM n;

-- One to three lines per order.
INSERT INTO order_items (order_id, line_no, product_id, qty, unit_price)
SELECT o.id, l.line_no, p.id, 1 + (o.id + l.line_no) % 3, p.price
FROM orders o
JOIN (SELECT 1 AS line_no UNION ALL SELECT 2 UNION ALL SELECT 3) l ON l.line_no <= 1 + o.id % 3
JOIN products p ON p.id = 1 + (o.id * 7 + l.line_no * 31) % 120;

UPDATE orders o
JOIN (SELECT order_id, SUM(qty * unit_price) AS total FROM order_items GROUP BY order_id) t
  ON t.order_id = o.id
SET o.total = t.total;
