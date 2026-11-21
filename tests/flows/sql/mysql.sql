WITH RECURSIVE s (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 5)
SELECT n AS id, CONCAT('item ', n) AS name, n * 1.5 AS price, DATE '2026-01-01' + INTERVAL n DAY AS day,
  CAST(n % 2 = 0 AS UNSIGNED) AS even
FROM s
WHERE n >= :min
ORDER BY n
