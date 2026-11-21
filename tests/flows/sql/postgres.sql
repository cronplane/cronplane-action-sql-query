SELECT n AS id, 'item ' || n AS name, n * 1.5 AS price, DATE '2026-01-01' + n AS day, n % 2 = 0 AS even
FROM generate_series(1, 5) AS n
WHERE n >= :min
ORDER BY n
