-- SQLite. transactions is the original dataset plus documented classifications.
-- Missing customer IDs stay NULL. Amounts are GBP, at source line level.
CREATE TABLE orders AS
SELECT InvoiceNo AS pedido, date(min(InvoiceDate)) AS fecha,
       CASE WHEN count(CustomerID) = count(*) THEN min(CustomerID) END AS cliente,
       sum(amount) AS importe
FROM transactions WHERE classification = 'sale'
GROUP BY InvoiceNo;

CREATE TABLE monthly AS
SELECT substr(fecha, 1, 7) || '-01' AS month,
       sum(importe) AS gross_sales, count(*) AS invoices,
       avg(importe) AS average_invoice
FROM orders GROUP BY 1;

CREATE TABLE customers AS
SELECT CustomerID AS customer, sum(amount) AS gross_sales,
       count(DISTINCT InvoiceNo) AS invoices
FROM transactions
WHERE classification = 'sale' AND CustomerID IS NOT NULL
GROUP BY CustomerID;
