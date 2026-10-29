#!/usr/bin/env bash
# Makes changes in the shop database, so you can watch Vectezium capture them.
#
#   ./scripts/changes.sh insert [n]     insert n new customers (default 1)
#   ./scripts/changes.sh update [n]     change the loyalty tier and email of n random customers
#   ./scripts/changes.sh delete [n]     delete n random customers who have no orders
#   ./scripts/changes.sh order          place an order: one transaction across three tables
#   ./scripts/changes.sh stream [secs]  a random mix of all of the above, for secs seconds (default 60)
#   ./scripts/changes.sh sql "<SQL>"    run any statement as the application user
source "$(dirname "$0")/lib.sh"
require_running

FIRST=(Amara Bruno Carmen Dmitri Esme Farah Gus Hana Ivo Jade Kofi Lucia Mateo Nia Omar Pia)
LAST=(Adeyemi Berg Costa Dubois Eriksen Fischer Garcia Horvat Ito Jensen Kaur Lopez Murphy Nakamura)
COUNTRIES=(GB IE FR DE NL ES PT IT)
pick() { local list=("$@"); echo "${list[RANDOM % $#]}"; }

insert() {
  local n=${1:-1} values=() i name
  for ((i = 0; i < n; i++)); do
    name="$(pick "${FIRST[@]}") $(pick "${LAST[@]}")"
    values+=("('$name', '$(tr 'A-Z ' 'a-z.' <<<"$name").$RANDOM$RANDOM@example.com', '$(pick "${COUNTRIES[@]}")', SHA2(UUID(), 256))")
  done
  local IFS=,
  sql <<SQL
INSERT INTO customers (name, email, country, password_hash) VALUES ${values[*]};
SQL
}

update() {
  sql <<SQL
UPDATE customers
SET loyalty_tier = ELT(1 + FLOOR(RAND() * 3), 'bronze', 'silver', 'gold'),
    email = CONCAT(SUBSTRING_INDEX(email, '@', 1), '@example.org')
ORDER BY RAND() LIMIT ${1:-1};
SQL
}

delete() {
  sql <<SQL
DELETE FROM customers
WHERE id NOT IN (SELECT customer_id FROM orders)
ORDER BY RAND() LIMIT ${1:-1};
SQL
}

order() {
  local customer=$((1 + RANDOM % 500)) lines=$((1 + RANDOM % 3)) items="" i
  for ((i = 1; i <= lines; i++)); do
    items+="INSERT INTO order_items (order_id, line_no, product_id, qty, unit_price)
  SELECT @order, $i, id, $((1 + RANDOM % 3)), price FROM products WHERE id = $((1 + RANDOM % 120));
"
  done
  sql <<SQL
START TRANSACTION;
INSERT INTO orders (customer_id) VALUES ($customer);
SET @order = LAST_INSERT_ID();
${items}UPDATE products p JOIN order_items i ON i.product_id = p.id AND i.order_id = @order
  SET p.stock = p.stock - i.qty;
UPDATE orders SET total = (SELECT SUM(qty * unit_price) FROM order_items WHERE order_id = @order)
  WHERE id = @order;
COMMIT;
SQL
}

stream() {
  local until=$((SECONDS + ${1:-60}))
  say "A random mix of changes for ${1:-60} seconds (Ctrl-C to stop)"
  while ((SECONDS < until)); do
    case $((RANDOM % 10)) in
      0 | 1 | 2) insert $((1 + RANDOM % 3)) ;;
      3 | 4 | 5) update $((1 + RANDOM % 4)) ;;
      6) delete 1 ;;
      *) order ;;
    esac
    sleep 0.$((3 + RANDOM % 7))
  done
}

command=${1:-}
shift || true
case $command in
  insert | update | delete) "$command" "${1:-1}" ;;
  order) order ;;
  stream) stream "${1:-60}" ;;
  sql) sql <<<"${1:?usage: changes.sh sql \"<SQL>\"}" ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
