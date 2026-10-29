#!/usr/bin/env bash
# Simulates shop traffic: a steady mix of inserts and updates across all the captured tables,
# so you can watch Vectezium capture a realistic stream.
#
#   ./scripts/simulate.sh [seconds] [ops-per-second]    defaults: 60 seconds, 5 operations a second
#   ./scripts/simulate.sh 0 20                          until Ctrl-C, 20 operations a second
#
# Each operation is one transaction:
#   inserts  a customer, a product, an order with 1-3 lines, a line added to an open order,
#            a review (if shop.reviews exists: ./scripts/add-table.sh)
#   updates  a customer's tier and email, a product's price or stock, an order moving from
#            placed to paid to shipped, an order line's quantity (with the order total)
# Every second's operations run in one batch; a line per second shows what was sent.
source "$(dirname "$0")/lib.sh"
require_running

SECS=${1:-60}
RATE=${2:-5}
if ! [[ $SECS =~ ^[0-9]+$ && $RATE =~ ^[1-9][0-9]*$ ]]; then
  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

FIRST=(Amara Bruno Carmen Dmitri Esme Farah Gus Hana Ivo Jade Kofi Lucia Mateo Nia Omar Pia)
LAST=(Adeyemi Berg Costa Dubois Eriksen Fischer Garcia Horvat Ito Jensen Kaur Lopez Murphy Nakamura)
COUNTRIES=(GB IE FR DE NL ES PT IT)
PRODUCTS=(Lamp Kettle Backpack Notebook Mug Blanket Speaker Planter Clock Scarf)
COLOURS=(black navy sage rust white)
TITLES=("Love it" "Does the job" "Not for me" "Great value" "Arrived late" "Would buy again")
pick() { local list=("$@"); echo "${list[RANDOM % $#]}"; }
# A random id from 1 to $1 (RANDOM alone stops at 32767).
rnd() { echo $((1 + (RANDOM * 32768 + RANDOM) % $1)); }

# Upper bounds for picking existing rows; ids that were deleted simply match nothing.
read -r MAX_CUSTOMER MAX_PRODUCT MAX_ORDER HAS_REVIEWS < <(query "
  SELECT (SELECT MAX(id) FROM customers), (SELECT MAX(id) FROM products), (SELECT MAX(id) FROM orders),
         (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'shop' AND table_name = 'reviews')")

# Sets @o to an open (placed) order, preferring one at or after a random id.
open_order() {
  echo "SET @o = COALESCE(
  (SELECT id FROM orders WHERE status = 'placed' AND id >= $(rnd "$MAX_ORDER") ORDER BY id LIMIT 1),
  (SELECT MIN(id) FROM orders WHERE status = 'placed'));"
}
TOTAL="UPDATE orders SET total = (SELECT COALESCE(SUM(qty * unit_price), 0) FROM order_items WHERE order_id = @o) WHERE id = @o;"

# ------------------------------------------------------------------------------------- inserts

insert_customer() {
  local name; name="$(pick "${FIRST[@]}") $(pick "${LAST[@]}")"
  echo "INSERT INTO customers (name, email, country, password_hash)
  VALUES ('$name', '$(tr 'A-Z ' 'a-z.' <<<"$name").$RANDOM$RANDOM@example.com', '$(pick "${COUNTRIES[@]}")', SHA2(UUID(), 256));"
}

insert_product() {
  echo "INSERT INTO products (sku, name, price, stock, attributes)
  VALUES (CONCAT('SIM-', UNIX_TIMESTAMP(), '-', $RANDOM), '$(pick "${COLOURS[@]}") $(pick "${PRODUCTS[@]}")',
          $((5 + RANDOM % 195)).$((RANDOM % 100)), $((10 + RANDOM % 200)),
          JSON_OBJECT('colour', '$(pick "${COLOURS[@]}")', 'weight_g', $((100 + RANDOM % 2000))));"
}

insert_order() {
  local i lines=$((1 + RANDOM % 3)) sql="START TRANSACTION;
INSERT INTO orders (customer_id) SELECT id FROM customers WHERE id >= $(rnd "$MAX_CUSTOMER") ORDER BY id LIMIT 1;
SET @o = LAST_INSERT_ID();"
  for ((i = 1; i <= lines; i++)); do
    sql+="
INSERT INTO order_items (order_id, line_no, product_id, qty, unit_price)
  SELECT @o, $i, id, $((1 + RANDOM % 3)), price FROM products WHERE id >= $(rnd "$MAX_PRODUCT") ORDER BY id LIMIT 1;"
  done
  echo "$sql
UPDATE products p JOIN order_items i ON i.product_id = p.id AND i.order_id = @o SET p.stock = p.stock - i.qty;
$TOTAL
COMMIT;"
}

add_order_line() {
  echo "START TRANSACTION;
$(open_order)
INSERT INTO order_items (order_id, line_no, product_id, qty, unit_price)
  SELECT @o, (SELECT COALESCE(MAX(line_no), 0) + 1 FROM order_items WHERE order_id = @o),
         id, $((1 + RANDOM % 3)), price
  FROM products WHERE id >= $(rnd "$MAX_PRODUCT") AND @o IS NOT NULL ORDER BY id LIMIT 1;
$TOTAL
COMMIT;"
}

insert_review() {
  echo "INSERT INTO reviews (product_id, customer_id, rating, title)
  VALUES ($(rnd "$MAX_PRODUCT"), $(rnd "$MAX_CUSTOMER"), $((1 + RANDOM % 5)), '$(pick "${TITLES[@]}")');"
}

# ------------------------------------------------------------------------------------- updates

update_customer() {
  echo "UPDATE customers
  SET loyalty_tier = ELT(1 + FLOOR(RAND() * 3), 'bronze', 'silver', 'gold'),
      email = CONCAT(SUBSTRING_INDEX(email, '@', 1), '@', ELT(1 + FLOOR(RAND() * 2), 'example.com', 'example.org'))
  WHERE id = $(rnd "$MAX_CUSTOMER");"
}

update_product() {
  if ((RANDOM % 2)); then
    echo "UPDATE products SET price = ROUND(price * (0.95 + RAND() * 0.1), 2) WHERE id = $(rnd "$MAX_PRODUCT");"
  else
    echo "UPDATE products SET stock = stock + $((5 + RANDOM % 50)) WHERE id = $(rnd "$MAX_PRODUCT");"
  fi
}

advance_order() {
  echo "SET @o = COALESCE(
  (SELECT id FROM orders WHERE status IN ('placed', 'paid') AND id >= $(rnd "$MAX_ORDER") ORDER BY id LIMIT 1),
  (SELECT MIN(id) FROM orders WHERE status IN ('placed', 'paid')));
UPDATE orders SET status = IF(status = 'placed', 'paid', 'shipped') WHERE id = @o;"
}

change_line_qty() {
  echo "START TRANSACTION;
$(open_order)
UPDATE order_items SET qty = qty + 1 WHERE order_id = @o AND line_no = 1;
$TOTAL
COMMIT;"
}

# ------------------------------------------------------------------------------------- the loop

# Operation, weight. Roughly 45% inserts and 55% updates.
OPS=(insert_customer:10 insert_product:4 insert_order:12 add_order_line:7
     update_customer:16 update_product:15 advance_order:14 change_line_qty:10)
((HAS_REVIEWS)) && OPS+=(insert_review:6)
WEIGHTS=0
for op in "${OPS[@]}"; do WEIGHTS=$((WEIGHTS + ${op#*:})); done

choose() {
  local r=$((RANDOM % WEIGHTS)) op
  for op in "${OPS[@]}"; do
    r=$((r - ${op#*:}))
    if ((r < 0)); then echo "${op%:*}"; return; fi
  done
}

declare -A TOTALS=()
summary() {
  echo
  say "Sent $SENT operations in $((SECONDS - STARTED)) s"
  for op in "${!TOTALS[@]}"; do printf '  %-16s %6d\n' "$op" "${TOTALS[$op]}"; done | sort
  exit 0
}
trap summary INT

say "Simulating shop traffic: $RATE operations a second, $([ "$SECS" -eq 0 ] && echo "until Ctrl-C" || echo "for $SECS s")"
echo "tables: customers, products, orders, order_items$( ((HAS_REVIEWS)) && echo ", reviews")"
SENT=0
STARTED=$SECONDS
while ((SECS == 0 || SECONDS - STARTED < SECS)); do
  tick=$SECONDS batch="" line=""
  declare -A counts=()
  for ((i = 0; i < RATE; i++)); do
    op=$(choose)
    batch+="$($op)"$'\n'
    counts[$op]=$((${counts[$op]:-0} + 1))
    TOTALS[$op]=$((${TOTALS[$op]:-0} + 1))
  done
  # --force: a statement that fails (say, a duplicate email) does not stop the rest of the batch.
  errors=$(mysql_app --force <<<"$batch" 2>&1 >/dev/null | grep '^ERROR' || true)
  SENT=$((SENT + RATE))
  for op in "${!counts[@]}"; do line+=" ${op}=${counts[$op]}"; done
  printf '%s %5d sent |%s\n' "$(date +%T)" "$SENT" "$line"
  [ -n "$errors" ] && printf '\033[2m  %s\033[0m\n' "$(head -1 <<<"$errors")"
  unset counts
  # Pace to about one batch a second (a slow batch just runs late).
  while ((SECONDS == tick)); do sleep 0.1; done
done
summary
